use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder, Result};
use actix_web::http::header::{ContentType, CACHE_CONTROL, CONTENT_TYPE};
use actix_web::rt::spawn;
use bytes::Bytes;
use futures::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncReadExt};
use tokio::net::TcpStream;
use tokio::sync::mpsc::{unbounded_channel, UnboundedSender, UnboundedReceiver};
use base64::encode;

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    protocol: String,
}

#[derive(Serialize)]
struct StartStreamResponse {
    status: String,
    format: String,
    session_id: String,
}

#[derive(Serialize)]
struct StopStreamResponse {
    status: String,
    message: String,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

struct AppState {
    streaming: Mutex<bool>,
    format: Mutex<String>,
    session_id: String,
    sender: Mutex<Option<UnboundedSender<Vec<u8>>>>,
}

fn get_env(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn build_rtsp_url() -> String {
    let user = get_env("CAMERA_USERNAME", "admin");
    let pass = get_env("CAMERA_PASSWORD", "admin");
    let ip = get_env("DEVICE_IP", "192.168.1.64");
    let port = get_env("RTSP_PORT", "554");
    let channel = get_env("CAMERA_CHANNEL", "101");
    format!("rtsp://{}:{}@{}:{}/Streaming/Channels/{}", user, pass, ip, port, channel)
}

fn build_mjpeg_url() -> String {
    let user = get_env("CAMERA_USERNAME", "admin");
    let pass = get_env("CAMERA_PASSWORD", "admin");
    let ip = get_env("DEVICE_IP", "192.168.1.64");
    let port = get_env("HTTP_PORT", "80");
    let channel = get_env("CAMERA_CHANNEL", "101");
    format!("http://{}:{}@{}:{}/Streaming/channels/{}/httpPreview", user, pass, ip, port, channel)
}

async fn stream_url_handler(state: web::Data<Arc<AppState>>) -> Result<impl Responder> {
    let format = state.format.lock().unwrap().clone();
    let (url, protocol) = match format.as_str() {
        "mjpeg" => (build_mjpeg_url(), "MJPEG"),
        _ => (build_rtsp_url(), "RTSP"),
    };
    Ok(web::Json(StreamUrlResponse {
        url,
        protocol: protocol.to_string(),
    }))
}

async fn start_stream_handler(
    state: web::Data<Arc<AppState>>,
    req: web::Json<StartStreamRequest>,
) -> Result<impl Responder> {
    let mut streaming = state.streaming.lock().unwrap();
    if *streaming {
        return Ok(web::Json(StartStreamResponse {
            status: "already running".into(),
            format: state.format.lock().unwrap().clone(),
            session_id: state.session_id.clone(),
        }));
    }
    let format = req.format.clone().unwrap_or_else(|| "mjpeg".into()).to_lowercase();
    *state.format.lock().unwrap() = format.clone();
    *streaming = true;
    Ok(web::Json(StartStreamResponse {
        status: "started".into(),
        format,
        session_id: state.session_id.clone(),
    }))
}

async fn stop_stream_handler(state: web::Data<Arc<AppState>>) -> Result<impl Responder> {
    let mut streaming = state.streaming.lock().unwrap();
    let mut sender = state.sender.lock().unwrap();
    *streaming = false;
    if let Some(tx) = sender.take() {
        let _ = tx.send(vec![]); // signal to close
    }
    Ok(web::Json(StopStreamResponse {
        status: "stopped".into(),
        message: "Streaming session ended.".into(),
    }))
}

async fn video_stream_handler(
    req: HttpRequest,
    state: web::Data<Arc<AppState>>,
) -> Result<HttpResponse> {
    let is_running = *state.streaming.lock().unwrap();
    if !is_running {
        return Ok(HttpResponse::NotFound().body("Streaming not started. POST /stream/start first."));
    }
    let format = state.format.lock().unwrap().clone();
    match format.as_str() {
        "mjpeg" => mjpeg_stream(state).await,
        "h264" => h264_stream(state).await,
        _ => Ok(HttpResponse::BadRequest().body("Unknown format")),
    }
}

async fn mjpeg_stream(state: web::Data<Arc<AppState>>) -> Result<HttpResponse> {
    let url = build_mjpeg_url();
    let url_parts = url::Url::parse(&url).expect("Invalid MJPEG URL");
    let user = url_parts.username();
    let pass = url_parts.password().unwrap_or("");
    let mut headers = reqwest::header::HeaderMap::new();
    if !user.is_empty() {
        let auth = format!("Basic {}", encode(format!("{}:{}", user, pass)));
        headers.insert(reqwest::header::AUTHORIZATION, auth.parse().unwrap());
    }
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap();

    let resp = client
        .get(url)
        .headers(headers)
        .send()
        .await
        .map_err(|_| actix_web::error::ErrorInternalServerError("MJPEG connect error"))?;

    let stream = resp.bytes_stream();
    let boundary = "--myboundary";
    let content_type = format!("multipart/x-mixed-replace;boundary={}", &boundary[2..]);

    let s = stream.map(move |chunk| match chunk {
        Ok(bytes) => {
            let mut v = Vec::new();
            v.extend_from_slice(boundary.as_bytes());
            v.extend_from_slice(b"\r\nContent-Type: image/jpeg\r\n\r\n");
            v.extend_from_slice(&bytes);
            v.extend_from_slice(b"\r\n");
            Ok::<Bytes, actix_web::Error>(Bytes::from(v))
        }
        Err(_) => Ok(Bytes::from_static(b"")),
    });

    Ok(HttpResponse::Ok()
        .insert_header((CONTENT_TYPE, content_type))
        .insert_header((CACHE_CONTROL, "no-cache"))
        .streaming(s))
}

async fn h264_stream(state: web::Data<Arc<AppState>>) -> Result<HttpResponse> {
    let url = build_rtsp_url();
    let rtsp_addr = url.replace("rtsp://", "");
    let parts: Vec<&str> = rtsp_addr.split('@').collect();
    let (auth_part, addr_part) = if parts.len() == 2 {
        (parts[0], parts[1])
    } else {
        ("", parts[0])
    };
    let addr_parts: Vec<&str> = addr_part.split('/').collect();
    let host_port = addr_parts[0];
    let path = &addr_part[host_port.len()..];

    let mut stream = TcpStream::connect(host_port)
        .await
        .map_err(|_| actix_web::error::ErrorInternalServerError("RTSP TCP connect error"))?;

    // Compose RTSP DESCRIBE and SETUP/PLAY commands.
    let describe = format!(
        "DESCRIBE rtsp://{}{} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n",
        host_port, path
    );
    let setup = format!(
        "SETUP rtsp://{}{} RTSP/1.0\r\nCSeq: 3\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n",
        host_port, path
    );
    let play = format!(
        "PLAY rtsp://{}{} RTSP/1.0\r\nCSeq: 4\r\nRange: npt=0.000-\r\n",
        host_port, path
    );

    // Auth header
    let auth_header = if !auth_part.is_empty() {
        let creds = format!("Basic {}", encode(auth_part));
        format!("Authorization: {}\r\n", creds)
    } else {
        "".to_string()
    };

    let describe = format!("{}{}", describe, auth_header);
    let setup = format!("{}{}", setup, auth_header);
    let play = format!("{}{}", play, auth_header);

    // Send RTSP handshake sequence.
    stream
        .write_all(describe.as_bytes())
        .await
        .map_err(|_| actix_web::error::ErrorInternalServerError("RTSP DESCRIBE send error"))?;
    let mut buf = [0u8; 4096];
    let _ = stream.read(&mut buf).await?;

    stream
        .write_all(setup.as_bytes())
        .await
        .map_err(|_| actix_web::error::ErrorInternalServerError("RTSP SETUP send error"))?;
    let _ = stream.read(&mut buf).await?;

    stream
        .write_all(play.as_bytes())
        .await
        .map_err(|_| actix_web::error::ErrorInternalServerError("RTSP PLAY send error"))?;
    let _ = stream.read(&mut buf).await?;

    // Now, stream RTP-over-TCP packets as raw H264.
    let (tx, mut rx): (UnboundedSender<Vec<u8>>, UnboundedReceiver<Vec<u8>>) = unbounded_channel();
    {
        let mut sender = state.sender.lock().unwrap();
        *sender = Some(tx);
    }

    let mut stream_clone = stream.try_clone().unwrap();
    spawn(async move {
        let mut local_buf = vec![0u8; 2048];
        loop {
            match stream_clone.read(&mut local_buf).await {
                Ok(n) if n > 0 => {
                    let _ = rx.send(local_buf[..n].to_vec());
                }
                _ => break,
            }
        }
    });

    let payload = async_stream::stream! {
        while let Some(chunk) = rx.recv().await {
            if chunk.is_empty() { break; }
            yield Ok::<Bytes, actix_web::Error>(Bytes::from(chunk));
        }
    };

    Ok(HttpResponse::Ok()
        .insert_header((CONTENT_TYPE, "video/h264"))
        .insert_header((CACHE_CONTROL, "no-cache"))
        .streaming(Box::pin(payload) as Pin<Box<dyn Stream<Item = Result<Bytes, actix_web::Error>> + Send>>))
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let http_host = get_env("SERVER_HOST", "0.0.0.0");
    let http_port = get_env("SERVER_PORT", "8080");

    let state = Arc::new(AppState {
        streaming: Mutex::new(false),
        format: Mutex::new("mjpeg".into()),
        session_id: uuid::Uuid::new_v4().to_string(),
        sender: Mutex::new(None),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(state.clone()))
            .route("/stream/url", web::get().to(stream_url_handler))
            .route("/stream/start", web::post().to(start_stream_handler))
            .route("/stream/stop", web::post().to(stop_stream_handler))
            .route("/stream/video", web::get().to(video_stream_handler))
    })
    .bind(format!("{}:{}", http_host, http_port))?
    .run()
    .await
}