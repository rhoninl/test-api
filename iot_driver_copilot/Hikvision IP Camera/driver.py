use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use actix_web::http::{header, StatusCode};
use futures::stream::StreamExt;
use serde::{Deserialize, Serialize};
use std::env;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::broadcast;
use bytes::Bytes;

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    status: String,
    format: String,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
}

struct StreamSession {
    active: bool,
    format: String,
    sender: Option<broadcast::Sender<Bytes>>,
}

type SharedSession = Arc<Mutex<StreamSession>>;

async fn start_stream(
    session: web::Data<SharedSession>,
    req: web::Json<StartStreamRequest>,
) -> impl Responder {
    let mut sess = session.lock().unwrap();
    if sess.active {
        return HttpResponse::Ok().json(StartStreamResponse {
            status: "already started".to_string(),
            format: sess.format.clone(),
        });
    }
    let fmt = req
        .format
        .as_ref()
        .map(|f| f.to_lowercase())
        .unwrap_or("mjpeg".to_string());
    if fmt != "mjpeg" && fmt != "h264" {
        return HttpResponse::BadRequest().body("Unsupported format");
    }
    sess.format = fmt.clone();
    sess.active = true;
    let (tx, _rx) = broadcast::channel::<Bytes>(32);
    sess.sender = Some(tx);
    HttpResponse::Ok().json(StartStreamResponse {
        status: "started".to_string(),
        format: fmt,
    })
}

async fn stop_stream(session: web::Data<SharedSession>) -> impl Responder {
    let mut sess = session.lock().unwrap();
    sess.active = false;
    sess.sender = None;
    HttpResponse::Ok().body("Stream stopped")
}

async fn stream_url(session: web::Data<SharedSession>) -> impl Responder {
    let sess = session.lock().unwrap();
    let port = env::var("SERVER_PORT").unwrap_or_else(|_| "8080".to_string());
    let host = env::var("SERVER_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let fmt = &sess.format;
    let url = format!("http://{}:{}/stream/video", host, port);
    HttpResponse::Ok().json(StreamUrlResponse { url })
}

async fn stream_video(
    req: HttpRequest,
    session: web::Data<SharedSession>,
) -> impl Responder {
    let sess = session.lock().unwrap();
    if !sess.active {
        return HttpResponse::build(StatusCode::SERVICE_UNAVAILABLE)
            .body("Stream not started");
    }
    let format = sess.format.clone();
    let tx = sess.sender.as_ref().unwrap().clone();
    drop(sess);

    match format.as_str() {
        "mjpeg" => {
            let rx = tx.subscribe();
            let stream = futures::stream::unfold(rx, |mut rx| async {
                match rx.recv().await {
                    Ok(chunk) => {
                        let mut data = Vec::new();
                        data.extend_from_slice(b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n");
                        data.extend_from_slice(&chunk);
                        data.extend_from_slice(b"\r\n");
                        Some((Bytes::from(data), rx))
                    }
                    Err(_) => None,
                }
            });
            HttpResponse::Ok()
                .insert_header((
                    "Content-Type",
                    "multipart/x-mixed-replace;boundary=boundary",
                ))
                .streaming(stream)
        }
        "h264" => {
            let rx = tx.subscribe();
            let stream = futures::stream::unfold(rx, |mut rx| async {
                match rx.recv().await {
                    Ok(chunk) => Some((chunk, rx)),
                    Err(_) => None,
                }
            });
            HttpResponse::Ok()
                .insert_header(("Content-Type", "video/h264"))
                .streaming(stream)
        }
        _ => HttpResponse::BadRequest().body("Unsupported format"),
    }
}

async fn camera_stream_loop(session: SharedSession) {
    let camera_ip = env::var("CAMERA_IP").expect("CAMERA_IP is not set");
    let camera_rtsp_port =
        env::var("CAMERA_RTSP_PORT").unwrap_or_else(|_| "554".to_string());
    let camera_http_port =
        env::var("CAMERA_HTTP_PORT").unwrap_or_else(|_| "80".to_string());
    let camera_user = env::var("CAMERA_USER").unwrap_or_else(|_| "admin".to_string());
    let camera_pass = env::var("CAMERA_PASS").unwrap_or_else(|_| "12345".to_string());
    let stream_path = env::var("CAMERA_STREAM_PATH")
        .unwrap_or_else(|_| "/Streaming/channels/101/picture".to_string());

    loop {
        let (active, format, tx_opt) = {
            let sess = session.lock().unwrap();
            (sess.active, sess.format.clone(), sess.sender.clone())
        };
        if !active || tx_opt.is_none() {
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            continue;
        }
        if format == "mjpeg" {
            // MJPEG over HTTP fetch loop
            let url = format!(
                "http://{}:{}{}",
                camera_ip, camera_http_port, stream_path
            );
            let client = reqwest::Client::new();
            let req = client
                .get(&url)
                .basic_auth(camera_user.clone(), Some(camera_pass.clone()))
                .send();
            let mut resp = match req.await {
                Ok(r) => r,
                Err(_) => {
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                    continue;
                }
            };
            let mut stream = resp.bytes_stream();
            let tx = tx_opt.unwrap();
            while let Some(Ok(chunk)) = stream.next().await {
                let _ = tx.send(chunk);
                let (active2, _, _) = {
                    let sess = session.lock().unwrap();
                    (sess.active, sess.format.clone(), sess.sender.clone())
                };
                if !active2 {
                    break;
                }
            }
        } else if format == "h264" {
            // H.264 over RTSP simple fetch
            // Note: This does NOT fully parse RTSP/RTP, but proxies raw TCP data.
            let rtsp_url = format!(
                "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
                camera_user, camera_pass, camera_ip, camera_rtsp_port
            );
            // Minimal RTSP, just connect and forward raw data.
            match TcpStream::connect(format!("{}:{}", camera_ip, camera_rtsp_port)).await {
                Ok(mut stream) => {
                    let describe = format!(
                        "DESCRIBE {} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\nAuthorization: Basic {}\r\n\r\n",
                        rtsp_url,
                        base64::encode(format!("{}:{}", camera_user, camera_pass))
                    );
                    let _ = stream.write_all(describe.as_bytes()).await;
                    let mut buf = [0u8; 4096];
                    let tx = tx_opt.unwrap();
                    loop {
                        match stream.read(&mut buf).await {
                            Ok(0) => break,
                            Ok(n) => {
                                let _ = tx.send(Bytes::copy_from_slice(&buf[..n]));
                            }
                            Err(_) => break,
                        }
                        let (active2, fmt2, _) = {
                            let sess = session.lock().unwrap();
                            (sess.active, sess.format.clone(), sess.sender.clone())
                        };
                        if !active2 || fmt2 != "h264" {
                            break;
                        }
                    }
                }
                Err(_) => {
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                }
            }
        } else {
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        }
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let server_host = env::var("SERVER_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let server_port = env::var("SERVER_PORT").unwrap_or_else(|_| "8080".to_string());

    let session = Arc::new(Mutex::new(StreamSession {
        active: false,
        format: "mjpeg".to_string(),
        sender: None,
    }));

    let session_clone = session.clone();
    tokio::spawn(async move {
        camera_stream_loop(session_clone).await;
    });

    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(session.clone()))
            .route("/stream/start", web::post().to(start_stream))
            .route("/stream/stop", web::post().to(stop_stream))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/video", web::get().to(stream_video))
    })
    .bind(format!("{}:{}", server_host, server_port))?
    .run()
    .await
}