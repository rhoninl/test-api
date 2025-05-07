use actix_web::{web, App, HttpResponse, HttpServer, Responder, post, get};
use actix_web::rt::task::JoinHandle;
use actix_web::rt::spawn;
use actix_web::http::{header, StatusCode};
use std::env;
use std::sync::{Arc, Mutex};
use std::io::{Read, Write};
use serde_json::json;
use serde::{Deserialize};
use std::collections::HashMap;
use std::thread;
use std::net::TcpStream;

struct AppState {
    streaming: Mutex<bool>,
    stream_handle: Mutex<Option<JoinHandle<()>>>,
    stream_format: Mutex<String>,
}

/// MJPEG HTTP proxy: receives RTSP MJPEG stream, parses frames, and emits as HTTP multipart/x-mixed-replace
async fn mjpeg_proxy(data: web::Data<AppState>) -> impl Responder {
    let device_ip = env::var("DEVICE_IP").unwrap_or("127.0.0.1".into());
    let rtsp_port = env::var("DEVICE_RTSP_PORT").unwrap_or("554".into());
    let http_port = env::var("DEVICE_HTTP_PORT").unwrap_or("80".into());
    let username = env::var("DEVICE_USERNAME").unwrap_or("admin".into());
    let password = env::var("DEVICE_PASSWORD").unwrap_or("12345".into());
    let stream_url = format!(
        "http://{}:{}/Streaming/channels/101/httppreview",
        device_ip,
        http_port
    );

    let client = reqwest::Client::new();
    let res = client
        .get(&stream_url)
        .basic_auth(username, Some(password))
        .send()
        .await;

    match res {
        Ok(mut stream) => {
            let mut stream_resp = HttpResponse::Ok();
            stream_resp
                .content_type("multipart/x-mixed-replace; boundary=--myboundary")
                .streaming(futures_util::stream::unfold((), move |_| {
                    let mut buf = vec![0u8; 8192];
                    async {
                        match stream.chunk().await {
                            Ok(Some(chunk)) => {
                                let mut part = Vec::new();
                                part.extend_from_slice(b"\r\n--myboundary\r\nContent-Type: image/jpeg\r\n\r\n");
                                part.extend_from_slice(&chunk);
                                Some((Ok::<_, actix_web::Error>(part), ()))
                            }
                            Ok(None) | Err(_) => None,
                        }
                    }
                }));
            stream_resp
        }
        Err(_) => HttpResponse::InternalServerError().body("Failed to connect to camera stream"),
    }
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

// /stream/start: instruct to start streaming (format: h264 or mjpeg)
#[post("/stream/start")]
async fn stream_start(
    data: web::Data<AppState>,
    req_body: web::Json<StartStreamRequest>,
) -> impl Responder {
    let mut streaming = data.streaming.lock().unwrap();
    let mut stream_format = data.stream_format.lock().unwrap();

    if *streaming {
        return HttpResponse::BadRequest().json(json!({"error": "Stream already started"}));
    }

    let format = req_body.format.clone().unwrap_or("mjpeg".to_string()).to_lowercase();
    if format != "mjpeg" {
        return HttpResponse::BadRequest().json(json!({"error": "Only MJPEG format is supported for HTTP browser streaming"}));
    }

    *streaming = true;
    *stream_format = format.clone();

    HttpResponse::Ok().json(json!({
        "status": "stream_started",
        "format": format,
        "stream_url": "/stream/live"
    }))
}

// /stream/stop: stop streaming
#[post("/stream/stop")]
async fn stream_stop(data: web::Data<AppState>) -> impl Responder {
    let mut streaming = data.streaming.lock().unwrap();
    let mut handle = data.stream_handle.lock().unwrap();

    *streaming = false;

    if let Some(h) = handle.take() {
        h.abort();
    }

    HttpResponse::Ok().json(json!({
        "status": "stream_stopped"
    }))
}

// /stream/url: returns the camera's RTSP or MJPEG URL (not for browser use)
#[get("/stream/url")]
async fn stream_url() -> impl Responder {
    let device_ip = env::var("DEVICE_IP").unwrap_or("127.0.0.1".into());
    let rtsp_port = env::var("DEVICE_RTSP_PORT").unwrap_or("554".into());
    let http_port = env::var("DEVICE_HTTP_PORT").unwrap_or("80".into());
    let username = env::var("DEVICE_USERNAME").unwrap_or("admin".into());
    let password = env::var("DEVICE_PASSWORD").unwrap_or("12345".into());

    let rtsp_url = format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
        username, password, device_ip, rtsp_port
    );
    let mjpeg_url = format!(
        "http://{}:{}/Streaming/channels/101/httppreview",
        device_ip, http_port
    );
    HttpResponse::Ok().json(json!({
        "rtsp": rtsp_url,
        "mjpeg": mjpeg_url
    }))
}

// /stream/live: HTTP MJPEG streaming endpoint
#[get("/stream/live")]
async fn stream_live(data: web::Data<AppState>) -> impl Responder {
    let streaming = data.streaming.lock().unwrap();
    let stream_format = data.stream_format.lock().unwrap();

    if !*streaming || *stream_format != "mjpeg" {
        return HttpResponse::BadRequest().body("Stream not started or format not MJPEG");
    }
    mjpeg_proxy(data).await
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let server_host = env::var("SERVER_HOST").unwrap_or("0.0.0.0".into());
    let server_port = env::var("SERVER_PORT").unwrap_or("8080".into());

    let app_state = web::Data::new(AppState {
        streaming: Mutex::new(false),
        stream_handle: Mutex::new(None),
        stream_format: Mutex::new("mjpeg".to_string()),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(app_state.clone())
            .service(stream_start)
            .service(stream_stop)
            .service(stream_url)
            .service(stream_live)
    })
    .bind(format!("{}:{}", server_host, server_port))?
    .run()
    .await
}