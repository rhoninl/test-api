import os
import io
import threading
import time
import cv2
import numpy as np
from flask import Flask, Response, request, jsonify

# --- Configuration from environment variables ---
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# RTSP stream path, typical for Hikvision cameras, may need adjustment
RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')

# --- Flask App and Global State ---
app = Flask(__name__)

# Global streaming control and frame buffer
streaming = {
    "active": False,
    "thread": None,
    "frame": None,
    "lock": threading.Lock(),
    "stop_event": threading.Event(),
}

def get_rtsp_url():
    return f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}"

# --- Video Capture and Streaming Logic ---
def capture_frames():
    rtsp_url = get_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        with streaming["lock"]:
            streaming["frame"] = None
        return

    while not streaming["stop_event"].is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        # Store the latest frame
        with streaming["lock"]:
            streaming["frame"] = frame
        time.sleep(0.01)  # ~100 FPS, can adjust as needed

    cap.release()
    with streaming["lock"]:
        streaming["frame"] = None

# --- HTTP MJPEG Stream Generator ---
def mjpeg_stream():
    while True:
        if not streaming["active"]:
            break
        with streaming["lock"]:
            frame = streaming["frame"].copy() if streaming["frame"] is not None else None
        if frame is not None:
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        else:
            time.sleep(0.05)

# --- REST API Endpoints ---

@app.route('/start', methods=['POST'])
def start_stream():
    if streaming["active"]:
        return jsonify({
            "status": "already_running",
            "message": "Stream is already active.",
            "stream_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video"
        }), 200

    streaming["active"] = True
    streaming["stop_event"].clear()
    streaming["thread"] = threading.Thread(target=capture_frames, daemon=True)
    streaming["thread"].start()
    # Wait a moment for the thread to start and capture initial frame
    time.sleep(1)
    return jsonify({
        "status": "started",
        "message": "Video stream started.",
        "stream_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video"
    }), 200

@app.route('/stop', methods=['POST'])
def stop_stream():
    if not streaming["active"]:
        return jsonify({"status": "already_stopped", "message": "Stream is not active."}), 200

    streaming["stop_event"].set()
    streaming["active"] = False
    if streaming["thread"] is not None:
        streaming["thread"].join(timeout=2)
        streaming["thread"] = None
    with streaming["lock"]:
        streaming["frame"] = None
    return jsonify({"status": "stopped", "message": "Video stream stopped."}), 200

@app.route('/video', methods=['GET'])
def video_feed():
    if not streaming["active"]:
        return jsonify({"error": "Video stream is not started. Use /start to begin."}), 400
    return Response(mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "streaming": streaming["active"],
        "has_frame": streaming["frame"] is not None,
    }), 200

# --- Main Entrypoint ---
if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)