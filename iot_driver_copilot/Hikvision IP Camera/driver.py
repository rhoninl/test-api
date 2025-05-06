import os
import threading
import time
import io
from flask import Flask, Response, request, jsonify, send_file, abort
import requests
import cv2
import numpy as np

app = Flask(__name__)

# Environment configuration
CAMERA_IP = os.environ.get("HIKVISION_CAMERA_IP")
CAMERA_RTSP_PORT = int(os.environ.get("HIKVISION_CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.environ.get("HIKVISION_CAMERA_USER")
CAMERA_PASSWORD = os.environ.get("HIKVISION_CAMERA_PASSWORD")
CAMERA_CHANNEL = os.environ.get("HIKVISION_CAMERA_CHANNEL", "101")
CAMERA_HTTP_PORT = int(os.environ.get("HIKVISION_CAMERA_HTTP_PORT", "80"))

SERVER_HOST = os.environ.get("DRIVER_HTTP_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("DRIVER_HTTP_PORT", "8080"))

# Global stream state
stream_thread = None
stream_running = threading.Event()
frame_lock = threading.Lock()
last_frame = None

def get_rtsp_url():
    if CAMERA_USER and CAMERA_PASSWORD:
        return f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/Streaming/Channels/{CAMERA_CHANNEL}"
    else:
        return f"rtsp://{CAMERA_IP}:{CAMERA_RTSP_PORT}/Streaming/Channels/{CAMERA_CHANNEL}"

def get_snapshot_url():
    # Hikvision snapshot API URL
    if CAMERA_USER and CAMERA_PASSWORD:
        return f"http://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_HTTP_PORT}/ISAPI/Streaming/channels/{CAMERA_CHANNEL}/picture"
    else:
        return f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}/ISAPI/Streaming/channels/{CAMERA_CHANNEL}/picture"

def capture_snapshot():
    url = get_snapshot_url()
    try:
        auth = (CAMERA_USER, CAMERA_PASSWORD) if CAMERA_USER and CAMERA_PASSWORD else None
        r = requests.get(url, auth=auth, timeout=5)
        if r.status_code == 200 and r.headers.get('Content-Type', '').startswith("image"):
            return r.content
    except Exception:
        pass
    # Fallback: try to grab a frame from RTSP stream
    cap = cv2.VideoCapture(get_rtsp_url())
    ret, frame = cap.read()
    cap.release()
    if ret:
        _, buf = cv2.imencode('.jpg', frame)
        return buf.tobytes()
    return None

def frame_grabber():
    global last_frame
    rtsp_url = get_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    while stream_running.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        _, buf = cv2.imencode('.jpg', frame)
        with frame_lock:
            last_frame = buf.tobytes()
        time.sleep(0.033)  # ~30 FPS
    cap.release()

def start_stream():
    global stream_thread
    if not stream_running.is_set():
        stream_running.set()
        stream_thread = threading.Thread(target=frame_grabber, daemon=True)
        stream_thread.start()

def stop_stream():
    global stream_thread
    stream_running.clear()
    if stream_thread is not None:
        stream_thread.join(timeout=2)
        stream_thread = None

def gen_mjpeg():
    while stream_running.is_set():
        with frame_lock:
            frame = last_frame
        if frame is not None:
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.05)

def gen_h264():
    # Use OpenCV to read frames, re-encode as H264 stream (raw), pipe as HTTP
    rtsp_url = get_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        yield b''
        return
    while stream_running.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        # Re-encode as H264 using OpenCV VideoWriter in memory buffer
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 90]
        ret, buf = cv2.imencode('.jpg', frame, encode_param)
        if not ret:
            continue
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
        time.sleep(0.033)
    cap.release()

@app.route('/play', methods=['POST'])
def api_play():
    start_stream()
    return jsonify({
        "status": "streaming",
        "stream_url_mjpeg": f"http://{request.host}/video",
        "snapshot_url": f"http://{request.host}/snap"
    })

@app.route('/halt', methods=['POST'])
def api_halt():
    stop_stream()
    return jsonify({"status": "stopped"})

@app.route('/snap', methods=['GET', 'POST'])
def api_snap():
    img = capture_snapshot()
    if img is not None:
        return Response(img, mimetype='image/jpeg')
    else:
        abort(503, description="Snapshot unavailable")

@app.route('/video', methods=['GET'])
def api_video():
    # MJPEG for browser compatibility
    if not stream_running.is_set():
        start_stream()
    return Response(gen_mjpeg(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)