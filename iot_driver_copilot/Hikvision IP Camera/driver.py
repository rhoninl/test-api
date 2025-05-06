import os
import io
import threading
import time
from flask import Flask, Response, request, jsonify, stream_with_context
import requests
import cv2
import numpy as np

# Configuration via environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_USER = os.environ.get('DEVICE_USER', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
SNAPSHOT_PORT = int(os.environ.get('SNAPSHOT_PORT', '80'))  # HTTP port for snapshot

# RTSP Stream URL (Mainstream)
RTSP_URL = f"rtsp://{DEVICE_USER}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/Streaming/Channels/101"

# Snapshot URL (JPEG still)
SNAPSHOT_URL = f"http://{DEVICE_IP}:{SNAPSHOT_PORT}/ISAPI/Streaming/channels/101/picture"

# Camera streaming state
streaming_sessions = {}
streaming_lock = threading.Lock()

app = Flask(__name__)

def get_rtsp_video_capture():
    cap = cv2.VideoCapture(RTSP_URL)
    return cap if cap.isOpened() else None

def mjpeg_stream():
    cap = get_rtsp_video_capture()
    if cap is None:
        yield b''
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            frame_bytes = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        cap.release()

def h264_stream():
    cap = get_rtsp_video_capture()
    if cap is None:
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            ret, encoded = cv2.imencode('.h264', frame)
            if not ret:
                continue
            yield encoded.tobytes()
    finally:
        cap.release()

def get_snapshot():
    try:
        response = requests.get(SNAPSHOT_URL, auth=(DEVICE_USER, DEVICE_PASSWORD), timeout=5)
        if response.status_code == 200 and response.headers['Content-Type'] in ['image/jpeg', 'image/jpg']:
            return response.content
    except Exception:
        pass
    # Fallback to grab from RTSP stream
    cap = get_rtsp_video_capture()
    if cap is None:
        return None
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    ret, jpeg = cv2.imencode('.jpg', frame)
    if not ret:
        return None
    return jpeg.tobytes()

@app.route('/play', methods=['POST'])
def play():
    session_id = request.form.get('session_id') or request.args.get('session_id') or str(time.time())
    with streaming_lock:
        streaming_sessions[session_id] = True
    return jsonify({
        "session_id": session_id,
        "stream_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video?session_id={session_id}",
        "mjpeg_url": f"http://{SERVER_HOST}:{SERVER_PORT}/mjpeg?session_id={session_id}",
        "message": "Streaming session started."
    })

@app.route('/halt', methods=['POST'])
def halt():
    session_id = request.form.get('session_id') or request.args.get('session_id')
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    with streaming_lock:
        streaming_sessions.pop(session_id, None)
    return jsonify({"message": "Streaming session halted.", "session_id": session_id})

@app.route('/video', methods=['GET'])
def video():
    session_id = request.args.get('session_id')
    if session_id and session_id not in streaming_sessions:
        return jsonify({"error": "Invalid or inactive session_id"}), 403
    return Response(h264_stream(), mimetype='video/H264')

@app.route('/mjpeg', methods=['GET'])
def mjpeg():
    session_id = request.args.get('session_id')
    if session_id and session_id not in streaming_sessions:
        return jsonify({"error": "Invalid or inactive session_id"}), 403
    return Response(mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/snap', methods=['GET', 'POST'])
def snap():
    img_bytes = get_snapshot()
    if not img_bytes:
        return jsonify({"error": "Failed to retrieve snapshot"}), 500
    return Response(img_bytes, mimetype='image/jpeg')

@app.route('/')
def root():
    return jsonify({
        "device": "Hikvision IP Camera Driver",
        "endpoints": [
            {"method": "POST", "path": "/play", "description": "Start streaming session"},
            {"method": "POST", "path": "/halt", "description": "Stop streaming session"},
            {"method": "GET", "path": "/video", "description": "H.264 raw video stream"},
            {"method": "GET", "path": "/mjpeg", "description": "MJPEG stream for browser"},
            {"method": "GET/POST", "path": "/snap", "description": "JPEG snapshot"}
        ]
    })

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)