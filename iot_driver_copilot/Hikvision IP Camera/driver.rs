use actix_web::{web, App, HttpResponse, HttpServer, Responder, post, get};
use bytes::Bytes;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use std::env;
use std::sync::{Arc, Mutex};
use tokio::net::TcpStream;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::broadcast;
use url::Url;

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StreamSession {
    status: String,
    format: String,
}

struct AppState {
    session_active: Mutex<bool>,
    stream_format: Mutex<String>,
    shutdown_tx: broadcast::Sender<()>,
}

fn get_env(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.into())
}

fn build_rtsp_url(ip: &str, user: &str, pass: &str, port: &str, format: &str) -> String {
    let path = match format {
        "h264" => "/Streaming/Channels/101",
        "mjpeg" => "/Streaming/Channels/102",
        _ => "/Streaming/Channels/101",
    };
    format!("rtsp://{}:{}@{}:{}{}", user, pass, ip, port, path)
}

fn build_mjpeg_url(ip: &str, user: &str, pass: &str, port: &str) -> String {
    format!(
        "http://{}:{}@{}:{}/Streaming/channels/102/httppreview",
        user, pass, ip, port
    )
}

#[post("/stream/start")]
async fn start_stream(
    state: web::Data<Arc<AppState>>,
    req: web::Json<StartStreamRequest>
) -> impl Responder {
    let mut active = state.session_active.lock().unwrap();
    if *active {
        HttpResponse::Ok().json(StreamSession {
            status: "already_active".into(),
            format: state.stream_format.lock().unwrap().clone(),
        })
    } else {
        let mut fmt = state.stream_format.lock().unwrap();
        let format = req.format.clone().unwrap_or_else(|| "h264".into());
        *fmt = format.clone();
        *active = true;
        HttpResponse::Ok().json(StreamSession {
            status: "started".into(),
            format,
        })
    }
}

#[post("/stream/stop")]
async fn stop_stream(state: web::Data<Arc<AppState>>) -> impl Responder {
    let mut active = state.session_active.lock().unwrap();
    if *active {
        let _ = state.shutdown_tx.send(());
        *active = false;
        HttpResponse::Ok().body("Stream stopped")
    } else {
        HttpResponse::Ok().body("No active stream to stop")
    }
}

#[get("/stream/url")]
async fn stream_url(state: web::Data<Arc<AppState>>) -> impl Responder {
    let ip = get_env("CAMERA_IP", "127.0.0.1");
    let user = get_env("CAMERA_USER", "admin");
    let pass = get_env("CAMERA_PASS", "12345");
    let rtsp_port = get_env("CAMERA_RTSP_PORT", "554");
    let http_port = get_env("CAMERA_HTTP_PORT", "80");
    let format = state.stream_format.lock().unwrap().clone();

    let url = if format == "mjpeg" {
        build_mjpeg_url(&ip, &user, &pass, &http_port)
    } else {
        build_rtsp_url(&ip, &user, &pass, &rtsp_port, &format)
    };
    HttpResponse::Ok().json(StreamUrlResponse { url })
}

#[get("/stream/live")]
async fn stream_live(
    state: web::Data<Arc<AppState>>,
    req: actix_web::HttpRequest,
) -> impl Responder {
    let ip = get_env("CAMERA_IP", "127.0.0.1");
    let user = get_env("CAMERA_USER", "admin");
    let pass = get_env("CAMERA_PASS", "12345");
    let rtsp_port = get_env("CAMERA_RTSP_PORT", "554");
    let http_port = get_env("CAMERA_HTTP_PORT", "80");
    let format = state.stream_format.lock().unwrap().clone();

    let (shutdown_tx, mut shutdown_rx) = broadcast::channel::<()>(2);

    // MJPEG proxying
    if format == "mjpeg" {
        let url = build_mjpeg_url(&ip, &user, &pass, &http_port);
        let client = reqwest::Client::new();

        let mut stream = match client.get(&url).send().await {
            Ok(resp) => resp.bytes_stream(),
            Err(_) => return HttpResponse::InternalServerError().body("Failed to connect to MJPEG stream"),
        };

        let boundary = "myboundary";
        let content_type = format!("multipart/x-mixed-replace; boundary={}", boundary);

        let mut body = actix_web::body::BodyStream::new(
            async_stream::stream! {
                loop {
                    tokio::select! {
                        _ = shutdown_rx.recv() => {
                            break;
                        }
                        Some(item) = stream.next() => {
                            if let Ok(bytes) = item {
                                // Try to find JPEG frame boundaries (not always strictly necessary, depends on camera output)
                                yield Ok::<Bytes, actix_web::Error>(Bytes::from(format!("\r\n--{}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n", boundary, bytes.len())));
                                yield Ok::<Bytes, actix_web::Error>(bytes);
                            } else {
                                break;
                            }
                        }
                        else => { break; }
                    }
                }
            }
        );
        return HttpResponse::Ok()
            .content_type(content_type)
            .streaming(body);
    }

    // RTSP (H.264) proxying: naive implementation, just TCP proxy to browser as raw stream (not playable in browser, but can be consumed by ffmpeg/vlc/mjpeg tools)
    // For browsers, real-time H.264 transport would require transmuxing to e.g. fragmented MP4 or MJPEG, which is out of scope for this minimal driver; so we stream raw H.264.
    let rtsp_url = build_rtsp_url(&ip, &user, &pass, &rtsp_port, &format);

    let url = match Url::parse(&rtsp_url) {
        Ok(u) => u,
        Err(_) => return HttpResponse::InternalServerError().body("Invalid RTSP URL"),
    };

    // Parse host/port/path
    let host = url.host_str().unwrap_or("127.0.0.1");
    let port = url.port().unwrap_or(554);
    let path = url.path();
    let userinfo = url.username();
    let password = url.password().unwrap_or("");

    // RTSP handshake and proxy
    let mut stream = match TcpStream::connect((host, port)).await {
        Ok(s) => s,
        Err(_) => return HttpResponse::InternalServerError().body("Failed to connect to camera RTSP port"),
    };
    let cseq = 1;

    // Send DESCRIBE
    let describe = format!(
        "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\nAccept: application/sdp\r\nAuthorization: Basic {}\r\n\r\n",
        rtsp_url,
        cseq,
        base64::encode(format!("{}:{}", userinfo, password))
    );
    if stream.write_all(describe.as_bytes()).await.is_err() {
        return HttpResponse::InternalServerError().body("Failed to send DESCRIBE");
    }
    let mut resp_buf = vec![0u8; 4096];
    let _ = stream.read(&mut resp_buf).await;

    // Send SETUP
    let setup = format!(
        "SETUP {}?trackID=1 RTSP/1.0\r\nCSeq: {}\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nAuthorization: Basic {}\r\n\r\n",
        rtsp_url,
        cseq + 1,
        base64::encode(format!("{}:{}", userinfo, password))
    );
    if stream.write_all(setup.as_bytes()).await.is_err() {
        return HttpResponse::InternalServerError().body("Failed to send SETUP");
    }
    let _ = stream.read(&mut resp_buf).await;

    // Send PLAY
    let play = format!(
        "PLAY {} RTSP/1.0\r\nCSeq: {}\r\nSession: 12345678\r\nAuthorization: Basic {}\r\n\r\n",
        rtsp_url,
        cseq + 2,
        base64::encode(format!("{}:{}", userinfo, password))
    );
    if stream.write_all(play.as_bytes()).await.is_err() {
        return HttpResponse::InternalServerError().body("Failed to send PLAY");
    }
    let _ = stream.read(&mut resp_buf).await;

    // Proxy the RTP stream over HTTP (note: browser can't natively play, but ffmpeg/vlc can)
    let mut shutdown_rx = state.shutdown_tx.subscribe();
    let body = actix_web::body::BodyStream::new(
        async_stream::stream! {
            let mut buf = [0u8; 4096];
            loop {
                tokio::select! {
                    _ = shutdown_rx.recv() => {
                        break;
                    }
                    n = stream.read(&mut buf) => {
                        match n {
                            Ok(0) | Err(_) => break,
                            Ok(sz) => {
                                yield Ok::<Bytes, actix_web::Error>(Bytes::copy_from_slice(&buf[..sz]));
                            }
                        }
                    }
                }
            }
        }
    );
    HttpResponse::Ok()
        .content_type("application/octet-stream")
        .streaming(body)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let server_host = get_env("SERVER_HOST", "0.0.0.0");
    let server_port = get_env("SERVER_PORT", "8080");

    let app_state = Arc::new(AppState {
        session_active: Mutex::new(false),
        stream_format: Mutex::new("h264".into()),
        shutdown_tx: broadcast::channel(2).0,
    });

    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(app_state.clone()))
            .service(start_stream)
            .service(stop_stream)
            .service(stream_url)
            .service(stream_live)
    })
    .bind(format!("{}:{}", server_host, server_port))?
    .run()
    .await
}