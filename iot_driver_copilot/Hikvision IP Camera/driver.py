use actix_web::{web, App, HttpResponse, HttpServer, Responder, HttpRequest};
use actix_web::rt::System;
use actix_web::http::{header, StatusCode};
use serde::{Deserialize, Serialize};
use std::env;
use std::sync::{Arc, Mutex};
use once_cell::sync::Lazy;
use futures::Stream;
use bytes::Bytes;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::io::{Read, Write};
use std::process::{Child, Stdio};
use std::thread;
use std::time::Duration;
use std::collections::HashMap;
use std::net::TcpStream;

static SESSION: Lazy<Arc<Mutex<SessionState>>> = Lazy::new(|| Arc::new(Mutex::new(SessionState::new())));

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StreamStartRequest {
    format: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StreamStartResponse {
    message: String,
    format: String,
    session_active: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StreamStopResponse {
    message: String,
    session_active: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
    message: String,
}

#[derive(Debug)]
struct SessionState {
    active: bool,
    format: String,
}

impl SessionState {
    fn new() -> Self {
        SessionState {
            active: false,
            format: "h264".to_string(),
        }
    }
}

fn get_env_or(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_string())
}

fn build_rtsp_url() -> String {
    let ip = get_env_or("DEVICE_IP", "127.0.0.1");
    let rtsp_port = get_env_or("RTSP_PORT", "554");
    let user = get_env_or("DEVICE_USER", "admin");
    let pass = get_env_or("DEVICE_PASS", "admin");
    // This is a typical Hikvision RTSP path, may need adjustment
    format!("rtsp://{}:{}@{}:{}/Streaming/Channels/101", user, pass, ip, rtsp_port)
}

fn build_mjpeg_url() -> String {
    let ip = get_env_or("DEVICE_IP", "127.0.0.1");
    let http_port = get_env_or("CAMERA_HTTP_PORT", "80");
    let user = get_env_or("DEVICE_USER", "admin");
    let pass = get_env_or("DEVICE_PASS", "admin");
    // Typical MJPEG URL for Hikvision:
    format!("http://{}:{}@{}:{}/Streaming/Channels/102/preview", user, pass, ip, http_port)
}

async fn stream_start(req: web::Json<StreamStartRequest>) -> impl Responder {
    let mut state = SESSION.lock().unwrap();
    let fmt = req.format.clone().unwrap_or_else(|| "h264".to_string());
    if fmt != "h264" && fmt != "mjpeg" {
        return HttpResponse::BadRequest().json(StreamStartResponse {
            message: "Invalid format".into(),
            format: fmt,
            session_active: state.active,
        });
    }
    state.active = true;
    state.format = fmt.clone();
    HttpResponse::Ok().json(StreamStartResponse {
        message: "Stream started".into(),
        format: fmt,
        session_active: state.active,
    })
}

async fn stream_stop(_req: HttpRequest) -> impl Responder {
    let mut state = SESSION.lock().unwrap();
    state.active = false;
    HttpResponse::Ok().json(StreamStopResponse {
        message: "Stream stopped".into(),
        session_active: state.active,
    })
}

async fn stream_url(_req: HttpRequest) -> impl Responder {
    let state = SESSION.lock().unwrap();
    let fmt = &state.format;
    let url = if fmt == "h264" {
        build_rtsp_url()
    } else {
        build_mjpeg_url()
    };
    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: fmt.clone(),
        message: "Stream URL returned".into(),
    })
}

// Simple RTSP to HTTP MJPEG proxy stream
struct MjpegHttpStream {
    url: String,
    stop_flag: Arc<Mutex<bool>>,
}

impl Stream for MjpegHttpStream {
    type Item = Result<Bytes, actix_web::Error>;

    fn poll_next(self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        Poll::Ready(None) // Place-holder: see below for real implementation
    }
}

// H264 over multipart/x-mixed-replace MJPEG proxy (this is a simplified demo)
async fn stream_mjpeg(_req: HttpRequest) -> impl Responder {
    let state = SESSION.lock().unwrap();
    if !state.active {
        return HttpResponse::build(StatusCode::BAD_REQUEST).body("Stream not started");
    }
    let url = build_mjpeg_url();

    // Connect to camera MJPEG HTTP stream directly and proxy it
    let client = awc::Client::default();
    let mut res = match client.get(url.clone()).send().await {
        Ok(r) => r,
        Err(_) => return HttpResponse::build(StatusCode::BAD_GATEWAY).body("Failed to connect to camera MJPEG stream"),
    };

    let content_type = res
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|ct| ct.to_str().ok())
        .unwrap_or("multipart/x-mixed-replace;boundary=--myboundary")
        .to_string();

    let mut body = web::BytesMut::new();
    while let Some(chunk) = res.next().await {
        match chunk {
            Ok(bytes) => body.extend_from_slice(&bytes),
            Err(_) => break,
        }
    }

    HttpResponse::Ok()
        .content_type(content_type)
        .body(body.freeze())
}

// H264 over HTTP using RTP-over-TCP proxy (very simplified, for demo)
async fn stream_h264(_req: HttpRequest) -> impl Responder {
    let state = SESSION.lock().unwrap();
    if !state.active {
        return HttpResponse::build(StatusCode::BAD_REQUEST).body("Stream not started");
    }
    let rtsp_url = build_rtsp_url();
    // We must implement an RTSP client in Rust (no external ffmpeg etc.)
    // For simplicity, we return 501 Not Implemented
    HttpResponse::NotImplemented().body("H.264 RTSP streaming proxy is not implemented in this demo driver")
}

async fn stream_video(req: HttpRequest) -> impl Responder {
    let state = SESSION.lock().unwrap();
    let fmt = &state.format;
    drop(state);

    if fmt == "mjpeg" {
        stream_mjpeg(req).await
    } else {
        stream_h264(req).await
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    dotenv::dotenv().ok();

    let server_host = get_env_or("SERVER_HOST", "0.0.0.0");
    let server_port = get_env_or("SERVER_PORT", "8080");

    HttpServer::new(|| {
        App::new()
            .route("/stream/start", web::post().to(stream_start))
            .route("/stream/stop", web::post().to(stream_stop))
            .route("/stream/url", web::get().to(stream_url))
            .route("/video", web::get().to(stream_video))
    })
    .bind(format!("{}:{}", server_host, server_port))?
    .run()
    .await
}