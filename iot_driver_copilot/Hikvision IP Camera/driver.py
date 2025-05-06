import os
import threading
import time
from flask import Flask, Response, jsonify, request
import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "/Streaming/Channels/101")
CAMERA_PROTOCOL = os.environ.get("CAMERA_PROTOCOL", "rtsp")  # rtsp
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# RTSP URL constructor
def get_rtsp_url():
    return f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{CAMERA_STREAM_PATH}"

app = Flask(__name__)

streaming_active = threading.Event()
frame_lock = threading.Lock()
current_frame = [None]
video_capture = [None]

def start_capture():
    with frame_lock:
        if video_capture[0] is None or not video_capture[0].isOpened():
            rtsp_url = get_rtsp_url()
            cap = cv2.VideoCapture(rtsp_url)
            if not cap.isOpened():
                return False
            video_capture[0] = cap
            streaming_active.set()
            threading.Thread(target=update_frame_thread, daemon=True).start()
            return True
        else:
            streaming_active.set()
            return True

def stop_capture():
    with frame_lock:
        streaming_active.clear()
        if video_capture[0] is not None:
            try:
                video_capture[0].release()
            except Exception:
                pass
            video_capture[0] = None
        current_frame[0] = None

def update_frame_thread():
    while streaming_active.is_set():
        with frame_lock:
            cap = video_capture[0]
            if cap is None or not cap.isOpened():
                streaming_active.clear()
                break
            ret, frame = cap.read()
            if ret:
                # Convert frame to JPEG
                ret, jpeg = cv2.imencode('.jpg', frame)
                if ret:
                    current_frame[0] = jpeg.tobytes()
        time.sleep(0.04)  # ~25 FPS

def gen_mjpeg():
    while streaming_active.is_set():
        with frame_lock:
            frame = current_frame[0]
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.04)

@app.route("/video/stream")
def video_stream():
    if not streaming_active.is_set():
        return jsonify({"error": "Streaming is not active. Use /video/start to begin."}), 503
    return Response(gen_mjpeg(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/video/start", methods=["POST"])
def start_video():
    if streaming_active.is_set():
        return jsonify({"message": "Stream already running."}), 200
    if not start_capture():
        return jsonify({"error": "Failed to connect to camera."}), 500
    return jsonify({"message": "Streaming started."}), 200

@app.route("/video/stop", methods=["POST"])
def stop_video():
    if not streaming_active.is_set():
        return jsonify({"message": "Stream already stopped."}), 200
    stop_capture()
    return jsonify({"message": "Streaming stopped."}), 200

@app.route("/")
def index():
    return """
    <html>
        <head>
            <title>Hikvision Camera MJPEG Stream</title>
        </head>
        <body>
            <h1>Hikvision Camera Live Stream</h1>
            <img src="/video/stream" width="720" />
            <br>
            <form action="/video/start" method="post">
                <button type="submit">Start Stream</button>
            </form>
            <form action="/video/stop" method="post">
                <button type="submit">Stop Stream</button>
            </form>
        </body>
    </html>
    """

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)