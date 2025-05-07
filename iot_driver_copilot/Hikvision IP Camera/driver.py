use actix_web::{web, App, HttpResponse, HttpServer, Responder, get, post, rt::spawn, HttpRequest};
use actix_web::web::{Bytes, Data};
use futures::{Stream, StreamExt};
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use tokio::io::{AsyncReadExt};
use tokio::net::TcpStream;
use tokio::sync::mpsc::{self, Sender, Receiver};
use bytes::BytesMut;

#[derive(Clone)]
struct AppState {
    rtsp_session: Arc<Mutex<Option<StreamSession>>>,
    device_ip: String,
    username: String,
    password: String,
    rtsp_port: u16,
    mjpeg_path: String,
    h264_path: String,
}

struct StreamSession {
    format: String,
    running: bool,
}

#[post("/stream/start")]
async fn stream_start(state: Data<AppState>, body: web::Json<HashMap<String, String>>) -> impl Responder {
    let format = body.get("format").map(|f| f.to_lowercase()).unwrap_or("mjpeg".to_string());
    if format != "h264" && format != "mjpeg" {
        return HttpResponse::BadRequest().body("Invalid format. Use 'h264' or 'mjpeg'.");
    }
    let mut session = state.rtsp_session.lock().unwrap();
    *session = Some(StreamSession {
        format: format.clone(),
        running: true,
    });
    HttpResponse::Ok().json(serde_json::json!({
        "status": "started",
        "format": format,
    }))
}

#[post("/stream/stop")]
async fn stream_stop(state: Data<AppState>) -> impl Responder {
    let mut session = state.rtsp_session.lock().unwrap();
    let running = session.as_ref().map(|s| s.running).unwrap_or(false);
    *session = None;
    if running {
        HttpResponse::Ok().json(serde_json::json!({"status": "stopped"}))
    } else {
        HttpResponse::Ok().json(serde_json::json!({"status": "already stopped"}))
    }
}

#[get("/stream/url")]
async fn stream_url(state: Data<AppState>) -> impl Responder {
    let session = state.rtsp_session.lock().unwrap();
    if let Some(s) = session.as_ref() {
        let url = match s.format.as_str() {
            "h264" => format!(
                "rtsp://{}:{}@{}:{}/{}",
                state.username, state.password, state.device_ip, state.rtsp_port, state.h264_path
            ),
            _ => format!(
                "http://{}:{}@{}:{}/{}",
                state.username, state.password, state.device_ip, 80, state.mjpeg_path
            ),
        };
        HttpResponse::Ok().json(serde_json::json!({
            "url": url,
            "format": s.format,
        }))
    } else {
        HttpResponse::BadRequest().body("Stream is not started")
    }
}

#[get("/video")]
async fn video_stream(req: HttpRequest, state: Data<AppState>) -> impl Responder {
    let session = state.rtsp_session.lock().unwrap();
    if let Some(s) = session.as_ref() {
        if s.format == "mjpeg" {
            // MJPEG over HTTP proxy
            let url = format!(
                "http://{}:{}@{}:{}/{}",
                state.username, state.password, state.device_ip, 80, state.mjpeg_path
            );
            drop(session);
            return proxy_mjpeg_stream(url).await;
        } else if s.format == "h264" {
            // RTSP over HTTP (RFC2326, raw H264)
            let url = format!(
                "rtsp://{}:{}@{}:{}/{}",
                state.username, state.password, state.device_ip, state.rtsp_port, state.h264_path
            );
            drop(session);
            return proxy_rtsp_h264_stream(url).await;
        }
    }
    HttpResponse::BadRequest().body("Stream is not started")
}

async fn proxy_mjpeg_stream(url: String) -> HttpResponse {
    let client = reqwest::Client::new();
    let stream_res = client.get(&url).send().await;
    if let Ok(mut res) = stream_res {
        let content_type = res
            .headers()
            .get("Content-Type")
            .and_then(|ct| ct.to_str().ok())
            .unwrap_or("multipart/x-mixed-replace; boundary=--myboundary")
            .to_string();

        let stream = async_stream::stream! {
            while let Some(chunk) = res.chunk().await.unwrap_or(None) {
                yield Ok::<_, actix_web::Error>(Bytes::from(chunk));
            }
        };
        HttpResponse::Ok()
            .content_type(content_type)
            .streaming(Box::pin(stream) as Pin<Box<dyn Stream<Item = Result<Bytes, actix_web::Error>> + Send>>)
    } else {
        HttpResponse::BadGateway().body("Failed to connect to camera MJPEG stream")
    }
}

async fn proxy_rtsp_h264_stream(url: String) -> HttpResponse {
    // Minimal RTSP client for H264 RTP-over-TCP interleaved stream, send data as raw H264 (not playable in browsers directly, but can be played with ffmpeg/jsmpeg clients)
    // Only supporting basic implementations (no external binaries)
    // For full browser support, a transcoder is required, but here just proxy raw H264 RTP-over-TCP over HTTP

    let parsed = url::Url::parse(&url).ok();
    let (host, port, path, user, pass) = if let Some(u) = parsed {
        (
            u.host_str().unwrap_or(""),
            u.port().unwrap_or(554),
            u.path().trim_start_matches('/').to_string(),
            u.username().to_string(),
            u.password().unwrap_or("").to_string()
        )
    } else {
        return HttpResponse::BadRequest().body("Invalid RTSP URL");
    };
    let addr = format!("{}:{}", host, port);

    let mut stream = match TcpStream::connect(addr).await {
        Ok(s) => s,
        Err(_) => return HttpResponse::BadGateway().body("Failed to connect to RTSP server"),
    };

    let session_id = "123456";
    let auth = if !user.is_empty() {
        let creds = format!("{}:{}", user, pass);
        let enc = base64::encode(creds.as_bytes());
        format!("Authorization: Basic {}\r\n", enc)
    } else {
        "".to_string()
    };
    let describe = format!(
        "DESCRIBE rtsp://{}/{} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n{}User-Agent: hik-rs\r\n\r\n",
        host, path, auth
    );
    if let Err(_) = stream.write_all(describe.as_bytes()).await { return HttpResponse::BadGateway().body("RTSP Error"); }
    let mut buf = vec![0u8; 4096];
    if let Err(_) = stream.read(&mut buf).await { return HttpResponse::BadGateway().body("RTSP Error"); }
    // (skipping SDP parse, use default channel=0 for video)

    let setup = format!(
        "SETUP rtsp://{}/{} RTSP/1.0\r\nCSeq: 3\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n{}User-Agent: hik-rs\r\n\r\n",
        host, path, auth
    );
    if let Err(_) = stream.write_all(setup.as_bytes()).await { return HttpResponse::BadGateway().body("RTSP Error"); }
    if let Err(_) = stream.read(&mut buf).await { return HttpResponse::BadGateway().body("RTSP Error"); }

    let play = format!(
        "PLAY rtsp://{}/{} RTSP/1.0\r\nCSeq: 4\r\nSession: {}\r\n{}User-Agent: hik-rs\r\n\r\n",
        host, path, session_id, auth
    );
    if let Err(_) = stream.write_all(play.as_bytes()).await { return HttpResponse::BadGateway().body("RTSP Error"); }
    if let Err(_) = stream.read(&mut buf).await { return HttpResponse::BadGateway().body("RTSP Error"); }

    let (tx, mut rx): (Sender<Result<Bytes, actix_web::Error>>, Receiver<Result<Bytes, actix_web::Error>>) = mpsc::channel(16);
    let mut stream_clone = stream.try_clone().unwrap();
    spawn(async move {
        let mut buf = [0u8; 4096];
        loop {
            match stream_clone.read(&mut buf).await {
                Ok(n) if n > 0 => {
                    let _ = tx.send(Ok(Bytes::copy_from_slice(&buf[0..n]))).await;
                }
                _ => break,
            }
        }
    });

    let output_stream = async_stream::stream! {
        while let Some(chunk) = rx.recv().await {
            yield chunk;
        }
    };

    HttpResponse::Ok()
        .content_type("video/H264")
        .streaming(Box::pin(output_stream) as Pin<Box<dyn Stream<Item = Result<Bytes, actix_web::Error>> + Send>>)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let device_ip = env::var("DEVICE_IP").expect("DEVICE_IP env required");
    let username = env::var("DEVICE_USERNAME").unwrap_or("admin".to_string());
    let password = env::var("DEVICE_PASSWORD").unwrap_or("12345".to_string());
    let server_host = env::var("SERVER_HOST").unwrap_or("0.0.0.0".to_string());
    let server_port: u16 = env::var("SERVER_PORT").unwrap_or("8080".to_string()).parse().unwrap_or(8080);
    let rtsp_port: u16 = env::var("RTSP_PORT").unwrap_or("554".to_string()).parse().unwrap_or(554);
    let mjpeg_path = env::var("MJPEG_PATH").unwrap_or("Streaming/channels/102/httppreview".to_string());
    let h264_path = env::var("H264_PATH").unwrap_or("Streaming/channels/101".to_string());

    let state = Data::new(AppState {
        rtsp_session: Arc::new(Mutex::new(None)),
        device_ip,
        username,
        password,
        rtsp_port,
        mjpeg_path,
        h264_path,
    });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .service(stream_start)
            .service(stream_stop)
            .service(stream_url)
            .service(video_stream)
    })
    .bind((server_host, server_port))?
    .run()
    .await
}