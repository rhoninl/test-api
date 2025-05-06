import os
import io
import threading
import time
import queue
import base64
from flask import Flask, Response, request, send_file, jsonify
import requests
import cv2
import numpy as np

# Configuration from environment variables
CAMERA_HOST = os.environ.get("HIKVISION_HOST", "192.168.1.64")
CAMERA_RTSP_PORT = int(os.environ.get("HIKVISION_RTSP_PORT", "554"))
CAMERA_HTTP_PORT = int(os.environ.get("HIKVISION_HTTP_PORT", "80"))
CAMERA_USER = os.environ.get("HIKVISION_USER", "admin")
CAMERA_PASS = os.environ.get("HIKVISION_PASS", "12345")
SERVER_HOST = os.environ.get("DRIVER_HTTP_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("DRIVER_HTTP_PORT", "8080"))
STREAM_PATH = os.environ.get("HIKVISION_STREAM_PATH", "Streaming/Channels/101")
SNAPSHOT_PATH = os.environ.get("HIKVISION_SNAPSHOT_PATH", "Streaming/channels/1/picture")
RTSP_STREAM_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_HOST}:{CAMERA_RTSP_PORT}/{STREAM_PATH}"

app = Flask(__name__)

# Global variables for session management
stream_active = threading.Event()
stream_queue = queue.Queue(maxsize=100)
capture_thread = None

def mjpeg_generator():
    while stream_active.is_set():
        try:
            frame = stream_queue.get(timeout=2)
            if frame is None:
                break
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except queue.Empty:
            break

def h264_generator():
    while stream_active.is_set():
        try:
            data = stream_queue.get(timeout=2)
            if data is None:
                break
            yield data
        except queue.Empty:
            break

def stream_capture_worker():
    cap = cv2.VideoCapture(RTSP_STREAM_URL)
    if not cap.isOpened():
        return
    while stream_active.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.2)
            continue
        # For MJPEG streaming (/video), encode frame as JPEG
        ret2, jpeg = cv2.imencode('.jpg', frame)
        if ret2:
            # Only keep newest frames in the queue
            try:
                stream_queue.get_nowait()
            except queue.Empty:
                pass
            stream_queue.put(jpeg.tobytes())
    cap.release()
    # Signal generator to end
    stream_queue.put(None)

def h264_stream_worker():
    cap = cv2.VideoCapture(RTSP_STREAM_URL)
    if not cap.isOpened():
        return
    while stream_active.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.2)
            continue
        # Encode frame as H264
        ret2, h264 = cv2.imencode('.h264', frame)
        if ret2:
            try:
                stream_queue.get_nowait()
            except queue.Empty:
                pass
            stream_queue.put(h264.tobytes())
    cap.release()
    stream_queue.put(None)

def start_stream():
    global capture_thread
    if stream_active.is_set():
        return
    while not stream_queue.empty():
        try:
            stream_queue.get_nowait()
        except queue.Empty:
            break
    stream_active.set()
    capture_thread = threading.Thread(target=stream_capture_worker, daemon=True)
    capture_thread.start()

def start_h264_stream():
    global capture_thread
    if stream_active.is_set():
        return
    while not stream_queue.empty():
        try:
            stream_queue.get_nowait()
        except queue.Empty:
            break
    stream_active.set()
    capture_thread = threading.Thread(target=h264_stream_worker, daemon=True)
    capture_thread.start()

def stop_stream():
    stream_active.clear()
    # Clean up the queue
    while not stream_queue.empty():
        try:
            stream_queue.get_nowait()
        except queue.Empty:
            break

@app.route("/play", methods=["POST"])
def play():
    start_stream()
    return jsonify({"message": "Streaming started"}), 200

@app.route("/halt", methods=["POST"])
def halt():
    stop_stream()
    return jsonify({"message": "Streaming stopped"}), 200

@app.route("/video", methods=["GET"])
def video():
    # Start stream if not already started
    start_stream()
    return Response(mjpeg_generator(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/snap", methods=["GET", "POST"])
def snap():
    # Use camera's HTTP snapshot API
    url = f"http://{CAMERA_HOST}:{CAMERA_HTTP_PORT}/{SNAPSHOT_PATH}"
    try:
        resp = requests.get(url, auth=(CAMERA_USER, CAMERA_PASS), timeout=5, stream=True)
        if resp.status_code == 200:
            return Response(resp.content, mimetype='image/jpeg')
        else:
            return jsonify({"error": "Failed to retrieve snapshot", "status": resp.status_code}), 502
    except Exception as e:
        return jsonify({"error": "Exception getting snapshot", "exception": str(e)}), 500

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)