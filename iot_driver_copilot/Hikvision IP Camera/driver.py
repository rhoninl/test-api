use actix_web::{web, App, HttpServer, HttpResponse, Responder, Error, HttpRequest};
use actix_web::http::{header, StatusCode};
use std::env;
use std::sync::{Arc, Mutex};
use futures::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use reqwest::Client;
use bytes::Bytes;
use tokio::io::{AsyncReadExt};
use std::collections::HashMap;

#[derive(Clone, Debug)]
struct AppState {
    camera_ip: String,
    camera_rtsp_port: u16,
    camera_http_port: u16,
    camera_user: String,
    camera_pass: String,
    stream_format: Arc<Mutex<Option<String>>>,
    session_active: Arc<Mutex<bool>>,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    status: String,
    session_active: bool,
    format: String,
}

#[derive(Serialize)]
struct StopStreamResponse {
    status: String,
    session_active: bool,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
}

fn get_env_var(key: &str, default: Option<&str>) -> String {
    match env::var(key) {
        Ok(val) => val,
        Err(_) => default.unwrap_or("").to_string(),
    }
}

fn build_rtsp_url(state: &AppState, format: &str) -> String {
    match format.to_lowercase().as_str() {
        "h264" => format!(
            "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
            state.camera_user,
            state.camera_pass,
            state.camera_ip,
            state.camera_rtsp_port
        ),
        "mjpeg" => format!(
            "rtsp://{}:{}@{}:{}/Streaming/Channels/102",
            state.camera_user,
            state.camera_pass,
            state.camera_ip,
            state.camera_rtsp_port
        ),
        _ => format!(
            "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
            state.camera_user,
            state.camera_pass,
            state.camera_ip,
            state.camera_rtsp_port
        ),
    }
}

fn build_mjpeg_url(state: &AppState) -> String {
    format!(
        "http://{}:{}/Streaming/channels/102/httppreview",
        state.camera_ip,
        state.camera_http_port
    )
}

async fn stream_start(
    state: web::Data<AppState>,
    req: web::Json<StartStreamRequest>,
) -> impl Responder {
    let mut fmt_guard = state.stream_format.lock().unwrap();
    let mut session_guard = state.session_active.lock().unwrap();

    let fmt = req.format.clone().unwrap_or_else(|| "mjpeg".to_string()).to_lowercase();
    if fmt != "mjpeg" && fmt != "h264" {
        return HttpResponse::BadRequest().json(HashMap::from([
            ("error", "format must be 'h264' or 'mjpeg'")
        ]));
    }
    *fmt_guard = Some(fmt.clone());
    *session_guard = true;

    HttpResponse::Ok().json(StartStreamResponse {
        status: "stream started".into(),
        session_active: true,
        format: fmt,
    })
}

async fn stream_stop(
    state: web::Data<AppState>,
) -> impl Responder {
    let mut session_guard = state.session_active.lock().unwrap();
    *session_guard = false;
    HttpResponse::Ok().json(StopStreamResponse {
        status: "stream stopped".into(),
        session_active: false,
    })
}

async fn stream_url(
    state: web::Data<AppState>,
) -> impl Responder {
    let fmt_guard = state.stream_format.lock().unwrap();
    let fmt = fmt_guard.clone().unwrap_or_else(|| "mjpeg".into());
    let url = match fmt.as_str() {
        "h264" => build_rtsp_url(&state, "h264"),
        "mjpeg" => build_mjpeg_url(&state),
        _ => build_mjpeg_url(&state),
    };
    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: fmt,
    })
}

// This endpoint will proxy video stream as HTTP for browser consumption.
async fn stream_proxy(
    req: HttpRequest,
    state: web::Data<AppState>,
) -> Result<HttpResponse, Error> {
    let session_guard = state.session_active.lock().unwrap();
    if !*session_guard {
        return Ok(HttpResponse::BadRequest().body("Streaming session not started."));
    }
    let fmt_guard = state.stream_format.lock().unwrap();
    let fmt = fmt_guard.clone().unwrap_or_else(|| "mjpeg".to_string());

    if fmt == "mjpeg" {
        // Proxy MJPEG HTTP stream from camera
        let mjpeg_url = build_mjpeg_url(&state);
        let client = Client::new();
        let mut response = client.get(&mjpeg_url)
            .basic_auth(&state.camera_user, Some(&state.camera_pass))
            .send()
            .await
            .map_err(|_| actix_web::error::ErrorBadGateway("Failed to connect to camera"))?;

        if !response.status().is_success() {
            return Ok(HttpResponse::build(StatusCode::BAD_GATEWAY)
                .body("Failed to connect to camera MJPEG stream"));
        }

        let mut body = response.bytes_stream();

        let mut builder = HttpResponse::Ok();
        if let Some(content_type) = response.headers().get(header::CONTENT_TYPE) {
            builder.content_type(content_type.clone());
        } else {
            builder.content_type("multipart/x-mixed-replace; boundary=--myboundary");
        }

        let stream = async_stream::stream! {
            while let Some(item) = body.next().await {
                match item {
                    Ok(bytes) => {
                        yield Ok::<_, Error>(bytes);
                    }
                    Err(_) => break,
                }
            }
        };

        Ok(builder.streaming(stream))
    } else if fmt == "h264" {
        // Proxy RTSP stream as raw H264 over HTTP
        // Lightweight RTSP client implementation for extracting raw H264
        use rtsp::client::RtspClient;
        use rtsp::session::Session;
        use tokio::net::TcpStream;
        use std::time::Duration;

        let rtsp_url = build_rtsp_url(&state, "h264");

        // spawn RTSP client in background
        let (tx, rx) = tokio::sync::mpsc::channel::<Bytes>(50);
        let rtsp_url_clone = rtsp_url.clone();
        let user_clone = state.camera_user.clone();
        let pass_clone = state.camera_pass.clone();

        // RTSP stream extraction (minimal, only for demo)
        tokio::spawn(async move {
            let mut session = match Session::describe(&rtsp_url_clone).await {
                Ok(s) => s,
                Err(_) => { return; }
            };
            if let Err(_) = session.setup_all().await { return; }
            let mut stream = match session.play().await {
                Ok(s) => s,
                Err(_) => { return; }
            };
            while let Some(Ok(packet)) = stream.next().await {
                if let Ok(bytes) = Bytes::try_from(packet.payload) {
                    if tx.send(bytes).await.is_err() { break; }
                }
            }
        });

        let stream = async_stream::stream! {
            while let Some(bytes) = rx.recv().await {
                yield Ok::<_, Error>(bytes);
            }
        };

        Ok(HttpResponse::Ok()
            .content_type("video/h264")
            .streaming(stream))
    } else {
        Ok(HttpResponse::BadRequest().body("Unsupported format."))
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    dotenvy::dotenv().ok();

    let camera_ip = get_env_var("CAMERA_IP", None);
    let camera_rtsp_port = get_env_var("CAMERA_RTSP_PORT", Some("554")).parse().unwrap_or(554);
    let camera_http_port = get_env_var("CAMERA_HTTP_PORT", Some("80")).parse().unwrap_or(80);
    let camera_user = get_env_var("CAMERA_USER", Some("admin"));
    let camera_pass = get_env_var("CAMERA_PASS", Some("12345"));
    let server_host = get_env_var("SERVER_HOST", Some("0.0.0.0"));
    let server_port = get_env_var("SERVER_PORT", Some("8080")).parse().unwrap_or(8080);

    let state = web::Data::new(AppState {
        camera_ip,
        camera_rtsp_port,
        camera_http_port,
        camera_user,
        camera_pass,
        stream_format: Arc::new(Mutex::new(None)),
        session_active: Arc::new(Mutex::new(false)),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .route("/stream/start", web::post().to(stream_start))
            .route("/stream/stop", web::post().to(stream_stop))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/view", web::get().to(stream_proxy))
    })
    .bind((server_host, server_port))?
    .run()
    .await
}