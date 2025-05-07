use actix_web::{web, App, HttpResponse, HttpServer, Responder, post, get};
use actix_web::http::{header, StatusCode};
use std::env;
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};
use futures_util::StreamExt;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use base64::engine::general_purpose;
use base64::Engine;
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    session: String,
    format: String,
    status: String,
}

#[derive(Serialize)]
struct StopStreamResponse {
    status: String,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
}

struct AppState {
    streaming: AtomicBool,
    stream_format: Mutex<String>,
}

fn get_env(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn get_rtsp_url() -> String {
    let ip = get_env("DEVICE_IP", "192.168.1.64");
    let port = get_env("RTSP_PORT", "554");
    let user = get_env("DEVICE_USER", "admin");
    let pass = get_env("DEVICE_PASS", "12345");
    let channel = get_env("CAMERA_CHANNEL", "101");
    format!("rtsp://{}:{}@{}:{}/Streaming/Channels/{}", user, pass, ip, port, channel)
}

fn get_mjpeg_url() -> String {
    let ip = get_env("DEVICE_IP", "192.168.1.64");
    let port = get_env("HTTP_PORT", "80");
    let user = get_env("DEVICE_USER", "admin");
    let pass = get_env("DEVICE_PASS", "12345");
    let channel = get_env("CAMERA_CHANNEL", "1");
    // Many Hikvision cameras provide MJPEG on this path if enabled
    format!("http://{}:{}@{}:{}/Streaming/channels/{}/httpPreview", user, pass, ip, port, channel)
}

#[post("/stream/start")]
async fn start_stream(
    data: web::Data<Arc<AppState>>,
    body: web::Json<StartStreamRequest>
) -> impl Responder {
    let mut format = "mjpeg".to_string();
    if let Some(fmt) = &body.format {
        if fmt.to_lowercase() == "h264" {
            format = "h264".to_string();
        }
    }
    {
        let mut fmt_guard = data.stream_format.lock().unwrap();
        *fmt_guard = format.clone();
    }
    data.streaming.store(true, Ordering::SeqCst);
    HttpResponse::Ok().json(StartStreamResponse {
        session: "hikvision-session".to_string(),
        format,
        status: "streaming".to_string(),
    })
}

#[post("/stream/stop")]
async fn stop_stream(data: web::Data<Arc<AppState>>) -> impl Responder {
    data.streaming.store(false, Ordering::SeqCst);
    HttpResponse::Ok().json(StopStreamResponse {
        status: "stopped".to_string(),
    })
}

#[get("/stream/url")]
async fn stream_url(data: web::Data<Arc<AppState>>) -> impl Responder {
    let fmt = data.stream_format.lock().unwrap().clone();
    let url = if fmt == "h264" {
        format!("/stream/live/h264")
    } else {
        format!("/stream/live/mjpeg")
    };
    HttpResponse::Ok().json(StreamUrlResponse { url })
}

#[get("/stream/live/mjpeg")]
async fn stream_live_mjpeg(
    data: web::Data<Arc<AppState>>
) -> impl Responder {
    if !data.streaming.load(Ordering::SeqCst) {
        return HttpResponse::build(StatusCode::SERVICE_UNAVAILABLE)
            .body("Stream not started. POST /stream/start first.");
    }
    let mjpeg_url = get_mjpeg_url();
    let mut url = mjpeg_url.clone();
    // Remove credentials from url for reqwest
    let mut auth = None;
    if let Some(idx) = url.find('@') {
        if let Some(colon) = url.find("://") {
            let creds = &url[(colon+3)..idx];
            if let Some(split) = creds.find(':') {
                let user = &creds[..split];
                let pass = &creds[(split+1)..];
                auth = Some((user.to_owned(), pass.to_owned()));
            }
            url = format!("http://{}", &url[(idx+1)..]);
        }
    }
    let boundary = "hikvisionmjpeg";
    let stream = async_stream::stream! {
        let client = reqwest::Client::new();
        let req = if let Some((ref user, ref pass)) = auth {
            client.get(&url)
                .basic_auth(user, Some(pass))
                .send()
                .await
        } else {
            client.get(&url).send().await
        };
        if let Ok(mut resp) = req {
            if resp.status().is_success() {
                let mut bytes_stream = resp.bytes_stream();
                while let Some(chunk) = bytes_stream.next().await {
                    if !data.streaming.load(Ordering::SeqCst) {
                        break;
                    }
                    if let Ok(img) = chunk {
                        yield format!("\r\n--{}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n", boundary, img.len()).into_bytes();
                        yield img.to_vec();
                    } else {
                        break;
                    }
                }
            }
        }
    };
    HttpResponse::Ok()
        .insert_header((header::CONTENT_TYPE, format!("multipart/x-mixed-replace; boundary={}", boundary)))
        .streaming(stream)
}

#[get("/stream/live/h264")]
async fn stream_live_h264(
    data: web::Data<Arc<AppState>>
) -> impl Responder {
    if !data.streaming.load(Ordering::SeqCst) {
        return HttpResponse::build(StatusCode::SERVICE_UNAVAILABLE)
            .body("Stream not started. POST /stream/start first.");
    }

    let rtsp_url = get_rtsp_url();
    // Parse RTSP URL
    let url = url::Url::parse(&rtsp_url).unwrap();
    let host = url.host_str().unwrap();
    let port = url.port().unwrap_or(554);
    let user = url.username();
    let pass = url.password().unwrap_or("");

    // Connect to RTSP and proxy raw H264 as HTTP chunked octet-stream
    let stream = async_stream::stream! {
        if let Ok(mut tcp) = TcpStream::connect(format!("{}:{}", host, port)).await {
            let mut cseq = 1;
            let mut session = String::new();
            let path = format!("rtsp://{}{}", host, url.path());
            let auth_hdr = if !user.is_empty() {
                let credentials = format!("{}:{}", user, pass);
                let encoded = general_purpose::STANDARD.encode(credentials.as_bytes());
                format!("Authorization: Basic {}\r\n", encoded)
            } else {
                "".to_string()
            };
            // Send OPTIONS
            let msg = format!(
                "OPTIONS {} RTSP/1.0\r\nCSeq: {}\r\n{}User-Agent: hikvision-driver\r\n\r\n",
                path, cseq, auth_hdr
            );
            if tcp.write_all(msg.as_bytes()).await.is_err() { return; }
            let mut buf = vec![0u8; 4096];
            let n = tcp.read(&mut buf).await.unwrap_or(0);
            if n == 0 { return; }
            cseq +=1;

            // Send DESCRIBE
            let msg = format!(
                "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\n{}Accept: application/sdp\r\nUser-Agent: hikvision-driver\r\n\r\n",
                path, cseq, auth_hdr
            );
            if tcp.write_all(msg.as_bytes()).await.is_err() { return; }
            let n = tcp.read(&mut buf).await.unwrap_or(0);
            if n == 0 { return; }
            cseq += 1;

            // Send SETUP (UDP is not supported, use TCP interleaved)
            let msg = format!(
                "SETUP {} RTSP/1.0\r\nCSeq: {}\r\n{}Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nUser-Agent: hikvision-driver\r\n\r\n",
                path, cseq, auth_hdr
            );
            if tcp.write_all(msg.as_bytes()).await.is_err() { return; }
            let n = tcp.read(&mut buf).await.unwrap_or(0);
            if n == 0 { return; }
            let setup_resp = String::from_utf8_lossy(&buf[..n]);
            // Extract Session header
            for line in setup_resp.lines() {
                if line.to_ascii_lowercase().starts_with("session:") {
                    session = line.split(':').nth(1).unwrap_or("").trim().split(';').next().unwrap_or("").to_string();
                }
            }
            if session.is_empty() { return; }
            cseq += 1;

            // Send PLAY
            let msg = format!(
                "PLAY {} RTSP/1.0\r\nCSeq: {}\r\n{}Session: {}\r\nUser-Agent: hikvision-driver\r\n\r\n",
                path, cseq, auth_hdr, session
            );
            if tcp.write_all(msg.as_bytes()).await.is_err() { return; }
            let n = tcp.read(&mut buf).await.unwrap_or(0);
            if n == 0 { return; }

            // Now we should receive RTP over RTSP (interleaved)
            loop {
                if !data.streaming.load(Ordering::SeqCst) {
                    break;
                }
                // RTSP interleaved frame: $ + channel + 2-byte len + payload
                let mut header = [0u8; 4];
                if tcp.read_exact(&mut header).await.is_err() { break; }
                if header[0] != b'$' { break; }
                let length = ((header[2] as u16) << 8) | (header[3] as u16);
                let mut payload = vec![0u8; length as usize];
                if tcp.read_exact(&mut payload).await.is_err() { break; }
                // Only channel 0 is video
                if header[1] == 0 {
                    // Strip RTP header (12 bytes) and return raw H264
                    if payload.len() > 12 {
                        // This is very basic, not proper depacketization!
                        let h264 = &payload[12..];
                        yield h264.to_vec();
                    }
                }
            }
        }
    };
    HttpResponse::Ok()
        .insert_header((header::CONTENT_TYPE, "video/h264"))
        .insert_header((header::CACHE_CONTROL, "no-cache"))
        .streaming(stream)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    dotenvy::dotenv().ok();
    let server_host = get_env("SERVER_HOST", "0.0.0.0");
    let server_port = get_env("SERVER_PORT", "8080");

    let state = Arc::new(AppState {
        streaming: AtomicBool::new(false),
        stream_format: Mutex::new("mjpeg".to_string()),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(state.clone()))
            .service(start_stream)
            .service(stop_stream)
            .service(stream_url)
            .service(stream_live_mjpeg)
            .service(stream_live_h264)
    })
    .bind(format!("{}:{}", server_host, server_port))?
    .run()
    .await
}