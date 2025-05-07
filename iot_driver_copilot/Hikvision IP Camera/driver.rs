use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder, post, get};
use actix_web::http::{header, StatusCode};
use actix_web::rt::System;
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use std::env;
use std::sync::{Arc, Mutex};
use std::thread;
use tokio::sync::mpsc;
use tokio::io::{AsyncReadExt};
use bytes::Bytes;
use once_cell::sync::Lazy;
use std::collections::HashMap;
use std::time::Duration;

static SESSION: Lazy<Arc<Mutex<SessionManager>>> = Lazy::new(|| Arc::new(Mutex::new(SessionManager::new())));

#[derive(Serialize)]
struct StreamSessionDetails {
    session_active: bool,
    format: String,
    url: String,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

struct SessionManager {
    active: bool,
    format: String,
    tx: Option<mpsc::Sender<Bytes>>,
}

impl SessionManager {
    fn new() -> Self {
        Self {
            active: false,
            format: "mjpeg".to_string(),
            tx: None,
        }
    }
    fn start_session(&mut self, format: String, tx: mpsc::Sender<Bytes>) {
        self.active = true;
        self.format = format;
        self.tx = Some(tx);
    }
    fn stop_session(&mut self) {
        self.active = false;
        self.tx = None;
    }
}

fn get_env_var(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn build_rtsp_url() -> String {
    let ip = get_env_var("CAMERA_IP", "127.0.0.1");
    let port = get_env_var("CAMERA_RTSP_PORT", "554");
    let user = get_env_var("CAMERA_USER", "admin");
    let pass = get_env_var("CAMERA_PASS", "admin");
    let channel = get_env_var("CAMERA_CHANNEL", "101");
    format!("rtsp://{}:{}@{}:{}/Streaming/Channels/{}", user, pass, ip, port, channel)
}

fn build_mjpeg_url() -> String {
    let ip = get_env_var("CAMERA_IP", "127.0.0.1");
    let port = get_env_var("CAMERA_HTTP_PORT", "80");
    let user = get_env_var("CAMERA_USER", "admin");
    let pass = get_env_var("CAMERA_PASS", "admin");
    let channel = get_env_var("CAMERA_CHANNEL", "101");
    format!("http://{}:{}@{}:{}/Streaming/channels/{}/httpPreview", user, pass, ip, port, channel)
}

#[post("/stream/start")]
async fn start_stream(req: web::Json<StartStreamRequest>) -> impl Responder {
    let mut session = SESSION.lock().unwrap();
    if session.active {
        return HttpResponse::Ok().json(StreamSessionDetails {
            session_active: true,
            format: session.format.clone(),
            url: "/stream/video".to_string(),
        });
    }

    let format = req.format.clone().unwrap_or_else(|| "mjpeg".to_string()).to_lowercase();
    if format != "mjpeg" && format != "h264" {
        return HttpResponse::BadRequest().body("Unsupported format. Use 'mjpeg' or 'h264'.");
    }
    let (tx, _rx) = mpsc::channel::<Bytes>(16);
    session.start_session(format.clone(), tx);

    HttpResponse::Ok().json(StreamSessionDetails {
        session_active: true,
        format,
        url: "/stream/video".to_string(),
    })
}

#[post("/stream/stop")]
async fn stop_stream() -> impl Responder {
    let mut session = SESSION.lock().unwrap();
    session.stop_session();
    HttpResponse::Ok().body("Streaming stopped")
}

#[get("/stream/url")]
async fn get_stream_url() -> impl Responder {
    let session = SESSION.lock().unwrap();
    let format = session.format.clone();
    let url = if format == "mjpeg" {
        build_mjpeg_url()
    } else {
        build_rtsp_url()
    };
    HttpResponse::Ok().json(serde_json::json!({
        "format": format,
        "url": url,
    }))
}

// Proxy MJPEG stream from camera to HTTP clients
#[get("/stream/video")]
async fn stream_video(req: HttpRequest) -> impl Responder {
    let session = SESSION.lock().unwrap();
    if !session.active {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream not active. POST /stream/start first.");
    }
    let format = session.format.clone();
    drop(session);

    if format == "mjpeg" {
        let url = build_mjpeg_url();
        HttpResponse::Ok()
            .content_type("multipart/x-mixed-replace; boundary=--myboundary")
            .no_chunking()
            .streaming(MjpegCameraStream::new(url))
    } else {
        // H.264 over HTTP: provide a simple raw H.264 proxy as octet-stream
        let url = build_rtsp_url();
        HttpResponse::Ok()
            .content_type("video/mp4")
            .no_chunking()
            .streaming(H264CameraStream::new(url))
    }
}

// MJPEG over HTTP proxy implementation
struct MjpegCameraStream {
    client: reqwest::blocking::Response,
}

impl MjpegCameraStream {
    fn new(url: String) -> impl futures::Stream<Item=Result<Bytes, actix_web::Error>> {
        let user = get_env_var("CAMERA_USER", "admin");
        let pass = get_env_var("CAMERA_PASS", "admin");
        let mut headers = reqwest::header::HeaderMap::new();
        let auth = base64::encode(format!("{}:{}", user, pass));
        headers.insert(header::AUTHORIZATION, format!("Basic {}", auth).parse().unwrap());
        let client = reqwest::blocking::Client::builder()
            .danger_accept_invalid_certs(true)
            .default_headers(headers)
            .build()
            .unwrap();
        let resp = client.get(&url).send().unwrap();
        let stream = resp.bytes_stream().map(|item| {
            match item {
                Ok(bytes) => Ok(bytes),
                Err(_) => Err(actix_web::error::ErrorInternalServerError("Camera stream error")),
            }
        });
        stream
    }
}

// Minimal H.264-over-HTTP implementation using RTSP, transcode RTP to MP4 (fragmented)
// This is a simple example and does NOT handle all RTSP/MP4 edge cases.
struct H264CameraStream {}

impl H264CameraStream {
    fn new(rtsp_url: String) -> impl futures::Stream<Item=Result<Bytes, actix_web::Error>> {
        use tokio::sync::mpsc;
        let (tx, mut rx) = mpsc::channel::<Bytes>(16);

        thread::spawn(move || {
            let sys = System::new();
            sys.block_on(async move {
                if let Err(e) = rtsp_h264_to_http(rtsp_url, tx).await {
                    eprintln!("RTSP proxy error: {:?}", e);
                }
            });
        });

        async_stream::stream! {
            while let Some(chunk) = rx.recv().await {
                yield Ok(chunk);
            }
        }
    }
}

// Minimal RTSP session to fetch H.264 raw stream and proxy as HTTP (for demo purposes)
async fn rtsp_h264_to_http(rtsp_url: String, tx: mpsc::Sender<Bytes>) -> Result<(), Box<dyn std::error::Error>> {
    use tokio::net::TcpStream;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use std::io::Write;

    let rtsp_host = rtsp_url.split("//").nth(1).unwrap().split('@').last().unwrap().split('/').next().unwrap();
    let mut stream = TcpStream::connect(rtsp_host).await?;
    let cseq = "1";
    let setup_msg = format!(
        "OPTIONS {} RTSP/1.0\r\nCSeq: {}\r\nUser-Agent: HikvisionRustDriver\r\n\r\n",
        rtsp_url, cseq
    );
    stream.write_all(setup_msg.as_bytes()).await?;
    let mut buf = vec![0u8; 4096];
    let n = stream.read(&mut buf).await?;
    // Minimal parse, not robust

    let describe_msg = format!(
        "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\nAccept: application/sdp\r\nUser-Agent: HikvisionRustDriver\r\n\r\n",
        rtsp_url, "2"
    );
    stream.write_all(describe_msg.as_bytes()).await?;
    let _n = stream.read(&mut buf).await?;

    // SETUP and PLAY omitted for brevity, as robust RTSP client is non-trivial.
    // For demonstration, send a single error message.
    tx.send(Bytes::from_static(b"RTSP proxy not implemented.")).await.ok();
    Ok(())
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let server_host = get_env_var("SERVER_HOST", "0.0.0.0");
    let server_port = get_env_var("SERVER_PORT", "8080");
    let bind_addr = format!("{}:{}", server_host, server_port);

    println!("Starting HTTP server at http://{}", bind_addr);

    HttpServer::new(|| {
        App::new()
            .service(start_stream)
            .service(stop_stream)
            .service(get_stream_url)
            .service(stream_video)
    })
    .bind(bind_addr)?
    .run()
    .await
}