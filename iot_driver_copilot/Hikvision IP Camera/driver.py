import os
import io
import threading
import time
from flask import Flask, Response, request, send_file, jsonify
import requests
import cv2
import numpy as np

# Load configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
DEVICE_HTTP_PORT = int(os.environ.get('DEVICE_HTTP_PORT', '80'))

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_PATH = os.environ.get('DEVICE_RTSP_PATH', 'Streaming/Channels/101')
RTSP_URL = f'rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_PATH}'

SNAPSHOT_PATH = os.environ.get('DEVICE_SNAPSHOT_PATH', 'ISAPI/Streaming/channels/101/picture')
SNAPSHOT_URL = f'http://{DEVICE_IP}:{DEVICE_HTTP_PORT}/{SNAPSHOT_PATH}'

# Globals for session management
streaming_active = threading.Event()
video_thread = None
frame_lock = threading.Lock()
last_frame = None

app = Flask(__name__)

def fetch_rtsp_stream():
    global last_frame
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        streaming_active.clear()
        return

    while streaming_active.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        with frame_lock:
            last_frame = frame
        time.sleep(0.01)
    cap.release()

def gen_mjpeg_stream():
    global last_frame
    while streaming_active.is_set():
        with frame_lock:
            frame = last_frame.copy() if last_frame is not None else None
        if frame is not None:
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        else:
            time.sleep(0.01)

def gen_h264_stream():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        yield b''
        return
    try:
        while streaming_active.is_set():
            ret, frame = cap.read()
            if not ret:
                continue
            ret, buf = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield buf.tobytes()
            time.sleep(0.04)
    finally:
        cap.release()

@app.route('/play', methods=['POST'])
def play():
    global video_thread
    if not streaming_active.is_set():
        streaming_active.set()
        video_thread = threading.Thread(target=fetch_rtsp_stream, daemon=True)
        video_thread.start()
    return jsonify({
        'message': 'Streaming started',
        'video_url': '/video'
    }), 200

@app.route('/halt', methods=['POST'])
def halt():
    if streaming_active.is_set():
        streaming_active.clear()
    return jsonify({'message': 'Streaming stopped'}), 200

@app.route('/snap', methods=['GET', 'POST'])
def snap():
    # Try to get JPEG snapshot from ISAPI
    try:
        resp = requests.get(SNAPSHOT_URL, auth=(DEVICE_USERNAME, DEVICE_PASSWORD), timeout=5)
        if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image/jpeg'):
            return Response(resp.content, mimetype='image/jpeg')
    except Exception:
        pass
    # If failed, fallback to grab from RTSP stream
    cap = cv2.VideoCapture(RTSP_URL)
    ret, frame = cap.read()
    cap.release()
    if ret:
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            return Response(jpeg.tobytes(), mimetype='image/jpeg')
    return jsonify({'error': 'Snapshot failed'}), 500

@app.route('/video', methods=['GET'])
def video():
    # Serve MJPEG stream (browser friendly)
    if not streaming_active.is_set():
        # Optionally start streaming automatically
        streaming_active.set()
        global video_thread
        video_thread = threading.Thread(target=fetch_rtsp_stream, daemon=True)
        video_thread.start()
    return Response(gen_mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)