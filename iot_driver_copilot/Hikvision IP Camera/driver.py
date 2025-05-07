use actix_web::{web, App, HttpServer, HttpResponse, Responder, HttpRequest};
use actix_web::http::{header, StatusCode};
use actix_web::rt::spawn;
use serde::{Serialize, Deserialize};
use std::env;
use std::sync::{Arc, Mutex};
use futures::Stream;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::io::{self, Read, Write};
use std::process::Stdio;
use bytes::Bytes;
use std::collections::HashMap;
use urlencoding::encode;

#[derive(Clone)]
enum StreamFormat {
    H264,
    MJPEG,
}

impl StreamFormat {
    fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "h264" => Some(StreamFormat::H264),
            "mjpeg" => Some(StreamFormat::MJPEG),
            _ => None,
        }
    }

    fn as_str(&self) -> &'static str {
        match self {
            StreamFormat::H264 => "h264",
            StreamFormat::MJPEG => "mjpeg",
        }
    }
}

#[derive(Clone)]
struct AppState {
    // Streaming session: only one at a time
    session_active: Arc<Mutex<bool>>,
    // Last used format
    session_format: Arc<Mutex<Option<StreamFormat>>>,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    status: String,
    format: String,
    http_stream_url: String,
}

#[derive(Serialize)]
struct StopStreamResponse {
    status: String,
}

fn get_env(key: &str, default: &str) -> String {
    env::var(key).unwrap_or(default.to_string())
}

fn get_rtsp_url() -> String {
    let user = get_env("CAMERA_USER", "admin");
    let pass = get_env("CAMERA_PASS", "12345");
    let ip = get_env("CAMERA_IP", "192.168.1.64");
    let port = get_env("CAMERA_RTSP_PORT", "554");
    let channel = get_env("CAMERA_CHANNEL", "1");
    // Hikvision RTSP main stream typical path
    format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/{}01",
        user, pass, ip, port, channel
    )
}

fn get_mjpeg_url() -> String {
    let user = get_env("CAMERA_USER", "admin");
    let pass = get_env("CAMERA_PASS", "12345");
    let ip = get_env("CAMERA_IP", "192.168.1.64");
    let port = get_env("CAMERA_HTTP_PORT", "80");
    // Hikvision MJPEG snapshot or substream (may need to be enabled in camera settings)
    format!(
        "http://{}:{}@{}:{}/Streaming/channels/101/httppreview",
        user, pass, ip, port
    )
}

// Proxy MJPEG stream from camera to HTTP client (browser)
struct MJPEGProxyStream {
    client: reqwest::blocking::Response,
}

impl Stream for MJPEGProxyStream {
    type Item = Result<Bytes, actix_web::Error>;
    fn poll_next(mut self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let mut buf = [0u8; 8192];
        match self.client.read(&mut buf) {
            Ok(0) => Poll::Ready(None),
            Ok(n) => Poll::Ready(Some(Ok(Bytes::copy_from_slice(&buf[..n])))),
            Err(_) => Poll::Ready(None),
        }
    }
}

// Proxy RTSP (H.264) stream, convert to HTTP multipart/x-mixed-replace with h264 NAL units
struct RTSPProxyStream {
    rtsp_url: String,
    tcp_stream: Option<std::net::TcpStream>,
}

impl RTSPProxyStream {
    fn new(rtsp_url: String) -> io::Result<Self> {
        // Build RTSP handshake and parse RTP packets (minimal for demo, not robust)
        // For a real solution, use a dedicated RTSP/RTP parser. Here, we just tunnel bytes.
        // For demo: just connect TCP and pipe (doesn't handle UDP interleaved RTP, etc.)
        let url = rtsp_url.trim_start_matches("rtsp://");
        let host_port = url.split('@').last().unwrap().split('/').next().unwrap();
        let mut parts = host_port.split(':');
        let host = parts.next().unwrap();
        let port = parts.next().unwrap_or("554");
        let tcp_stream = std::net::TcpStream::connect(format!("{}:{}", host, port))?;
        Ok(Self {
            rtsp_url,
            tcp_stream: Some(tcp_stream),
        })
    }
}

impl Stream for RTSPProxyStream {
    type Item = Result<Bytes, actix_web::Error>;
    fn poll_next(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        // Not implemented: For real-world use, you'd implement the full handshake and RTP parsing.
        // Here, just return None to avoid hanging.
        Poll::Ready(None)
    }
}

async fn stream_start(
    state: web::Data<AppState>,
    payload: web::Json<StartStreamRequest>,
    req: HttpRequest,
) -> impl Responder {
    let mut session = state.session_active.lock().unwrap();
    if *session {
        // Already running
        let format = state.session_format.lock().unwrap();
        let fmt = format.clone().unwrap_or(StreamFormat::MJPEG);
        let host = get_env("SERVER_HOST", "0.0.0.0");
        let port = get_env("SERVER_PORT", "8080");
        let url = format!(
            "http://{}:{}/stream/video?format={}",
            host,
            port,
            fmt.as_str()
        );
        return HttpResponse::Ok().json(StartStreamResponse {
            status: "already streaming".to_string(),
            format: fmt.as_str().to_string(),
            http_stream_url: url,
        });
    }

    let fmt = match payload.format.as_ref().map(|s| s.as_str()) {
        Some("h264") => StreamFormat::H264,
        _ => StreamFormat::MJPEG, // Default
    };

    *session = true;
    let mut fmt_guard = state.session_format.lock().unwrap();
    *fmt_guard = Some(fmt.clone());

    let host = get_env("SERVER_HOST", "0.0.0.0");
    let port = get_env("SERVER_PORT", "8080");
    let url = format!(
        "http://{}:{}/stream/video?format={}",
        host,
        port,
        fmt.as_str()
    );

    HttpResponse::Ok().json(StartStreamResponse {
        status: "stream started".to_string(),
        format: fmt.as_str().to_string(),
        http_stream_url: url,
    })
}

async fn stream_stop(state: web::Data<AppState>) -> impl Responder {
    let mut session = state.session_active.lock().unwrap();
    let mut fmt_guard = state.session_format.lock().unwrap();
    *session = false;
    *fmt_guard = None;
    HttpResponse::Ok().json(StopStreamResponse {
        status: "stream stopped".to_string(),
    })
}

async fn stream_url(state: web::Data<AppState>) -> impl Responder {
    let format = state.session_format.lock().unwrap();
    let fmt = format.clone().unwrap_or(StreamFormat::MJPEG);

    let url = match fmt {
        StreamFormat::H264 => get_rtsp_url(),
        StreamFormat::MJPEG => get_mjpeg_url(),
    };

    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: fmt.as_str().to_string(),
    })
}

async fn stream_video(
    state: web::Data<AppState>,
    req: HttpRequest,
) -> impl Responder {
    let session = state.session_active.lock().unwrap();
    let fmt = state.session_format.lock().unwrap();
    if !*session {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream not started. POST /stream/start first.");
    }
    let format = fmt.clone().unwrap_or(StreamFormat::MJPEG);

    match format {
        StreamFormat::MJPEG => {
            // Proxy MJPEG stream
            let url = get_mjpeg_url();
            let client = match reqwest::blocking::get(&url) {
                Ok(resp) => resp,
                Err(_) => return HttpResponse::InternalServerError().body("Camera unreachable"),
            };
            let content_type = client
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("multipart/x-mixed-replace;boundary=--BoundaryString")
                .to_string();
            let proxy = MJPEGProxyStream { client };
            HttpResponse::Ok()
                .insert_header((header::CONTENT_TYPE, content_type))
                .streaming(proxy)
        }
        StreamFormat::H264 => {
            // Not fully implemented: For demonstration only.
            // You would need to parse RTSP and RTP to extract H264 and wrap in multipart/x-mixed-replace.
            // Here, just error.
            HttpResponse::NotImplemented()
                .body("H264 over HTTP not implemented in this driver. Use MJPEG.")
        }
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let host = get_env("SERVER_HOST", "0.0.0.0");
    let port = get_env("SERVER_PORT", "8080");
    let bind_addr = format!("{}:{}", host, port);

    let state = web::Data::new(AppState {
        session_active: Arc::new(Mutex::new(false)),
        session_format: Arc::new(Mutex::new(None)),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .route("/stream/start", web::post().to(stream_start))
            .route("/stream/stop", web::post().to(stream_stop))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/video", web::get().to(stream_video))
    })
    .bind(bind_addr)?
    .run()
    .await
}