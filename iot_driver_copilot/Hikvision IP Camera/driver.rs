use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder, middleware::Logger};
use actix_web::http::{header, StatusCode};
use actix_web::rt::spawn;
use futures::Stream;
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use std::collections::HashMap;
use std::io::Error as IoError;
use bytes::Bytes;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::mpsc::{self, Sender, Receiver};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>, // "h264" or "mjpeg"
}

#[derive(Serialize)]
struct StartStreamResponse {
    session: String,
    format: String,
    status: String,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
}

#[derive(Clone)]
struct AppState {
    device_ip: String,
    device_rtsp_port: u16,
    device_http_port: u16,
    username: String,
    password: String,
    sessions: Arc<Mutex<HashMap<String, StreamSession>>>,
}

#[derive(Clone)]
struct StreamSession {
    format: String,
    active: bool,
}

fn get_env(key: &str, def: &str) -> String {
    env::var(key).unwrap_or_else(|_| def.to_string())
}

fn get_env_u16(key: &str, def: u16) -> u16 {
    env::var(key)
        .ok()
        .and_then(|x| x.parse().ok())
        .unwrap_or(def)
}

fn make_rtsp_url(ip: &str, port: u16, user: &str, pass: &str, channel: u8, format: &str) -> String {
    let path = match format.to_lowercase().as_str() {
        "mjpeg" => format!("Streaming/channels/{}02", channel), // MJPEG substream
        _ => format!("Streaming/channels/{}01", channel), // H.264 mainstream
    };
    format!(
        "rtsp://{}:{}@{}:{}/{}",
        user, pass, ip, port, path
    )
}

fn make_mjpeg_url(ip: &str, port: u16, user: &str, pass: &str, channel: u8) -> String {
    // Using HTTP MJPEG stream (if supported)
    format!(
        "http://{}:{}@{}:{}/Streaming/channels/{}02/httpPreview",
        user, pass, ip, port, channel
    )
}

fn gen_session_id() -> String {
    use rand::{thread_rng, RngCore};
    let mut buf = [0u8; 16];
    thread_rng().fill_bytes(&mut buf);
    base64::encode_config(&buf, base64::URL_SAFE_NO_PAD)
}

async fn start_stream(
    data: web::Data<AppState>,
    req: web::Json<StartStreamRequest>,
) -> impl Responder {
    let format = req.format.clone().unwrap_or_else(|| "h264".to_string()).to_lowercase();
    if format != "h264" && format != "mjpeg" {
        return HttpResponse::BadRequest().body("Invalid format");
    }

    let session_id = gen_session_id();
    {
        let mut sessions = data.sessions.lock().unwrap();
        sessions.insert(session_id.clone(), StreamSession { format: format.clone(), active: true });
    }

    HttpResponse::Ok().json(StartStreamResponse {
        session: session_id,
        format: format,
        status: "started".to_string(),
    })
}

async fn stop_stream(
    data: web::Data<AppState>,
    req: HttpRequest,
) -> impl Responder {
    // Optionally get session info from header/body for advanced clients
    let session_id = req
        .headers()
        .get("x-session-id")
        .and_then(|val| val.to_str().ok())
        .map(|s| s.to_string());

    let mut sessions = data.sessions.lock().unwrap();
    let mut stopped_any = false;
    if let Some(sid) = session_id {
        if let Some(sess) = sessions.get_mut(&sid) {
            sess.active = false;
            stopped_any = true;
        }
    } else {
        for (_k, sess) in sessions.iter_mut() {
            if sess.active {
                sess.active = false;
                stopped_any = true;
            }
        }
    }

    if stopped_any {
        HttpResponse::Ok().body("Stream(s) stopped")
    } else {
        HttpResponse::NotFound().body("No active stream found")
    }
}

async fn get_stream_url(
    data: web::Data<AppState>,
) -> impl Responder {
    let format = "h264"; // For demo, return h264 url
    let url = make_rtsp_url(
        &data.device_ip,
        data.device_rtsp_port,
        &data.username,
        &data.password,
        1,
        format,
    );
    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: format.to_string(),
    })
}

// Stream RTSP over HTTP as MJPEG (browser-friendly)
async fn stream_video(
    data: web::Data<AppState>,
    req: HttpRequest,
) -> impl Responder {
    // Accept ?format=h264/mjpeg
    let format = req.query_string()
        .split('&')
        .find_map(|kv| {
            let mut s = kv.splitn(2, '=');
            if let (Some(k), Some(v)) = (s.next(), s.next()) {
                if k == "format" { Some(v.to_lowercase()) } else { None }
            } else { None }
        }).unwrap_or_else(|| "mjpeg".to_string());

    if format == "h264" {
        // Not directly browser-playable, but return as octet-stream for ffmpeg/vlc
        let (tx, rx) = mpsc::channel::<Result<Bytes, IoError>>(32);
        let app = data.clone();
        spawn(async move {
            if let Err(e) = proxy_rtsp_to_http(&app, tx, "h264").await {
                eprintln!("stream error: {}", e);
            }
        });
        HttpResponse::Ok()
            .content_type("application/octet-stream")
            .insert_header((header::CACHE_CONTROL, "no-cache"))
            .streaming(MpscStream { rx })
    } else {
        // Try to stream MJPEG over HTTP, if camera supports it
        let (tx, rx) = mpsc::channel::<Result<Bytes, IoError>>(32);
        let app = data.clone();
        spawn(async move {
            if let Err(e) = proxy_mjpeg_http(&app, tx).await {
                eprintln!("stream error: {}", e);
            }
        });
        HttpResponse::Ok()
            .content_type("multipart/x-mixed-replace; boundary=--boundary")
            .insert_header((header::CACHE_CONTROL, "no-cache"))
            .streaming(MpscStream { rx })
    }
}

struct MpscStream {
    rx: Receiver<Result<Bytes, IoError>>,
}

impl Stream for MpscStream {
    type Item = Result<Bytes, IoError>;
    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let rx = unsafe { self.map_unchecked_mut(|s| &mut s.rx) };
        rx.poll_recv(cx)
    }
}

// Proxy RTSP stream's payload over HTTP (for H.264). For demo, we do a TCP connect to RTSP server and forward the raw stream.
// In production, you'd parse RTSP and RTP, reconstruct frames, and re-encapsulate for browser. Here we proxy raw payload for ffmpeg etc.
async fn proxy_rtsp_to_http(
    data: &AppState,
    mut tx: Sender<Result<Bytes, IoError>>,
    format: &str,
) -> Result<(), IoError> {
    let url = make_rtsp_url(
        &data.device_ip,
        data.device_rtsp_port,
        &data.username,
        &data.password,
        1,
        format,
    );
    let parts: Vec<&str> = url[7..].split('@').collect();
    let (auth, addr) = if parts.len() == 2 {
        (parts[0], parts[1])
    } else {
        ("", parts[0])
    };
    let mut addr_parts = addr.split('/');
    let host_port = addr_parts.next().unwrap_or("");
    let host_port = host_port.split(':').collect::<Vec<_>>();
    let host = host_port[0];
    let port = host_port.get(1).and_then(|x| x.parse().ok()).unwrap_or(554);

    let mut stream = TcpStream::connect(format!("{}:{}", host, port)).await?;

    // RTSP commands
    let username = &data.username;
    let password = &data.password;
    let path = format!("/Streaming/channels/101");
    let mut cseq = 1;

    // Send DESCRIBE
    let describe = format!(
        "DESCRIBE rtsp://{}:{}@{}:{}{} RTSP/1.0\r\nCSeq: {}\r\nAccept: application/sdp\r\nAuthorization: Basic {}\r\n\r\n",
        username, password, host, port, &path, cseq,
        base64::encode(format!("{}:{}", username, password))
    );
    stream.write_all(describe.as_bytes()).await?;
    let _ = read_rtsp_response(&mut stream).await?;

    cseq += 1;
    // Send SETUP (TCP interleaved)
    let setup = format!(
        "SETUP rtsp://{}:{}@{}:{}{}/trackID=1 RTSP/1.0\r\nCSeq: {}\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nAuthorization: Basic {}\r\n\r\n",
        username, password, host, port, &path, cseq,
        base64::encode(format!("{}:{}", username, password))
    );
    stream.write_all(setup.as_bytes()).await?;
    let _ = read_rtsp_response(&mut stream).await?;

    cseq += 1;
    // Send PLAY
    let play = format!(
        "PLAY rtsp://{}:{}@{}:{}{} RTSP/1.0\r\nCSeq: {}\r\nAuthorization: Basic {}\r\n\r\n",
        username, password, host, port, &path, cseq,
        base64::encode(format!("{}:{}", username, password))
    );
    stream.write_all(play.as_bytes()).await?;
    let _ = read_rtsp_response(&mut stream).await?;

    // Now, forward interleaved RTP packets as raw bytes
    let mut buf = [0u8; 4096];
    loop {
        let n = stream.read(&mut buf).await?;
        if n == 0 {
            break;
        }
        if tx.send(Ok(Bytes::copy_from_slice(&buf[..n]))).await.is_err() {
            break;
        }
    }
    Ok(())
}

// Read RTSP response until double CRLF
async fn read_rtsp_response(stream: &mut TcpStream) -> Result<String, IoError> {
    let mut buf = vec![];
    let mut state = 0;
    loop {
        let mut b = [0u8; 1];
        let n = stream.read(&mut b).await?;
        if n == 0 { break; }
        buf.push(b[0]);
        match state {
            0..=2 if b[0] == b"\r\n"[state % 2] => state += 1,
            3 if b[0] == b'\n' => break,
            _ => state = 0,
        }
    }
    Ok(String::from_utf8_lossy(&buf).to_string())
}

// Proxy MJPEG HTTP stream, if supported by camera
async fn proxy_mjpeg_http(
    data: &AppState,
    mut tx: Sender<Result<Bytes, IoError>>,
) -> Result<(), IoError> {
    let url = make_mjpeg_url(
        &data.device_ip,
        data.device_http_port,
        &data.username,
        &data.password,
        1
    );
    let parsed = url::Url::parse(&url).unwrap();
    let host = parsed.host_str().unwrap();
    let port = parsed.port().unwrap_or(80);
    let path = parsed.path().to_string();
    let userpass = format!("{}:{}", data.username, data.password);
    let auth = base64::encode(userpass);

    let mut stream = TcpStream::connect(format!("{}:{}", host, port)).await?;

    let req = format!(
        "GET {} HTTP/1.0\r\nHost: {}\r\nAuthorization: Basic {}\r\n\r\n",
        path, host, auth
    );
    stream.write_all(req.as_bytes()).await?;

    // Read headers until double CRLF
    let mut headers = Vec::new();
    let mut buf = [0u8; 1];
    let mut last4 = [0u8; 4];
    let mut idx = 0;
    loop {
        let n = stream.read(&mut buf).await?;
        if n == 0 { break; }
        headers.push(buf[0]);
        last4[idx % 4] = buf[0];
        idx += 1;
        if &last4 == b"\r\n\r\n" { break; }
    }
    // Now, forward everything as-is (MJPEG stream)
    let mut fwd_buf = [0u8; 4096];
    loop {
        let n = stream.read(&mut fwd_buf).await?;
        if n == 0 { break; }
        if tx.send(Ok(Bytes::copy_from_slice(&fwd_buf[..n]))).await.is_err() {
            break;
        }
    }
    Ok(())
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    env_logger::init();

    let host = get_env("SERVER_HOST", "0.0.0.0");
    let port = get_env_u16("SERVER_PORT", 8080);
    let device_ip = get_env("DEVICE_IP", "192.168.1.64");
    let device_rtsp_port = get_env_u16("DEVICE_RTSP_PORT", 554);
    let device_http_port = get_env_u16("DEVICE_HTTP_PORT", 80);
    let username = get_env("DEVICE_USERNAME", "admin");
    let password = get_env("DEVICE_PASSWORD", "12345");

    let app_state = web::Data::new(AppState {
        device_ip,
        device_rtsp_port,
        device_http_port,
        username,
        password,
        sessions: Arc::new(Mutex::new(HashMap::new())),
    });

    HttpServer::new(move || {
        App::new()
            .app_data(app_state.clone())
            .wrap(Logger::default())
            .route("/stream/start", web::post().to(start_stream))
            .route("/stream/stop", web::post().to(stop_stream))
            .route("/stream/url", web::get().to(get_stream_url))
            .route("/stream/video", web::get().to(stream_video))
    })
    .bind((host.as_str(), port))?
    .run()
    .await
}