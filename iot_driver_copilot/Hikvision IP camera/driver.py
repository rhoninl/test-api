import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify, abort
import cv2

# Config from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')
RTSP_USERNAME = os.environ.get('RTSP_USERNAME', '')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))
MJPEG_FPS = int(os.environ.get('MJPEG_FPS', 20))

if not DEVICE_IP:
    raise EnvironmentError("DEVICE_IP environment variable must be set.")

# RTSP URL construction
def build_rtsp_url():
    userinfo = ''
    if RTSP_USERNAME and RTSP_PASSWORD:
        userinfo = f"{RTSP_USERNAME}:{RTSP_PASSWORD}@"
    elif RTSP_USERNAME:
        userinfo = f"{RTSP_USERNAME}@"
    return f"rtsp://{userinfo}{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"

app = Flask(__name__)

# Shared state for streaming
stream_active = threading.Event()
frame_queue = queue.Queue(maxsize=10)
stream_thread = None
thread_lock = threading.Lock()

def camera_stream_worker(rtsp_url):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        stream_active.clear()
        return
    while stream_active.is_set():
        ret, frame = cap.read()
        if not ret:
            break
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        # Drop frames if queue is full
        try:
            frame_queue.put(jpeg.tobytes(), timeout=0.1)
        except queue.Full:
            continue
        # Throttle for MJPEG FPS
        time.sleep(1.0 / MJPEG_FPS)
    cap.release()
    frame_queue.queue.clear()

@app.route('/stream', methods=['POST'])
def start_stream():
    global stream_thread
    with thread_lock:
        if stream_active.is_set():
            return jsonify({"status": "already streaming"}), 200
        rtsp_url = build_rtsp_url()
        stream_active.set()
        stream_thread = threading.Thread(target=camera_stream_worker, args=(rtsp_url,), daemon=True)
        stream_thread.start()
        # Wait a bit to ensure stream starts
        time.sleep(0.5)
        if not stream_active.is_set():
            stream_active.clear()
            return jsonify({"status": "failed to start stream"}), 500
    return jsonify({"status": "stream started"}), 201

def mjpeg_stream_generator():
    while stream_active.is_set():
        try:
            frame = frame_queue.get(timeout=2)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except queue.Empty:
            continue

@app.route('/stream', methods=['GET'])
def get_stream():
    if not stream_active.is_set():
        abort(404, "Stream not running. POST to /stream to activate.")
    return Response(mjpeg_stream_generator(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stream', methods=['DELETE'])
def stop_stream():
    with thread_lock:
        if not stream_active.is_set():
            return jsonify({"status": "no stream running"}), 200
        stream_active.clear()
        # Wait for worker to finish
        if stream_thread and stream_thread.is_alive():
            stream_thread.join(timeout=2)
        frame_queue.queue.clear()
    return jsonify({"status": "stream stopped"}), 200

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)