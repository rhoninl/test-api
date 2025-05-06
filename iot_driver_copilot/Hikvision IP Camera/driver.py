import os
import threading
import time
from flask import Flask, Response, request, jsonify
import cv2

# --- Configuration from environment variables ---
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.64')
CAMERA_RTSP_PORT = os.environ.get('CAMERA_RTSP_PORT', '554')
CAMERA_USER = os.environ.get('CAMERA_USER', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

CAMERA_STREAM_PATH = os.environ.get('CAMERA_STREAM_PATH', 'Streaming/Channels/101')
CAMERA_STREAM_FORMAT = os.environ.get('CAMERA_STREAM_FORMAT', 'h264')  # 'h264' or 'mjpeg'

# --- RTSP Stream URL ---
RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"

app = Flask(__name__)

# --- Video Streaming State ---
streaming_active = threading.Event()
frame_lock = threading.Lock()
current_frame = [None]

def camera_stream_worker():
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            return
        while streaming_active.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            with frame_lock:
                current_frame[0] = frame
        cap.release()
    except Exception:
        if cap:
            cap.release()

@app.route('/video/start', methods=['POST'])
def start_stream():
    if streaming_active.is_set():
        return jsonify({"status": "already streaming"}), 200
    streaming_active.set()
    t = threading.Thread(target=camera_stream_worker, daemon=True)
    t.start()
    time.sleep(1)  # Allow the camera stream to warm up
    return jsonify({"status": "stream started"}), 200

@app.route('/video/stop', methods=['POST'])
def stop_stream():
    if not streaming_active.is_set():
        return jsonify({"status": "already stopped"}), 200
    streaming_active.clear()
    with frame_lock:
        current_frame[0] = None
    return jsonify({"status": "stream stopped"}), 200

def gen_mjpeg():
    while streaming_active.is_set():
        with frame_lock:
            frame = current_frame[0]
        if frame is not None:
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            byte_frame = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + byte_frame + b'\r\n')
        else:
            time.sleep(0.05)

@app.route('/video/stream')
def video_stream():
    if not streaming_active.is_set():
        return Response("Stream not started. POST /video/start to begin streaming.", status=503)
    return Response(gen_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return '''
    <html>
    <head>
        <title>Hikvision IP Camera Stream</title>
    </head>
    <body>
        <h2>Hikvision IP Camera Live Stream</h2>
        <img src="/video/stream" width="640" />
        <form action="/video/start" method="post">
            <button type="submit">Start Stream</button>
        </form>
        <form action="/video/stop" method="post">
            <button type="submit">Stop Stream</button>
        </form>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)