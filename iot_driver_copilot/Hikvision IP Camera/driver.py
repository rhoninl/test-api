import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify

import cv2

# Environment Variables for Configuration
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_RTSP_PORT = os.environ.get("CAMERA_RTSP_PORT", "554")
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")

HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "Streaming/Channels/101")
CAMERA_STREAM_CODEC = os.environ.get("CAMERA_STREAM_CODEC", "h264")  # 'h264' or 'mjpeg'
CAMERA_RTSP_TRANSPORT = os.environ.get("CAMERA_RTSP_TRANSPORT", "tcp")  # 'tcp' or 'udp'

RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"

app = Flask(__name__)

# Shared state for stream control
streaming_enabled = threading.Event()
frame_queue = queue.Queue(maxsize=10)
stream_thread = None
stream_thread_lock = threading.Lock()

def camera_stream_worker():
    global frame_queue
    cap = None
    try:
        cap = cv2.VideoCapture(
            RTSP_URL,
            cv2.CAP_FFMPEG
        )
        # Optionally set RTSP transport
        if CAMERA_RTSP_TRANSPORT.lower() == "udp":
            cap.set(cv2.CAP_PROP_RTSP_TRANSPORT, cv2.CAP_FFMPEG)
        while streaming_enabled.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            data = jpeg.tobytes()
            try:
                if frame_queue.full():
                    frame_queue.get_nowait()
                frame_queue.put_nowait(data)
            except queue.Full:
                pass
    finally:
        if cap:
            cap.release()

def start_camera_stream():
    global stream_thread
    with stream_thread_lock:
        if not streaming_enabled.is_set():
            streaming_enabled.set()
            stream_thread = threading.Thread(target=camera_stream_worker, daemon=True)
            stream_thread.start()

def stop_camera_stream():
    streaming_enabled.clear()
    # Clear queued frames
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break

@app.route("/video/start", methods=["POST"])
def api_video_start():
    start_camera_stream()
    return jsonify({"status": "started"}), 200

@app.route("/video/stop", methods=["POST"])
def api_video_stop():
    stop_camera_stream()
    return jsonify({"status": "stopped"}), 200

def mjpeg_generator():
    while streaming_enabled.is_set():
        try:
            frame = frame_queue.get(timeout=2)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    # End of stream
    yield b''

@app.route("/video/stream", methods=["GET"])
def api_video_stream():
    if not streaming_enabled.is_set():
        return jsonify({"error": "Stream is not started. POST /video/start first."}), 400
    return Response(mjpeg_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/", methods=["GET"])
def index():
    return '''
    <html>
      <head>
        <title>Hikvision Camera MJPEG Stream</title>
      </head>
      <body>
        <h1>Live Video Stream</h1>
        <img src="/video/stream" width="640" />
        <form method="post" action="/video/start"><button type="submit">Start Stream</button></form>
        <form method="post" action="/video/stop"><button type="submit">Stop Stream</button></form>
      </body>
    </html>
    '''

if __name__ == "__main__":
    app.run(host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT, threaded=True)