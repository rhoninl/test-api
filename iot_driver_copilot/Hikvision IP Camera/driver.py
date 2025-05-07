use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder, post, get};
use actix_web::http::{header, StatusCode};
use std::env;
use std::sync::{Arc, Mutex};
use std::collections::HashMap;
use futures_util::stream::StreamExt;
use serde::{Deserialize, Serialize};
use tokio::net::TcpStream;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use base64::engine::general_purpose::STANDARD as base64_engine;

#[derive(Clone)]
struct AppState {
    streaming_sessions: Arc<Mutex<HashMap<String, StreamingSession>>>,
    camera_cfg: CameraConfig,
}

#[derive(Clone)]
struct CameraConfig {
    ip: String,
    rtsp_port: u16,
    username: String,
    password: String,
    channel: String,
    stream: String,
}

#[derive(Clone, Serialize, Deserialize)]
struct StreamingSession {
    id: String,
    format: String,
    active: bool,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

fn get_env<T: std::str::FromStr>(key: &str, default: T) -> T {
    env::var(key)
        .ok()
        .and_then(|val| val.parse::<T>().ok())
        .unwrap_or(default)
}

fn build_rtsp_url(cfg: &CameraConfig) -> String {
    format!(
        "rtsp://{}:{}@{}:{}/Streaming/Channels/{}/{}",
        cfg.username,
        cfg.password,
        cfg.ip,
        cfg.rtsp_port,
        cfg.channel,
        cfg.stream
    )
}

#[post("/stream/start")]
async fn start_stream(
    data: web::Data<AppState>,
    body: web::Json<StartStreamRequest>,
) -> impl Responder {
    let format = body.format.clone().unwrap_or_else(|| "mjpeg".to_string());
    let session_id = uuid::Uuid::new_v4().to_string();
    let session = StreamingSession {
        id: session_id.clone(),
        format: format.clone(),
        active: true,
    };

    {
        let mut sessions = data.streaming_sessions.lock().unwrap();
        sessions.insert(session_id.clone(), session.clone());
    }

    HttpResponse::Ok().json(session)
}

#[post("/stream/stop")]
async fn stop_stream(
    data: web::Data<AppState>,
    req: HttpRequest,
) -> impl Responder {
    let query: HashMap<String, String> = req
        .query_string()
        .split('&')
        .filter_map(|kv| {
            let mut parts = kv.splitn(2, '=');
            let k = parts.next()?;
            let v = parts.next()?;
            Some((k.to_string(), v.to_string()))
        })
        .collect();

    let session_id = query.get("session_id");
    if session_id.is_none() {
        return HttpResponse::BadRequest().body("Missing session_id query parameter");
    }
    let session_id = session_id.unwrap();

    let mut sessions = data.streaming_sessions.lock().unwrap();
    if let Some(mut session) = sessions.get_mut(session_id) {
        session.active = false;
        sessions.remove(session_id);
        HttpResponse::Ok().json(serde_json::json!({"status": "stopped"}))
    } else {
        HttpResponse::NotFound().body("Session not found")
    }
}

#[get("/stream/url")]
async fn get_stream_url(
    data: web::Data<AppState>,
    req: HttpRequest,
) -> impl Responder {
    let format = req
        .query_string()
        .split('&')
        .filter_map(|kv| {
            let mut parts = kv.splitn(2, '=');
            let k = parts.next()?;
            let v = parts.next()?;
            if k == "format" {
                Some(v.to_string())
            } else {
                None
            }
        })
        .next()
        .unwrap_or("mjpeg".to_string());

    let url = match format.as_str() {
        "h264" => build_rtsp_url(&data.camera_cfg),
        "mjpeg" => format!(
            "http://{}:{}/stream/mjpeg",
            get_env::<String>("SERVER_HOST", "0.0.0.0".to_string()),
            get_env::<u16>("SERVER_PORT", 8080)
        ),
        _ => build_rtsp_url(&data.camera_cfg),
    };
    HttpResponse::Ok().json(serde_json::json!({ "url": url }))
}

#[get("/stream/mjpeg")]
async fn mjpeg_proxy(
    data: web::Data<AppState>,
) -> impl Responder {
    let rtsp_url = build_rtsp_url(&data.camera_cfg);

    let boundary = "mjpegstream";
    let mut res = HttpResponse::Ok();
    res.insert_header((header::CONTENT_TYPE, format!("multipart/x-mixed-replace; boundary={}", boundary)));

    res.streaming(mjpeg_stream(rtsp_url, boundary.to_string()))
}

async fn mjpeg_stream(rtsp_url: String, boundary: String) -> impl futures_core::Stream<Item = Result<bytes::Bytes, actix_web::Error>> {
    use bytes::Bytes;
    use tokio::sync::mpsc;

    let (mut tx, rx) = mpsc::channel::<Result<Bytes, actix_web::Error>>(8);

    // Spawn a background task to pull frames from RTSP and send as MJPEG
    tokio::spawn(async move {
        // Connect to RTSP and parse frames (simplified: this is a placeholder;
        // in real code, use a H.264/MJPEG parser; here we simulate MJPEG)
        // For demo: use HTTP MJPEG stream if camera supports, else error
        let http_mjpeg_url = rtsp_url.replace("rtsp://", "http://").replace("/Streaming/Channels/", "/Streaming/channels/") + "/picture";
        let client = reqwest::Client::builder().danger_accept_invalid_certs(true).build().unwrap();
        let req = client
            .get(&http_mjpeg_url)
            .basic_auth(
                env::var("CAMERA_USERNAME").unwrap_or("admin".to_string()),
                Some(env::var("CAMERA_PASSWORD").unwrap_or("12345".to_string())),
            )
            .send()
            .await;
        if let Ok(mut resp) = req {
            let mut stream = resp.bytes_stream();
            while let Some(chunk) = stream.next().await {
                match chunk {
                    Ok(bytes) => {
                        // Wrap the JPEG frame in multipart boundary
                        let mut part = format!("\r\n--{}\r\nContent-Type: image/jpeg\r\n\r\n", &boundary)
                            .into_bytes();
                        part.extend_from_slice(&bytes);
                        if tx.send(Ok(Bytes::from(part))).await.is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        } else {
            let _ = tx
                .send(Ok(Bytes::from_static(b"\r\n--mjpegstream\r\nContent-Type: text/plain\r\n\r\nCamera MJPEG not available")))
                .await;
        }
    });

    tokio_stream::wrappers::ReceiverStream::new(rx)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    dotenv::dotenv().ok();

    let camera_cfg = CameraConfig {
        ip: get_env::<String>("CAMERA_IP", "192.168.1.64".to_string()),
        rtsp_port: get_env::<u16>("CAMERA_RTSP_PORT", 554),
        username: get_env::<String>("CAMERA_USERNAME", "admin".to_string()),
        password: get_env::<String>("CAMERA_PASSWORD", "12345".to_string()),
        channel: get_env::<String>("CAMERA_CHANNEL", "101".to_string()),
        stream: get_env::<String>("CAMERA_STREAM", "".to_string()),
    };

    let state = AppState {
        streaming_sessions: Arc::new(Mutex::new(HashMap::new())),
        camera_cfg,
    };
    let server_host = get_env::<String>("SERVER_HOST", "0.0.0.0".to_string());
    let server_port = get_env::<u16>("SERVER_PORT", 8080);

    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(state.clone()))
            .service(start_stream)
            .service(stop_stream)
            .service(get_stream_url)
            .service(mjpeg_proxy)
    })
    .bind((server_host.as_str(), server_port))?
    .run()
    .await
}