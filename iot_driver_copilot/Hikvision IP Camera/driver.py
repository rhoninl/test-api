use std::env;
use std::sync::{Arc, Mutex};
use std::net::SocketAddr;
use std::time::Duration;

use hyper::{Body, Request, Response, Server, Method, StatusCode};
use hyper::service::{make_service_fn, service_fn};
use serde::{Deserialize, Serialize};
use tokio::sync::oneshot;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_stream::StreamExt;
use futures::stream::Stream;

#[derive(Clone)]
struct Config {
    camera_ip: String,
    camera_rtsp_port: u16,
    camera_user: String,
    camera_password: String,
    server_host: String,
    server_port: u16,
}

#[derive(Clone, Debug, Serialize)]
struct StreamSession {
    format: String,
    active: bool,
}

#[derive(Clone)]
struct AppState {
    // Only one active streaming session at a time for simplicity
    session: Arc<Mutex<Option<StreamSession>>>,
    // Used to signal stream task to stop
    stop_tx: Arc<Mutex<Option<oneshot::Sender<()>>>>,
    config: Config,
}

#[derive(Debug, Deserialize)]
struct StartStreamBody {
    format: Option<String>,
}

fn get_env(name: &str, default: Option<&str>) -> String {
    env::var(name).unwrap_or_else(|_| default.unwrap_or("").to_string())
}

fn get_env_u16(name: &str, default: u16) -> u16 {
    env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn build_rtsp_url(config: &Config, format: &str) -> String {
    // Hikvision default main stream URL
    // MJPEG: /Streaming/channels/1/httppreview
    // H.264: /Streaming/Channels/101 or /Streaming/Channels/1
    if format.to_lowercase() == "mjpeg" {
        format!(
            "rtsp://{}:{}@{}:{}/Streaming/channels/1/preview",
            config.camera_user, config.camera_password, config.camera_ip, config.camera_rtsp_port
        )
    } else {
        format!(
            "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
            config.camera_user, config.camera_password, config.camera_ip, config.camera_rtsp_port
        )
    }
}

async fn stream_rtsp_to_mjpeg(
    rtsp_url: &str,
    mut stop_rx: oneshot::Receiver<()>,
) -> impl Stream<Item=Result<bytes::Bytes, std::io::Error>> {
    // This is a stub/proxy: actual RTSP parsing is non-trivial, but for the sake of the example,
    // we simulate a compatible MJPEG stream.
    // To do: Implement a minimal RTSP client and H.264->MJPEG conversion; here we simulate MJPEG frames.
    // In a real implementation, you would use a library to decode the RTSP H.264 data and encode as MJPEG.
    let (mut tx, rx) = tokio::sync::mpsc::channel(8);

    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(100));
        loop {
            tokio::select! {
                _ = &mut stop_rx => {
                    break;
                }
                _ = interval.tick() => {
                    // Simulate a JPEG frame (just a static header and random bytes)
                    let frame = b"\xff\xd8\xff\xdbJPEGFRAME\xff\xd9".to_vec();
                    let mut part = Vec::new();
                    part.extend_from_slice(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n");
                    part.extend_from_slice(&frame);
                    part.extend_from_slice(b"\r\n");
                    if tx.send(Ok(bytes::Bytes::from(part))).await.is_err() {
                        break;
                    }
                }
            }
        }
    });

    tokio_stream::wrappers::ReceiverStream::new(rx)
}

async fn handle_request(
    req: Request<Body>,
    state: AppState,
) -> Result<Response<Body>, hyper::Error> {
    let path = req.uri().path().to_owned();
    let method = req.method().clone();

    match (method, path.as_str()) {
        (Method::POST, "/stream/start") => {
            // Parse requested format from JSON body
            let whole_body = hyper::body::to_bytes(req.into_body()).await?;
            let format = if !whole_body.is_empty() {
                serde_json::from_slice::<StartStreamBody>(&whole_body)
                    .unwrap_or(StartStreamBody { format: None })
                    .format.unwrap_or_else(|| "mjpeg".to_string())
            } else {
                "mjpeg".to_string()
            };
            if format.to_lowercase() != "mjpeg" && format.to_lowercase() != "h264" {
                return Ok(Response::builder()
                    .status(StatusCode::BAD_REQUEST)
                    .body(Body::from(r#"{"error":"Unsupported format"}"#))
                    .unwrap());
            }

            let mut session = state.session.lock().unwrap();
            if session.is_some() && session.as_ref().unwrap().active {
                return Ok(Response::builder()
                    .status(StatusCode::CONFLICT)
                    .body(Body::from(r#"{"error":"Stream already active"}"#))
                    .unwrap());
            }
            *session = Some(StreamSession {
                format: format.clone(),
                active: true,
            });
            // Prepare stop channel for stream
            let (stop_tx, _stop_rx) = oneshot::channel();
            *state.stop_tx.lock().unwrap() = Some(stop_tx);

            Ok(Response::builder()
                .header("Content-Type", "application/json")
                .body(Body::from(format!(
                    r#"{{"status":"started","format":"{}"}}"#,
                    format
                )))
                .unwrap())
        }
        (Method::POST, "/stream/stop") => {
            // Stop active stream if any
            let mut stop_tx = state.stop_tx.lock().unwrap();
            if let Some(tx) = stop_tx.take() {
                let _ = tx.send(());
            }
            let mut session = state.session.lock().unwrap();
            *session = None;
            Ok(Response::builder()
                .header("Content-Type", "application/json")
                .body(Body::from(r#"{"status":"stopped"}"#))
                .unwrap())
        }
        (Method::GET, "/stream/url") => {
            // Return the RTSP URL (for debugging or advanced clients)
            let session = state.session.lock().unwrap();
            let format = if let Some(ref s) = *session {
                s.format.clone()
            } else {
                "mjpeg".to_string()
            };
            let url = build_rtsp_url(&state.config, &format);
            Ok(Response::builder()
                .header("Content-Type", "application/json")
                .body(Body::from(format!(r#"{{"url":"{}"}}"#, url)))
                .unwrap())
        }
        (Method::GET, "/stream/live") => {
            // Proxy MJPEG stream over HTTP
            let session = state.session.lock().unwrap();
            if session.is_none() || !session.as_ref().unwrap().active {
                return Ok(Response::builder()
                    .status(StatusCode::NOT_FOUND)
                    .body(Body::from("Stream not started."))
                    .unwrap());
            }
            let format = session.as_ref().unwrap().format.clone();
            if format.to_lowercase() != "mjpeg" {
                return Ok(Response::builder()
                    .status(StatusCode::NOT_IMPLEMENTED)
                    .body(Body::from("Only MJPEG supported for browser streaming."))
                    .unwrap());
            }

            // Prepare to stream MJPEG frames
            let stop_rx = state.stop_tx.lock().unwrap().take().map(|tx| {
                let (proxy_tx, proxy_rx) = oneshot::channel();
                // Immediately replace, so /stream/stop can work again
                *state.stop_tx.lock().unwrap() = Some(proxy_tx);
                tx
            }).unwrap_or_else(|| {
                let (_tx, rx) = oneshot::channel();
                rx
            });

            let rtsp_url = build_rtsp_url(&state.config, &format);

            let stream = stream_rtsp_to_mjpeg(&rtsp_url, stop_rx).await;
            let body = Body::wrap_stream(stream);

            Ok(Response::builder()
                .header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                .header("Cache-Control", "no-cache")
                .body(body)
                .unwrap())
        }
        _ => Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("Not Found"))
            .unwrap()),
    }
}

#[tokio::main]
async fn main() {
    // Load configuration from environment
    let config = Config {
        camera_ip: get_env("CAMERA_IP", None),
        camera_rtsp_port: get_env_u16("CAMERA_RTSP_PORT", 554),
        camera_user: get_env("CAMERA_USER", Some("admin")),
        camera_password: get_env("CAMERA_PASSWORD", Some("12345")),
        server_host: get_env("SERVER_HOST", Some("0.0.0.0")),
        server_port: get_env_u16("SERVER_PORT", 8080),
    };

    let addr: SocketAddr = format!("{}:{}", config.server_host, config.server_port)
        .parse().expect("Invalid SERVER_HOST or SERVER_PORT");

    let state = AppState {
        session: Arc::new(Mutex::new(None)),
        stop_tx: Arc::new(Mutex::new(None)),
        config: config.clone(),
    };

    let make_svc = make_service_fn(move |_conn| {
        let state = state.clone();
        async move {
            Ok::<_, hyper::Error>(service_fn(move |req| {
                handle_request(req, state.clone())
            }))
        }
    });

    println!(
        "Hikvision IP Camera HTTP driver listening on http://{} (connect /stream/live in browser)",
        addr
    );
    Server::bind(&addr).serve(make_svc).await.unwrap();
}