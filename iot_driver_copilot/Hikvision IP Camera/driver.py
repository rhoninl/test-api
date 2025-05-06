import os
import threading
import queue
import time
import requests
from flask import Flask, Response, request, jsonify, send_file, abort
import cv2
import numpy as np

# Configuration via environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
RTSP_PORT = int(os.environ.get('RTSP_PORT', '554'))
RTSP_USER = os.environ.get('DEVICE_USER', 'admin')
RTSP_PASS = os.environ.get('DEVICE_PASS', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8000'))
SNAPSHOT_PORT = int(os.environ.get('SNAPSHOT_PORT', '80'))

RTSP_STREAM_PATH = os.environ.get('RTSP_STREAM_PATH', '/Streaming/Channels/101')
SNAPSHOT_PATH = os.environ.get('SNAPSHOT_PATH', '/ISAPI/Streaming/channels/101/picture')

# RTSP URL construction
RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{DEVICE_IP}:{RTSP_PORT}{RTSP_STREAM_PATH}"

# Snapshot URL construction
SNAPSHOT_URL = f"http://{DEVICE_IP}:{SNAPSHOT_PORT}{SNAPSHOT_PATH}"

# Flask App
app = Flask(__name__)

# Streaming session management
streaming_state = {
    "is_streaming": False,
    "clients": 0,
    "capture_thread": None,
    "frame_queue": queue.Queue(maxsize=10),
    "stop_event": threading.Event()
}

def rtsp_capture_worker():
    streaming_state['is_streaming'] = True
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        streaming_state['is_streaming'] = False
        return
    while not streaming_state['stop_event'].is_set():
        ret, frame = cap.read()
        if not ret:
            break
        # Encode frame as JPEG for browser-friendly streaming
        ret, jpg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        try:
            if streaming_state['frame_queue'].full():
                try:
                    streaming_state['frame_queue'].get_nowait()
                except queue.Empty:
                    pass
            streaming_state['frame_queue'].put(jpg.tobytes())
        except Exception:
            break
    cap.release()
    streaming_state['is_streaming'] = False

def start_stream():
    if not streaming_state['is_streaming']:
        streaming_state['stop_event'].clear()
        streaming_state['capture_thread'] = threading.Thread(target=rtsp_capture_worker, daemon=True)
        streaming_state['capture_thread'].start()

def stop_stream():
    streaming_state['stop_event'].set()
    if streaming_state['capture_thread'] and streaming_state['capture_thread'].is_alive():
        streaming_state['capture_thread'].join()
    streaming_state['is_streaming'] = False
    with streaming_state['frame_queue'].mutex:
        streaming_state['frame_queue'].queue.clear()

@app.route('/play', methods=['POST'])
def play_stream():
    start_stream()
    return jsonify({
        "status": "streaming_started",
        "video_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video"
    })

@app.route('/halt', methods=['POST'])
def halt_stream():
    stop_stream()
    return jsonify({"status": "streaming_stopped"})

@app.route('/video', methods=['GET'])
def video_feed():
    # Increment client count
    streaming_state['clients'] += 1
    start_stream()
    def generate():
        try:
            while not streaming_state['stop_event'].is_set():
                try:
                    frame = streaming_state['frame_queue'].get(timeout=2)
                except queue.Empty:
                    continue
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        finally:
            streaming_state['clients'] -= 1
            # If last client disconnects, stop stream
            if streaming_state['clients'] <= 0:
                stop_stream()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

def get_snapshot():
    try:
        resp = requests.get(SNAPSHOT_URL, auth=(RTSP_USER, RTSP_PASS), timeout=5)
        if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image'):
            return resp.content
        # Fallback: use RTSP stream and OpenCV
        cap = cv2.VideoCapture(RTSP_URL)
        ret, frame = cap.read()
        cap.release()
        if ret:
            ret, jpg = cv2.imencode('.jpg', frame)
            if ret:
                return jpg.tobytes()
    except Exception:
        pass
    return None

@app.route('/snap', methods=['GET', 'POST'])
def snapshot():
    img_bytes = get_snapshot()
    if img_bytes:
        return Response(img_bytes, mimetype='image/jpeg')
    return abort(503, description="Unable to get snapshot from camera.")

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)