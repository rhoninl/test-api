use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use actix_web::http::{header, StatusCode};
use futures::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use std::env;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::broadcast;
use urlencoding::encode;

#[derive(Clone)]
struct Config {
    cam_ip: String,
    cam_port: u16,
    cam_user: String,
    cam_pass: String,
    rtsp_port: u16,
    http_host: String,
    http_port: u16,
}

impl Config {
    fn from_env() -> Self {
        Self {
            cam_ip: env::var("CAM_IP").expect("Missing CAM_IP"),
            cam_port: env::var("CAM_PORT").unwrap_or_else(|_| "80".to_string()).parse().unwrap_or(80),
            cam_user: env::var("CAM_USER").unwrap_or_else(|_| "admin".to_string()),
            cam_pass: env::var("CAM_PASS").unwrap_or_else(|_| "12345".to_string()),
            rtsp_port: env::var("RTSP_PORT").unwrap_or_else(|_| "554".to_string()).parse().unwrap_or(554),
            http_host: env::var("SERVER_HOST").unwrap_or_else(|_| "0.0.0.0".to_string()),
            http_port: env::var("SERVER_PORT").unwrap_or_else(|_| "8080".to_string()).parse().unwrap_or(8080),
        }
    }

    fn rtsp_url(&self, format: &str) -> String {
        // Hikvision typical main stream:
        // rtsp://user:pass@ip:554/Streaming/Channels/101
        // MJPEG: http://user:pass@ip:port/Streaming/channels/102/httpPreview
        match format {
            "mjpeg" => format!(
                "http://{}:{}@{}:{}/Streaming/channels/102/httpPreview",
                self.cam_user, self.cam_pass, self.cam_ip, self.cam_port
            ),
            _ => format!(
                "rtsp://{}:{}@{}:{}/Streaming/Channels/101",
                self.cam_user, self.cam_pass, self.cam_ip, self.rtsp_port
            ),
        }
    }
}

#[derive(Deserialize)]
struct StartStreamRequest {
    format: Option<String>,
}

#[derive(Serialize)]
struct StartStreamResponse {
    message: String,
    session: String,
    format: String,
}

#[derive(Serialize)]
struct StreamUrlResponse {
    url: String,
    format: String,
}

type StopSignal = Arc<Mutex<Option<broadcast::Sender<()>>>>;

async fn start_stream(
    data: web::Data<AppState>,
    req: web::Json<StartStreamRequest>,
) -> impl Responder {
    let format = req.format.clone().unwrap_or_else(|| "h264".to_string());
    let session = uuid::Uuid::new_v4().to_string();

    {
        let mut stop_tx = data.stop_signal.lock().unwrap();
        if stop_tx.is_some() {
            let _ = stop_tx.take();
        }
        let (tx, _) = broadcast::channel(4);
        *stop_tx = Some(tx);
    }

    HttpResponse::Ok().json(StartStreamResponse {
        message: "Stream started".to_string(),
        session,
        format,
    })
}

async fn stop_stream(data: web::Data<AppState>) -> impl Responder {
    let mut stop_tx = data.stop_signal.lock().unwrap();
    if let Some(sender) = stop_tx.take() {
        let _ = sender.send(());
        HttpResponse::Ok().body("Stream stopped")
    } else {
        HttpResponse::Ok().body("No active stream")
    }
}

async fn stream_url(
    data: web::Data<AppState>,
) -> impl Responder {
    let url = data.config.rtsp_url("h264");
    HttpResponse::Ok().json(StreamUrlResponse {
        url,
        format: "h264".to_string(),
    })
}

// MJPEG proxying: grab MJPEG stream from camera, relay as browser-consumable multipart/x-mixed-replace
async fn mjpeg_proxy(
    req: HttpRequest,
    data: web::Data<AppState>,
) -> impl Responder {
    let url = data.config.rtsp_url("mjpeg");
    let (user, pass) = (data.config.cam_user.clone(), data.config.cam_pass.clone());

    let client = reqwest::Client::new();

    let res = client
        .get(&url)
        .basic_auth(user, Some(pass))
        .send()
        .await;

    if let Ok(mut cam_resp) = res {
        if cam_resp.status().is_success() {
            let content_type = cam_resp
                .headers()
                .get(header::CONTENT_TYPE)
                .cloned()
                .unwrap_or_else(|| header::HeaderValue::from_static("multipart/x-mixed-replace;boundary=--boundary"));

            let stream = async_stream::stream! {
                let mut chunk = [0u8; 16 * 1024];
                let mut body = cam_resp.bytes_stream();
                while let Some(Ok(bytes)) = body.next().await {
                    yield Ok::<_, actix_web::Error>(web::Bytes::copy_from_slice(&bytes));
                }
            };
            return HttpResponse::Ok()
                .content_type(content_type)
                .streaming(Box::pin(stream) as Pin<Box<dyn Stream<Item = Result<web::Bytes, actix_web::Error>> + Send>>);
        } else {
            return HttpResponse::build(StatusCode::BAD_GATEWAY)
                .body("Camera MJPEG stream unavailable");
        }
    }
    HttpResponse::build(StatusCode::BAD_GATEWAY).body("Failed to connect to camera MJPEG endpoint")
}

// RTSP to HLS proxy (basic, single client, raw transport): spawn a TCP connection to RTSP, parse RTP, extract H264 NAL units, wrap as MPEG-TS, stream as HTTP
async fn h264_proxy(
    data: web::Data<AppState>,
    req: HttpRequest,
) -> impl Responder {
    // Only implement a raw passthrough proxy of the RTSP stream as a byte stream for simplicity
    // For production, use GStreamer or FFmpeg library in-process
    let rtsp_url = data.config.rtsp_url("h264");
    let stop_signal = {
        let stop = data.stop_signal.lock().unwrap();
        stop.clone()
    };

    let (sender, mut receiver) = tokio::sync::mpsc::channel(2);

    // Spawn RTSP session in separate task
    let rtsp_url = rtsp_url.clone();

    tokio::spawn(async move {
        let mut session = match simple_rtsp::RtspSession::connect(&rtsp_url).await {
            Ok(s) => s,
            Err(_) => {
                let _ = sender.send(Err(actix_web::error::ErrorBadGateway("RTSP connect error"))).await;
                return;
            }
        };

        let mut stream = match session.start_stream().await {
            Ok(s) => s,
            Err(_) => {
                let _ = sender.send(Err(actix_web::error::ErrorBadGateway("RTSP stream error"))).await;
                return;
            }
        };

        loop {
            tokio::select! {
                Some(frame) = stream.next() => {
                    match frame {
                        Ok(data) => {
                            if sender.send(Ok(web::Bytes::from(data))).await.is_err() {
                                break;
                            }
                        },
                        Err(_) => break,
                    }
                }
                _ = async {
                    if let Some(stop) = &stop_signal {
                        let mut rx = stop.subscribe();
                        let _ = rx.recv().await;
                    }
                } => {
                    break;
                }
            }
        }
    });

    let stream = async_stream::stream! {
        while let Some(msg) = receiver.recv().await {
            yield msg;
        }
    };

    HttpResponse::Ok()
        .content_type("video/mp4")
        .streaming(Box::pin(stream) as Pin<Box<dyn Stream<Item = Result<web::Bytes, actix_web::Error>> + Send>>)
}

#[derive(Clone)]
struct AppState {
    config: Config,
    stop_signal: StopSignal,
}

// Minimal RTSP session for passthrough
mod simple_rtsp {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpStream;
    use futures::Stream;
    use std::pin::Pin;
    use std::task::{Context, Poll};
    use std::io;
    use bytes::{BytesMut, Bytes};
    use futures::stream::StreamExt;

    pub struct RtspSession {
        stream: TcpStream,
        session_id: Option<String>,
        cseq: u32,
        url: String,
    }

    impl RtspSession {
        pub async fn connect(url: &str) -> io::Result<Self> {
            // Parse url
            let parts = url::Url::parse(url).expect("Invalid RTSP url");
            let host = parts.host_str().unwrap();
            let port = parts.port().unwrap_or(554);
            let addr = format!("{}:{}", host, port);
            let stream = TcpStream::connect(addr).await?;
            Ok(Self {
                stream,
                session_id: None,
                cseq: 1,
                url: url.to_string(),
            })
        }

        async fn send_cmd(&mut self, cmd: &str, headers: &[(&str, &str)]) -> io::Result<String> {
            let mut req = format!("{}\r\nCSeq: {}\r\n", cmd, self.cseq);
            for (k, v) in headers {
                req.push_str(&format!("{}: {}\r\n", k, v));
            }
            req.push_str("\r\n");
            self.stream.write_all(req.as_bytes()).await?;
            self.cseq += 1;
            let mut buf = [0u8; 4096];
            let n = self.stream.read(&mut buf).await?;
            Ok(String::from_utf8_lossy(&buf[..n]).to_string())
        }

        pub async fn start_stream(mut self) -> io::Result<RtspStream> {
            let describe = format!("DESCRIBE {} RTSP/1.0", self.url);
            let _ = self.send_cmd(&describe, &[("Accept", "application/sdp")]).await?;
            let setup = format!("SETUP {}/trackID=1 RTSP/1.0", self.url);
            let reply = self.send_cmd(&setup, &[("Transport", "RTP/AVP/TCP;unicast;interleaved=0-1")]).await?;
            let session_id = reply.lines().find_map(|l| {
                if l.to_ascii_lowercase().starts_with("session:") {
                    Some(l.split(':').nth(1).unwrap().trim().to_string())
                } else {
                    None
                }
            });
            self.session_id = session_id;
            let play = format!("PLAY {} RTSP/1.0", self.url);
            let mut headers = vec![];
            if let Some(ref s) = self.session_id {
                headers.push(("Session", s.as_str()));
            }
            let _ = self.send_cmd(&play, &headers).await?;

            Ok(RtspStream {
                stream: self.stream,
            })
        }
    }

    pub struct RtspStream {
        stream: TcpStream,
    }

    impl Stream for RtspStream {
        type Item = io::Result<Vec<u8>>;
        fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
            let me = self.get_mut();
            let mut buf = [0u8; 4096];
            match futures::ready!(Pin::new(&mut me.stream).poll_read(cx, &mut buf)) {
                Ok(n) if n > 0 => Poll::Ready(Some(Ok(buf[..n].to_vec()))),
                Ok(_) => Poll::Ready(None),
                Err(e) => Poll::Ready(Some(Err(e))),
            }
        }
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    let config = Config::from_env();
    let stop_signal = Arc::new(Mutex::new(None));

    let app_data = web::Data::new(AppState {
        config: config.clone(),
        stop_signal,
    });

    HttpServer::new(move || {
        App::new()
            .app_data(app_data.clone())
            .route("/stream/start", web::post().to(start_stream))
            .route("/stream/stop", web::post().to(stop_stream))
            .route("/stream/url", web::get().to(stream_url))
            .route("/stream/mjpeg", web::get().to(mjpeg_proxy))
            .route("/stream/h264", web::get().to(h264_proxy))
    })
    .bind((config.http_host.as_str(), config.http_port))?
    .run()
    .await
}