import os
import threading
import time
import io
from flask import Flask, Response, request, jsonify
import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

# Build RTSP URL
RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

# Global state for streaming
streaming_active = threading.Event()
frame_lock = threading.Lock()
current_frame = [None]  # Use a list to allow mutability inside threads

def rtsp_capture_loop():
    global current_frame
    cap = None
    while True:
        if streaming_active.is_set():
            if cap is None:
                cap = cv2.VideoCapture(RTSP_URL)
                if not cap.isOpened():
                    cap.release()
                    cap = None
                    time.sleep(1)
                    continue
            ret, frame = cap.read()
            if not ret:
                cap.release()
                cap = None
                time.sleep(0.5)
                continue
            with frame_lock:
                current_frame[0] = frame
            time.sleep(0.03)  # ~30 fps
        else:
            if cap is not None:
                cap.release()
                cap = None
            with frame_lock:
                current_frame[0] = None
            time.sleep(0.1)

# Start background thread for capturing frames
capture_thread = threading.Thread(target=rtsp_capture_loop, daemon=True)
capture_thread.start()

@app.route('/video/start', methods=['POST'])
def start_stream():
    streaming_active.set()
    return jsonify({"status": "started"}), 200

@app.route('/video/stop', methods=['POST'])
def stop_stream():
    streaming_active.clear()
    return jsonify({"status": "stopped"}), 200

def generate_mjpeg():
    while streaming_active.is_set():
        with frame_lock:
            frame = current_frame[0]
        if frame is None:
            time.sleep(0.05)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = jpeg.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.03)  # ~30 fps

@app.route('/video', methods=['GET'])
def video_feed():
    if not streaming_active.is_set():
        return Response("Stream not started. POST to /video/start first.", status=503)
    return Response(generate_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/', methods=['GET'])
def index():
    return '''
    <html>
        <head>
            <title>Hikvision Camera MJPEG Stream</title>
        </head>
        <body>
            <h1>Hikvision Camera MJPEG Stream</h1>
            <img src="/video" width="720" />
            <form method="post" action="/video/start">
                <button type="submit">Start Stream</button>
            </form>
            <form method="post" action="/video/stop">
                <button type="submit">Stop Stream</button>
            </form>
        </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT, threaded=True)