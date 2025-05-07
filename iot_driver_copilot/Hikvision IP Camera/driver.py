use std::env;
use std::sync::{Arc, Mutex};
use std::convert::Infallible;
use std::net::SocketAddr;

use bytes::Bytes;
use futures::{Stream, StreamExt};
use hyper::header::{CONTENT_TYPE, CACHE_CONTROL};
use hyper::{Body, Request, Response, Server, Method, StatusCode};
use hyper::service::{make_service_fn, service_fn};
use serde::{Deserialize, Serialize};
use tokio::net::TcpStream;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StreamConfig {
    format: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StreamSession {
    active: bool,
    format: String,
}

#[derive(Debug, Clone)]
struct AppState {
    camera_ip: String,
    camera_rtsp_port: u16,
    camera_http_port: u16,
    camera_user: String,
    camera_pass: String,
    active_session: Arc<Mutex<Option<StreamSession>>>,
    rtsp_path: String,
    mjpeg_path: String,
}

impl AppState {
    fn get_rtsp_url(&self) -> String {
        format!(
            "rtsp://{}:{}@{}:{}/{}",
            self.camera_user,
            self.camera_pass,
            self.camera_ip,
            self.camera_rtsp_port,
            self.rtsp_path
        )
    }
    fn get_mjpeg_url(&self) -> String {
        format!(
            "http://{}:{}@{}:{}/{}",
            self.camera_user,
            self.camera_pass,
            self.camera_ip,
            self.camera_http_port,
            self.mjpeg_path
        )
    }
}

fn get_env<T: std::str::FromStr>(name: &str, default: T) -> T {
    match env::var(name) {
        Ok(val) => val.parse().unwrap_or(default),
        Err(_) => default,
    }
}

async fn handle_start_stream(
    req: Request<Body>,
    state: Arc<AppState>,
) -> Result<Response<Body>, Infallible> {
    let bytes = hyper::body::to_bytes(req.into_body()).await.unwrap_or_default();
    let config: Option<StreamConfig> = serde_json::from_slice(&bytes).ok();
    let format = config
        .and_then(|c| c.format)
        .unwrap_or_else(|| "mjpeg".to_string())
        .to_ascii_lowercase();

    if format != "mjpeg" && format != "h264" {
        return Ok(Response::builder()
            .status(StatusCode::BAD_REQUEST)
            .body(Body::from(r#"{"error":"format must be 'mjpeg' or 'h264'"}"#))
            .unwrap());
    }
    {
        let mut session = state.active_session.lock().unwrap();
        *session = Some(StreamSession {
            active: true,
            format: format.clone(),
        });
    }
    let resp = serde_json::json!({
        "success": true,
        "format": format,
        "stream_url": if format == "mjpeg" { "/video" } else { "/video" },
    });
    Ok(Response::builder()
        .header(CONTENT_TYPE, "application/json")
        .body(Body::from(resp.to_string()))
        .unwrap())
}

async fn handle_stop_stream(
    _req: Request<Body>,
    state: Arc<AppState>,
) -> Result<Response<Body>, Infallible> {
    {
        let mut session = state.active_session.lock().unwrap();
        *session = None;
    }
    let resp = serde_json::json!({
        "success": true,
        "message": "Streaming stopped."
    });
    Ok(Response::builder()
        .header(CONTENT_TYPE, "application/json")
        .body(Body::from(resp.to_string()))
        .unwrap())
}

async fn handle_stream_url(
    _req: Request<Body>,
    state: Arc<AppState>,
) -> Result<Response<Body>, Infallible> {
    let session = state.active_session.lock().unwrap();
    let (format, url) = if let Some(sess) = session.as_ref() {
        if sess.format == "mjpeg" {
            ("mjpeg", state.get_mjpeg_url())
        } else {
            ("h264", state.get_rtsp_url())
        }
    } else {
        ("none", "".to_string())
    };
    let resp = serde_json::json!({
        "active": session.is_some(),
        "format": format,
        "url": url
    });
    Ok(Response::builder()
        .header(CONTENT_TYPE, "application/json")
        .body(Body::from(resp.to_string()))
        .unwrap())
}

async fn proxy_mjpeg_stream(state: Arc<AppState>) -> impl Stream<Item = Result<Bytes, std::io::Error>> {
    let url = state.get_mjpeg_url();
    let host = format!("{}:{}", state.camera_ip, state.camera_http_port);
    let path = format!("/{}", state.mjpeg_path);

    let mut stream = TcpStream::connect(&host).await?;
    let auth = if !state.camera_user.is_empty() {
        let creds = format!("{}:{}", state.camera_user, state.camera_pass);
        let encoded = base64::encode(creds);
        format!("Authorization: Basic {}\r\n", encoded)
    } else {
        String::new()
    };
    let req = format!(
        "GET {} HTTP/1.1\r\nHost: {}\r\n{}Connection: close\r\n\r\n",
        path, state.camera_ip, auth
    );
    stream.write_all(req.as_bytes()).await?;

    let mut buf = vec![0u8; 4096];
    let mut headers_done = false;
    let mut leftover = vec![];
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Bytes, std::io::Error>>(8);

    tokio::spawn(async move {
        loop {
            let n = match stream.read(&mut buf).await {
                Ok(n) if n == 0 => break,
                Ok(n) => n,
                Err(e) => {
                    let _ = tx.send(Err(e)).await;
                    break;
                }
            };
            let mut chunk = &buf[..n];
            if !headers_done {
                if let Some(idx) = memchr::memmem::find(chunk, b"\r\n\r\n") {
                    headers_done = true;
                    chunk = &chunk[idx + 4..];
                } else {
                    continue;
                }
            }
            if !chunk.is_empty() {
                let mut data = leftover.clone();
                data.extend_from_slice(chunk);
                leftover.clear();
                let _ = tx.send(Ok(Bytes::from(data))).await;
            }
        }
    });
    tokio_stream::wrappers::ReceiverStream::new(rx)
}

async fn proxy_rtsp_h264_stream(state: Arc<AppState>) -> impl Stream<Item = Result<Bytes, std::io::Error>> {
    // For demonstration: proxy the raw RTSP TCP data stream.
    // Note: Browsers cannot play raw RTSP; for proof-of-concept only.
    let host = format!("{}:{}", state.camera_ip, state.camera_rtsp_port);
    let path = format!("/{}", state.rtsp_path);
    let creds = if !state.camera_user.is_empty() {
        format!("{}:{}", state.camera_user, state.camera_pass)
    } else {
        String::new()
    };

    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Bytes, std::io::Error>>(8);

    tokio::spawn(async move {
        let mut stream = match TcpStream::connect(&host).await {
            Ok(s) => s,
            Err(e) => {
                let _ = tx.send(Err(e)).await;
                return;
            }
        };
        // Basic RTSP SETUP/PLAY
        let setup = format!(
            "OPTIONS rtsp://{}{} RTSP/1.0\r\nCSeq: 1\r\n{}\r\n",
            state.camera_ip,
            path,
            if !creds.is_empty() {
                let encoded = base64::encode(creds);
                format!("Authorization: Basic {}", encoded)
            } else {
                String::new()
            }
        );
        let _ = stream.write_all(setup.as_bytes()).await;
        let mut buf = vec![0u8; 4096];
        loop {
            let n = match stream.read(&mut buf).await {
                Ok(n) if n == 0 => break,
                Ok(n) => n,
                Err(e) => {
                    let _ = tx.send(Err(e)).await;
                    break;
                }
            };
            if n > 0 {
                let _ = tx.send(Ok(Bytes::copy_from_slice(&buf[..n]))).await;
            }
        }
    });
    tokio_stream::wrappers::ReceiverStream::new(rx)
}

async fn handle_video(
    _req: Request<Body>,
    state: Arc<AppState>,
) -> Result<Response<Body>, Infallible> {
    let session = state.active_session.lock().unwrap().clone();
    if let Some(sess) = session {
        if sess.format == "mjpeg" {
            let stream = proxy_mjpeg_stream(state.clone()).await;
            Ok(Response::builder()
                .header(CONTENT_TYPE, "multipart/x-mixed-replace; boundary=--myboundary")
                .header(CACHE_CONTROL, "no-cache")
                .body(Body::wrap_stream(stream))
                .unwrap())
        } else if sess.format == "h264" {
            let stream = proxy_rtsp_h264_stream(state.clone()).await;
            Ok(Response::builder()
                .header(CONTENT_TYPE, "application/octet-stream")
                .header(CACHE_CONTROL, "no-cache")
                .body(Body::wrap_stream(stream))
                .unwrap())
        } else {
            Ok(Response::builder()
                .status(StatusCode::BAD_REQUEST)
                .body(Body::from("Unknown session format"))
                .unwrap())
        }
    } else {
        Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("No active stream"))
            .unwrap())
    }
}

async fn router(req: Request<Body>, state: Arc<AppState>) -> Result<Response<Body>, Infallible> {
    let (parts, body) = req.into_parts();
    let uri = parts.uri.path();
    match (parts.method, uri) {
        (Method::POST, "/stream/start") => handle_start_stream(Request::from_parts(parts, body), state).await,
        (Method::POST, "/stream/stop") => handle_stop_stream(Request::from_parts(parts, body), state).await,
        (Method::GET, "/stream/url") => handle_stream_url(Request::from_parts(parts, body), state).await,
        (Method::GET, "/video") => handle_video(Request::from_parts(parts, body), state).await,
        _ => Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("Not Found"))
            .unwrap()),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let host = get_env("SERVER_HOST", "0.0.0.0".to_string());
    let port = get_env("SERVER_PORT", 8080u16);
    let camera_ip = get_env("CAMERA_IP", "192.168.1.64".to_string());
    let camera_rtsp_port = get_env("CAMERA_RTSP_PORT", 554u16);
    let camera_http_port = get_env("CAMERA_HTTP_PORT", 80u16);
    let camera_user = get_env("CAMERA_USER", "".to_string());
    let camera_pass = get_env("CAMERA_PASS", "".to_string());
    let rtsp_path = get_env("CAMERA_RTSP_PATH", "Streaming/Channels/101".to_string());
    let mjpeg_path = get_env("CAMERA_MJPEG_PATH", "Streaming/channels/101/preview".to_string());

    let state = Arc::new(AppState {
        camera_ip,
        camera_rtsp_port,
        camera_http_port,
        camera_user,
        camera_pass,
        active_session: Arc::new(Mutex::new(None)),
        rtsp_path,
        mjpeg_path,
    });

    let addr = SocketAddr::new(host.parse().unwrap(), port);

    let make_svc = make_service_fn(move |_conn| {
        let state = state.clone();
        async move {
            Ok::<_, Infallible>(service_fn(move |req| {
                router(req, state.clone())
            }))
        }
    });

    println!("HTTP server running on http://{}/", addr);
    Server::bind(&addr).serve(make_svc).await?;
    Ok(())
}