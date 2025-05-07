use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use actix_web::http::{header, StatusCode};
use actix_web::rt::spawn;
use futures::Stream;
use serde::{Deserialize, Serialize};
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt};
use tokio::net::TcpStream;
use tokio::sync::oneshot;
use tokio_stream::StreamExt;

#[derive(Clone)]
struct AppState {
    camera_host: String,
    camera_rtsp_port: u16,
    camera_user: String,
    camera_pass: String,
    camera_http_port: u16,
    server_host: String,
    server_port: u16,
    current_format: Arc<Mutex<String>>,
    streaming: Arc<Mutex<bool>>,
    stream_stop_tx: Arc<Mutex<Option<oneshot::Sender<()>>>>,
}

#[derive(Deserialize)]
struct StartStreamBody {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    status: String,
    format: String,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
}

/// Utility to build RTSP URL
fn build_rtsp_url(state: &AppState) -> String {
    format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
        state.camera_user, state.camera_pass, state.camera_host, state.camera_rtsp_port
    )
}

/// Utility to build MJPEG HTTP URL
fn build_mjpeg_url(state: &AppState) -> String {
    format!(
        "http://{}:{}@{}:{}/Streaming/channels/101/httpPreview",
        state.camera_user, state.camera_pass, state.camera_host, state.camera_http_port
    )
}

/// POST /stream/start
async fn stream_start(
    state: web::Data<AppState>,
    body: web::Json<StartStreamBody>,
) -> impl Responder {
    let format = body
        .format
        .clone()
        .unwrap_or_else(|| "mjpeg".to_string())
        .to_lowercase();

    if format != "h264" && format != "mjpeg" {
        return HttpResponse::BadRequest().json(
            serde_json::json!({"error": "Unsupported format, use 'h264' or 'mjpeg'"}),
        );
    }

    let mut current_format = state.current_format.lock().unwrap();
    *current_format = format.clone();

    let mut streaming = state.streaming.lock().unwrap();
    *streaming = true;

    HttpResponse::Ok().json(StartStreamResponse {
        status: "started".to_string(),
        format,
    })
}

/// POST /stream/stop
async fn stream_stop(state: web::Data<AppState>) -> impl Responder {
    // Notify the stream handler to stop streaming
    if let Some(tx) = state.stream_stop_tx.lock().unwrap().take() {
        let _ = tx.send(());
    }
    let mut streaming = state.streaming.lock().unwrap();
    *streaming = false;
    HttpResponse::Ok().json(serde_json::json!({"status": "stopped"}))
}

/// GET /stream/url
async fn stream_url(state: web::Data<AppState>) -> impl Responder {
    let current_format = state.current_format.lock().unwrap().clone();
    let url = if current_format == "h264" {
        build_rtsp_url(&state)
    } else {
        build_mjpeg_url(&state)
    };
    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: current_format,
    })
}

/// GET /stream/live
async fn stream_live(
    req: HttpRequest,
    state: web::Data<AppState>,
) -> impl Responder {
    let format = state.current_format.lock().unwrap().clone();
    let streaming = *state.streaming.lock().unwrap();

    if !streaming {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream not started. Please call /stream/start first.");
    }

    // Setup channel to stop streaming when needed
    let (stop_tx, stop_rx) = oneshot::channel();
    {
        let mut tx_guard = state.stream_stop_tx.lock().unwrap();
        *tx_guard = Some(stop_tx);
    }

    if format == "mjpeg" {
        let url = build_mjpeg_url(&state);
        // Proxy MJPEG HTTP stream
        let client = reqwest::Client::new();
        // Use a stream response
        let resp_fut = async move {
            let mut resp = match client.get(&url).send().await {
                Ok(r) => r,
                Err(e) => return HttpResponse::InternalServerError().body(format!("Camera connection failed: {}", e)),
            };
            let content_type = resp
                .headers()
                .get(header::CONTENT_TYPE)
                .cloned()
                .unwrap_or(header::HeaderValue::from_static("multipart/x-mixed-replace;boundary=ffserver"));
            // Stream the body
            let stream = async_stream::stream! {
                let mut body = resp.bytes_stream();
                let mut stop_rx = stop_rx;
                let mut stop_rx = Box::pin(stop_rx);
                loop {
                    let next_chunk = futures::future::select(body.next(), &mut stop_rx).await;
                    match next_chunk {
                        futures::future::Either::Left((Some(Ok(chunk)), _)) => {
                            yield Ok::<_, actix_web::Error>(chunk);
                        },
                        futures::future::Either::Left((Some(Err(e)), _)) => {
                            break;
                        },
                        futures::future::Either::Left((None, _)) => {
                            break;
                        },
                        futures::future::Either::Right((_, _)) => {
                            // Stop signal received
                            break;
                        }
                    }
                }
            };
            HttpResponse::Ok()
                .content_type(content_type)
                .streaming(stream)
        };
        return resp_fut.await;
    } else if format == "h264" {
        // Use RTSP-over-TCP and frame raw H264 into an HTTP stream (video/mp4)
        // For browsers, H.264 over MP4 fragment streaming is required, but here we just proxy RTP packets as octet-stream.
        // Note: Browsers may not play this directly; external players like VLC will work.
        let rtsp_url = build_rtsp_url(&state);
        let parsed = url::Url::parse(&rtsp_url).unwrap();
        let host = parsed.host_str().unwrap();
        let port = parsed.port().unwrap_or(554);

        // Connect to RTSP and play stream (very basic RTSP client)
        let user = parsed.username();
        let pass = parsed.password().unwrap_or("");
        let path = parsed.path();

        let client_fut = async move {
            // Connect to TCP
            let mut stream = match TcpStream::connect((host, port)).await {
                Ok(s) => s,
                Err(e) => return HttpResponse::InternalServerError().body(format!("RTSP connect failed: {}", e)),
            };
            let mut cseq = 1u32;

            // Helper for RTSP requests
            async fn send_rtsp(
                stream: &mut TcpStream,
                req: &str,
            ) -> Result<String, std::io::Error> {
                stream.write_all(req.as_bytes()).await?;
                let mut buf = vec![0u8; 4096];
                let n = stream.read(&mut buf).await?;
                Ok(String::from_utf8_lossy(&buf[..n]).to_string())
            }

            // Send OPTIONS
            let req_opts = format!(
                "OPTIONS {} RTSP/1.0\r\nCSeq: {}\r\nUser-Agent: RustRTSP\r\nAuthorization: Basic {}\r\n\r\n",
                path,
                cseq,
                base64::encode(format!("{}:{}", user, pass))
            );
            let _ = send_rtsp(&mut stream, &req_opts).await;
            cseq += 1;

            // Send DESCRIBE
            let req_desc = format!(
                "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\nAccept: application/sdp\r\nUser-Agent: RustRTSP\r\nAuthorization: Basic {}\r\n\r\n",
                path,
                cseq,
                base64::encode(format!("{}:{}", user, pass))
            );
            let _ = send_rtsp(&mut stream, &req_desc).await;
            cseq += 1;

            // Send SETUP (interleaved)
            let req_setup = format!(
                "SETUP {}/trackID=1 RTSP/1.0\r\nCSeq: {}\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nUser-Agent: RustRTSP\r\nAuthorization: Basic {}\r\n\r\n",
                path,
                cseq,
                base64::encode(format!("{}:{}", user, pass))
            );
            let setup_resp = send_rtsp(&mut stream, &req_setup).await.unwrap_or_default();
            cseq += 1;

            // Find Session header
            let session_line = setup_resp
                .lines()
                .find(|l| l.starts_with("Session:"))
                .unwrap_or("Session: 123456");
            let session_id = session_line
                .split(':')
                .nth(1)
                .map(|s| s.trim())
                .unwrap_or("123456")
                .split(';')
                .next()
                .unwrap();

            // Send PLAY
            let req_play = format!(
                "PLAY {} RTSP/1.0\r\nCSeq: {}\r\nSession: {}\r\nUser-Agent: RustRTSP\r\nAuthorization: Basic {}\r\n\r\n",
                path,
                cseq,
                session_id,
                base64::encode(format!("{}:{}", user, pass))
            );
            let _ = send_rtsp(&mut stream, &req_play).await;
            // Now, the stream contains RTP packets interleaved in TCP

            // Streaming RTP-over-TCP packets as octet-stream
            let (mut rd, mut wr) = stream.into_split();
            let mut stop_rx = stop_rx;
            let s = async_stream::stream! {
                let mut buf = vec![0u8; 2048];
                loop {
                    tokio::select! {
                        n = rd.read(&mut buf) => {
                            if let Ok(n) = n {
                                if n == 0 { break; }
                                yield Ok::<_, actix_web::Error>(web::Bytes::copy_from_slice(&buf[..n]));
                            } else {
                                break;
                            }
                        }
                        _ = &mut stop_rx => {
                            break;
                        }
                    }
                }
            };
            HttpResponse::Ok()
                .content_type("application/octet-stream")
                .insert_header(("Access-Control-Allow-Origin", "*"))
                .streaming(s)
        };
        return client_fut.await;
    } else {
        HttpResponse::BadRequest().body("Unsupported format")
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    // Read env variables for all config
    let camera_host = env::var("CAMERA_HOST").expect("CAMERA_HOST required");
    let camera_rtsp_port = env::var("CAMERA_RTSP_PORT")
        .unwrap_or_else(|_| "554".to_string())
        .parse::<u16>()
        .expect("Invalid CAMERA_RTSP_PORT");
    let camera_user = env::var("CAMERA_USER").unwrap_or_else(|_| "admin".to_string());
    let camera_pass = env::var("CAMERA_PASS").unwrap_or_else(|_| "12345".to_string());
    let camera_http_port = env::var("CAMERA_HTTP_PORT")
        .unwrap_or_else(|_| "80".to_string())
        .parse::<u16>()
        .expect("Invalid CAMERA_HTTP_PORT");

    let server_host = env::var("SERVER_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let server_port = env::var("SERVER_PORT")
        .unwrap_or_else(|_| "8080".to_string())
        .parse::<u16>()
        .expect("Invalid SERVER_PORT");

    let state = web::Data::new(AppState {
        camera_host,
        camera_rtsp_port,
        camera_user,
        camera_pass,
        camera_http_port,
        server_host: server_host.clone(),
        server_port,
        current_format: Arc::new(Mutex::new("mjpeg".to_string())),
        streaming: Arc::new(Mutex::new(false)),
        stream_stop_tx: Arc::new(Mutex::new(None)),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .route("/stream/start", web::post().to(stream_start))
            .route("/stream/stop", web::post().to(stream_stop))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/live", web::get().to(stream_live))
    })
    .bind((server_host.as_str(), server_port))?
    .run()
    .await
}