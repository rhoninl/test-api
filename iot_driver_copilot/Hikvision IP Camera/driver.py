use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use actix_web::http::{header, StatusCode};
use bytes::BytesMut;
use futures::{Stream, StreamExt};
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::process::Command;
use tokio::sync::oneshot;
use url::Url;

#[derive(Clone)]
struct AppState {
    camera_ip: String,
    username: String,
    password: String,
    rtsp_port: u16,
    http_port: u16,
    stream_active: Arc<Mutex<bool>>,
    stream_stop_tx: Arc<Mutex<Option<oneshot::Sender<()>>>>,
    stream_format: Arc<Mutex<String>>,
}

async fn stream_start(
    state: web::Data<AppState>,
    req_body: String,
) -> impl Responder {
    let mut format = "mjpeg".to_string();
    if let Ok(json_body) = serde_json::from_str::<serde_json::Value>(&req_body) {
        if let Some(fmt) = json_body.get("format") {
            if fmt == "h264" {
                format = "h264".to_string();
            }
        }
    }

    let mut stream_active = state.stream_active.lock().unwrap();
    if *stream_active {
        return HttpResponse::Ok().json(serde_json::json!({
            "status": "already_streaming",
            "format": *state.stream_format.lock().unwrap()
        }));
    }

    *state.stream_format.lock().unwrap() = format.clone();
    *stream_active = true;
    HttpResponse::Ok().json(serde_json::json!({
        "status": "stream_started",
        "format": format
    }))
}

async fn stream_stop(state: web::Data<AppState>) -> impl Responder {
    let mut stream_active = state.stream_active.lock().unwrap();
    if !*stream_active {
        return HttpResponse::Ok().json(serde_json::json!({
            "status": "not_streaming"
        }));
    }
    if let Some(tx) = state.stream_stop_tx.lock().unwrap().take() {
        let _ = tx.send(());
    }
    *stream_active = false;
    HttpResponse::Ok().json(serde_json::json!({
        "status": "stream_stopped"
    }))
}

async fn stream_url(state: web::Data<AppState>) -> impl Responder {
    let url = format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
        state.username, state.password, state.camera_ip, state.rtsp_port
    );
    HttpResponse::Ok().json(serde_json::json!({
        "url": url
    }))
}

struct MJPEGStream {
    rtsp_url: String,
    stop_rx: Option<oneshot::Receiver<()>>,
}

impl Stream for MJPEGStream {
    type Item = Result<bytes::Bytes, actix_web::Error>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        // Not used, placeholder for trait
        Poll::Ready(None)
    }
}

async fn stream_mjpeg(
    state: web::Data<AppState>,
    _req: HttpRequest,
) -> impl Responder {
    let mut stream_active = state.stream_active.lock().unwrap();
    if !*stream_active {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream not started. Call /stream/start first.");
    }
    let format = state.stream_format.lock().unwrap().clone();
    if format != "mjpeg" {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream format is not MJPEG.");
    }
    let (tx, rx) = oneshot::channel();
    *state.stream_stop_tx.lock().unwrap() = Some(tx);

    let rtsp_url = format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
        state.username, state.password, state.camera_ip, state.rtsp_port
    );

    // Proxy RTSP MJPEG stream as HTTP MJPEG multipart
    let boundary = "mjpegstream";
    let (mut reader, mut writer) = tokio::io::duplex(1024 * 1024);

    // Spawn RTSP-MJPEG streaming task
    let rx_clone = rx;
    let rtsp_url_clone = rtsp_url.clone();

    tokio::spawn(async move {
        // Minimal RTSP-over-TCP client, expects MJPEG stream
        // Hikvision supports MJPEG over HTTP, so connect directly to HTTP MJPEG if available
        let mjpeg_url = format!(
            "http://{}:{}/Streaming/channels/102/httpPreview",
            state.camera_ip, state.http_port
        );
        if let Ok(mut stream) = TcpStream::connect(format!("{}:{}", state.camera_ip, state.http_port)).await {
            let request = format!(
                "GET /Streaming/channels/102/httpPreview HTTP/1.1\r\nHost: {}\r\nAuthorization: Basic {}\r\nConnection: close\r\n\r\n",
                state.camera_ip,
                base64::encode(format!("{}:{}", state.username, state.password))
            );
            let _ = stream.write_all(request.as_bytes()).await;
            let mut buffer = [0u8; 4096];
            let mut header_found = false;
            loop {
                tokio::select! {
                    n = stream.read(&mut buffer) => {
                        if let Ok(n) = n {
                            if n == 0 { break; }
                            // Find the headers and skip them
                            if !header_found {
                                if let Some(pos) = buffer[..n].windows(4).position(|w| w == b"\r\n\r\n") {
                                    header_found = true;
                                    let start = pos + 4;
                                    let _ = writer.write_all(&buffer[start..n]).await;
                                }
                            } else {
                                let _ = writer.write_all(&buffer[..n]).await;
                            }
                        } else {
                            break;
                        }
                    },
                    _ = rx_clone => {
                        break;
                    }
                }
            }
        }
    });

    let body = async_stream::stream! {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf).await {
                Ok(n) if n > 0 => {
                    yield Ok(bytes::Bytes::copy_from_slice(&buf[..n]));
                },
                _ => break,
            }
        }
    };

    HttpResponse::Ok()
        .content_type(format!("multipart/x-mixed-replace;boundary={}", boundary))
        .insert_header((header::CACHE_CONTROL, "no-cache"))
        .streaming(body)
}

async fn stream_h264(
    state: web::Data<AppState>,
    _req: HttpRequest,
) -> impl Responder {
    let mut stream_active = state.stream_active.lock().unwrap();
    if !*stream_active {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream not started. Call /stream/start first.");
    }
    let format = state.stream_format.lock().unwrap().clone();
    if format != "h264" {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream format is not h264.");
    }
    let (tx, rx) = oneshot::channel();
    *state.stream_stop_tx.lock().unwrap() = Some(tx);

    let rtsp_url = format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
        state.username, state.password, state.camera_ip, state.rtsp_port
    );

    // Proxy RTSP h264 stream as HTTP octet-stream
    // This is a raw proxy, browser may need MSE or similar to consume
    let (mut reader, mut writer) = tokio::io::duplex(1024 * 1024);
    let rx_clone = rx;
    let rtsp_url_clone = rtsp_url.clone();

    tokio::spawn(async move {
        // Minimal RTSP-over-TCP client, just pipes the RTP payload as raw stream
        if let Ok(mut stream) = TcpStream::connect(format!("{}:{}", state.camera_ip, state.rtsp_port)).await {
            // Write RTSP DESCRIBE/SETUP/PLAY
            let cseq = 1;
            let describe = format!(
                "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nAccept: application/sdp\r\n\r\n",
                rtsp_url_clone,
                cseq,
                base64::encode(format!("{}:{}", state.username, state.password))
            );
            let _ = stream.write_all(describe.as_bytes()).await;
            // For simplicity, just proxy all data received
            let mut buffer = [0u8; 4096];
            loop {
                tokio::select! {
                    n = stream.read(&mut buffer) => {
                        if let Ok(n) = n {
                            if n == 0 { break; }
                            let _ = writer.write_all(&buffer[..n]).await;
                        } else {
                            break;
                        }
                    },
                    _ = rx_clone => {
                        break;
                    }
                }
            }
        }
    });

    let body = async_stream::stream! {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf).await {
                Ok(n) if n > 0 => {
                    yield Ok(bytes::Bytes::copy_from_slice(&buf[..n]));
                },
                _ => break,
            }
        }
    };

    HttpResponse::Ok()
        .content_type("video/mp4")
        .insert_header((header::CACHE_CONTROL, "no-cache"))
        .streaming(body)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let camera_ip = env::var("CAMERA_IP").expect("CAMERA_IP not set");
    let username = env::var("CAMERA_USERNAME").unwrap_or_else(|_| "admin".to_string());
    let password = env::var("CAMERA_PASSWORD").unwrap_or_else(|_| "admin".to_string());
    let server_host = env::var("SERVER_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let server_port = env::var("SERVER_PORT")
        .unwrap_or_else(|_| "8080".to_string())
        .parse::<u16>()
        .expect("SERVER_PORT must be a number");
    let rtsp_port = env::var("RTSP_PORT")
        .unwrap_or_else(|_| "554".to_string())
        .parse::<u16>()
        .expect("RTSP_PORT must be a number");
    let http_port = env::var("HTTP_PORT")
        .unwrap_or_else(|_| "80".to_string())
        .parse::<u16>()
        .expect("HTTP_PORT must be a number");

    let state = web::Data::new(AppState {
        camera_ip,
        username,
        password,
        rtsp_port,
        http_port,
        stream_active: Arc::new(Mutex::new(false)),
        stream_stop_tx: Arc::new(Mutex::new(None)),
        stream_format: Arc::new(Mutex::new("mjpeg".to_string())),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .route("/stream/start", web::post().to(stream_start))
            .route("/stream/stop", web::post().to(stream_stop))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/mjpeg", web::get().to(stream_mjpeg))
            .route("/stream/h264", web::get().to(stream_h264))
    })
    .bind((server_host.as_str(), server_port))?
    .run()
    .await
}