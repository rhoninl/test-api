import os
import threading
import time
from flask import Flask, Response, request, jsonify
import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP")
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
STREAM_PATH = os.environ.get("STREAM_PATH", "/Streaming/Channels/101")

RTSP_TEMPLATE = "rtsp://{username}:{password}@{ip}:{port}{path}"

# Global state
stream_active = threading.Event()
camera_lock = threading.Lock()
video_capture = None

app = Flask(__name__)

def get_rtsp_url():
    return RTSP_TEMPLATE.format(
        username=DEVICE_USERNAME,
        password=DEVICE_PASSWORD,
        ip=DEVICE_IP,
        port=RTSP_PORT,
        path=STREAM_PATH
    )

def start_stream():
    global video_capture
    with camera_lock:
        if stream_active.is_set():
            return
        rtsp_url = get_rtsp_url()
        video_capture = cv2.VideoCapture(rtsp_url)
        if video_capture.isOpened():
            stream_active.set()
        else:
            video_capture.release()
            video_capture = None

def stop_stream():
    global video_capture
    with camera_lock:
        stream_active.clear()
        if video_capture is not None:
            video_capture.release()
            video_capture = None

def gen_frames():
    global video_capture
    while stream_active.is_set():
        with camera_lock:
            if video_capture is None:
                break
            ret, frame = video_capture.read()
        if not ret:
            time.sleep(0.1)
            continue
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    # Stream stopped, send end of stream
    yield b''

@app.route('/start', methods=['POST'])
def api_start():
    if stream_active.is_set():
        return jsonify({"status": "already_started"}), 200
    start_stream()
    if stream_active.is_set():
        return jsonify({"status": "started"}), 200
    else:
        return jsonify({"status": "failed_to_start"}), 500

@app.route('/stop', methods=['POST'])
def api_stop():
    if not stream_active.is_set():
        return jsonify({"status": "already_stopped"}), 200
    stop_stream()
    return jsonify({"status": "stopped"}), 200

@app.route('/stream', methods=['GET'])
def api_stream():
    if not stream_active.is_set():
        return jsonify({"error": "stream_not_started"}), 400
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)