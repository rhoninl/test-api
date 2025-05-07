use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use actix_web::rt::spawn;
use actix_web::http::{header, StatusCode};
use futures::{Stream, StreamExt};
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use serde::{Serialize, Deserialize};
use once_cell::sync::Lazy;
use std::collections::HashMap;
use std::io::{self, Read};
use std::time::Duration;
use tokio::net::TcpStream;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use base64::engine::general_purpose;
use base64::Engine;

static STREAM_STATE: Lazy<Arc<Mutex<StreamSession>>> = Lazy::new(|| Arc::new(Mutex::new(StreamSession::default())));

#[derive(Default)]
struct StreamSession {
    running: bool,
    format: String,
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    success: bool,
    format: String,
    session: String,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
}

fn get_env_var(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn get_rtsp_url(format: &str) -> String {
    let ip = get_env_var("DEVICE_IP", "192.168.1.64");
    let port = get_env_var("RTSP_PORT", "554");
    let username = get_env_var("DEVICE_USERNAME", "admin");
    let password = get_env_var("DEVICE_PASSWORD", "admin123");
    match format {
        "mjpeg" => format!("rtsp://{}:{}@{}:{}/Streaming/Channels/102", username, password, ip, port),
        _ => format!("rtsp://{}:{}@{}:{}/Streaming/Channels/101", username, password, ip, port),
    }
}

// -- RTSP minimal proxy for MJPEG over HTTP (for browser) --
async fn mjpeg_proxy() -> impl Responder {
    let ss = STREAM_STATE.clone();
    {
        let session = ss.lock().unwrap();
        if !session.running || session.format != "mjpeg" {
            return HttpResponse::build(StatusCode::BAD_REQUEST).body("Stream not started or not in MJPEG mode");
        }
    }

    let rtsp_url = get_rtsp_url("mjpeg");
    let parsed = url::Url::parse(&rtsp_url).unwrap();
    let host = parsed.host_str().unwrap();
    let port = parsed.port().unwrap_or(554);
    let username = parsed.username();
    let password = parsed.password().unwrap_or("");

    let auth = general_purpose::STANDARD.encode(format!("{}:{}", username, password));

    // Open TCP connection to camera's RTSP port
    let stream = TcpStream::connect(format!("{}:{}", host, port)).await;
    if stream.is_err() {
        return HttpResponse::build(StatusCode::BAD_GATEWAY).body("Unable to connect to camera");
    }
    let mut stream = stream.unwrap();

    // Send RTSP DESCRIBE, SETUP, PLAY, etc. for MJPEG
    let mut cseq = 1;
    let mut buf = vec![0u8; 4096];

    // DESCRIBE
    let describe = format!(
        "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nAccept: application/sdp\r\n\r\n", 
        rtsp_url, cseq, auth
    );
    cseq += 1;
    stream.write_all(describe.as_bytes()).await.ok();
    let n = stream.read(&mut buf).await.unwrap_or(0);
    let resp = String::from_utf8_lossy(&buf[..n]);
    if !resp.contains("200 OK") {
        return HttpResponse::build(StatusCode::BAD_GATEWAY).body("RTSP DESCRIBE failed");
    }
    // Find control URL for MJPEG stream (simplified, assume it's the same as the RTSP URL)
    let control_url = rtsp_url.clone();

    // SETUP
    let setup = format!(
        "SETUP {}/trackID=2 RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nTransport: RTP/AVP;unicast;client_port=8000-8001\r\n\r\n",
        control_url, cseq, auth
    );
    cseq += 1;
    stream.write_all(setup.as_bytes()).await.ok();
    let n = stream.read(&mut buf).await.unwrap_or(0);
    let resp = String::from_utf8_lossy(&buf[..n]);
    if !resp.contains("200 OK") {
        return HttpResponse::build(StatusCode::BAD_GATEWAY).body("RTSP SETUP failed");
    }
    // Find Session header
    let session_line = resp.lines().find(|l| l.starts_with("Session:"));
    let session_id = if let Some(line) = session_line {
        line.split(':').nth(1).unwrap_or("").trim().split(';').next().unwrap_or("")
    } else { "" };

    // PLAY
    let play = format!(
        "PLAY {} RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nSession: {}\r\n\r\n",
        control_url, cseq, auth, session_id
    );
    stream.write_all(play.as_bytes()).await.ok();

    // Now, stream RTP packets, extract MJPEG, and serve as multipart/x-mixed-replace
    let (mut reader, _) = stream.into_split();
    let mjpeg_stream = async_stream::stream! {
        let boundary = "frame";
        yield Ok::<_, actix_web::Error>(web::Bytes::from(format!(
            "HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary={}\r\n\r\n", boundary
        )));
        let mut rtp_buffer = vec![0u8; 2048];
        loop {
            let n = match reader.read(&mut rtp_buffer).await {
                Ok(n) => n,
                Err(_) => break,
            };
            if n == 0 { break; }
            // Minimal RTP/MJPEG extraction for browser demo (not robust)
            // Find JPEG SOI
            if let Some(pos) = rtp_buffer[..n].windows(2).position(|w| w == [0xFF, 0xD8]) {
                if let Some(eoi) = rtp_buffer[pos..n].windows(2).position(|w| w == [0xFF, 0xD9]) {
                    let jpeg = &rtp_buffer[pos..pos+eoi+2];
                    let frame = format!(
                        "--{}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                        boundary, jpeg.len()
                    );
                    yield Ok(web::Bytes::from(frame));
                    yield Ok(web::Bytes::copy_from_slice(jpeg));
                    yield Ok(web::Bytes::from("\r\n"));
                }
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    };
    HttpResponse::Ok()
        .content_type("multipart/x-mixed-replace; boundary=frame")
        .streaming(mjpeg_stream)
}

// -- RTSP to HTTP proxy for H264 (remux as raw, browser can play in some cases with MediaSource) --
async fn h264_proxy() -> impl Responder {
    let ss = STREAM_STATE.clone();
    {
        let session = ss.lock().unwrap();
        if !session.running || session.format != "h264" {
            return HttpResponse::build(StatusCode::BAD_REQUEST).body("Stream not started or not in H264 mode");
        }
    }

    let rtsp_url = get_rtsp_url("h264");
    let parsed = url::Url::parse(&rtsp_url).unwrap();
    let host = parsed.host_str().unwrap();
    let port = parsed.port().unwrap_or(554);
    let username = parsed.username();
    let password = parsed.password().unwrap_or("");

    let auth = general_purpose::STANDARD.encode(format!("{}:{}", username, password));

    // Open TCP connection to camera's RTSP port
    let stream = TcpStream::connect(format!("{}:{}", host, port)).await;
    if stream.is_err() {
        return HttpResponse::build(StatusCode::BAD_GATEWAY).body("Unable to connect to camera");
    }
    let mut stream = stream.unwrap();

    // Send RTSP DESCRIBE, SETUP, PLAY, etc. for main stream
    let mut cseq = 1;
    let mut buf = vec![0u8; 4096];

    // DESCRIBE
    let describe = format!(
        "DESCRIBE {} RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nAccept: application/sdp\r\n\r\n", 
        rtsp_url, cseq, auth
    );
    cseq += 1;
    stream.write_all(describe.as_bytes()).await.ok();
    let n = stream.read(&mut buf).await.unwrap_or(0);
    let resp = String::from_utf8_lossy(&buf[..n]);
    if !resp.contains("200 OK") {
        return HttpResponse::build(StatusCode::BAD_GATEWAY).body("RTSP DESCRIBE failed");
    }
    // For demo, main stream control is the RTSP URL
    let control_url = rtsp_url.clone();

    // SETUP
    let setup = format!(
        "SETUP {}/trackID=1 RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nTransport: RTP/AVP;unicast;client_port=9000-9001\r\n\r\n",
        control_url, cseq, auth
    );
    cseq += 1;
    stream.write_all(setup.as_bytes()).await.ok();
    let n = stream.read(&mut buf).await.unwrap_or(0);
    let resp = String::from_utf8_lossy(&buf[..n]);
    if !resp.contains("200 OK") {
        return HttpResponse::build(StatusCode::BAD_GATEWAY).body("RTSP SETUP failed");
    }
    let session_line = resp.lines().find(|l| l.starts_with("Session:"));
    let session_id = if let Some(line) = session_line {
        line.split(':').nth(1).unwrap_or("").trim().split(';').next().unwrap_or("")
    } else { "" };

    // PLAY
    let play = format!(
        "PLAY {} RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\nSession: {}\r\n\r\n",
        control_url, cseq, auth, session_id
    );
    stream.write_all(play.as_bytes()).await.ok();

    let (mut reader, _) = stream.into_split();

    let h264_stream = async_stream::stream! {
        let mut rtp_buffer = vec![0u8; 2048];
        loop {
            let n = match reader.read(&mut rtp_buffer).await {
                Ok(n) => n,
                Err(_) => break,
            };
            if n == 0 { break; }
            // Minimal RTP/H264 extraction (not robust, just dump payload for demo)
            // Find NAL start (0x00 00 00 01)
            if let Some(pos) = rtp_buffer[..n].windows(4).position(|w| w == [0,0,0,1]) {
                let nal = &rtp_buffer[pos..n];
                yield Ok(web::Bytes::copy_from_slice(nal));
            }
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
    };
    HttpResponse::Ok()
        .content_type("video/h264")
        .streaming(h264_stream)
}

// -- API Endpoints --

async fn start_stream(req: web::Json<StartStreamRequest>) -> impl Responder {
    let mut session = STREAM_STATE.lock().unwrap();
    if session.running {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream already running");
    }
    let format = req.format.clone().unwrap_or("h264".to_string());
    if format != "h264" && format != "mjpeg" {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Unsupported format");
    }
    session.running = true;
    session.format = format.clone();
    HttpResponse::Ok().json(StartStreamResponse {
        success: true,
        format,
        session: "cam-session-1".to_string(),
    })
}

async fn stop_stream(_req: HttpRequest) -> impl Responder {
    let mut session = STREAM_STATE.lock().unwrap();
    session.running = false;
    session.format = String::new();
    HttpResponse::Ok().body("Stream stopped")
}

async fn stream_url(_req: HttpRequest) -> impl Responder {
    let session = STREAM_STATE.lock().unwrap();
    if !session.running {
        return HttpResponse::build(StatusCode::BAD_REQUEST)
            .body("Stream not started");
    }
    let host = get_env_var("SERVER_HOST", "0.0.0.0");
    let port = get_env_var("SERVER_PORT", "8080");
    let path = match session.format.as_str() {
        "mjpeg" => "/stream/mjpeg",
        _ => "/stream/h264",
    };
    let url = format!("http://{}:{}{}", host, port, path);
    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: session.format.clone(),
    })
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let server_host = get_env_var("SERVER_HOST", "0.0.0.0");
    let server_port = get_env_var("SERVER_PORT", "8080");
    let bind_addr = format!("{}:{}", server_host, server_port);

    HttpServer::new(|| {
        App::new()
            .route("/stream/start", web::post().to(start_stream))
            .route("/stream/stop", web::post().to(stop_stream))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/mjpeg", web::get().to(mjpeg_proxy))
            .route("/stream/h264", web::get().to(h264_proxy))
    })
    .bind(bind_addr)?
    .run()
    .await
}