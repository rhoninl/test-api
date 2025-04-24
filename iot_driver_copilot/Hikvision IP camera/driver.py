import os
import threading
import queue
import time
import io

from flask import Flask, Response, request, jsonify

import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get('CAMERA_IP')
CAMERA_RTSP_PORT = os.environ.get('CAMERA_RTSP_PORT', '554')
CAMERA_USER = os.environ.get('CAMERA_USER', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '101')
RTSP_PATH = os.environ.get('CAMERA_RTSP_PATH', f'/Streaming/Channels/{CAMERA_CHANNEL}')
RTSP_TRANSPORT = os.environ.get('CAMERA_RTSP_TRANSPORT', 'tcp')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

# RTSP URL construction
RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{RTSP_PATH}"

app = Flask(__name__)

# Control state and stream
streaming_event = threading.Event()
frame_queue = queue.Queue(maxsize=100)
stream_thread = None

def stream_worker():
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError("Failed to connect to camera stream.")
        while streaming_event.is_set():
            ret, frame = cap.read()
            if not ret:
                continue
            # Encode as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            # Put into queue, drop oldest if full
            try:
                frame_queue.put(jpeg.tobytes(), timeout=0.2)
            except queue.Full:
                try:
                    frame_queue.get_nowait()
                    frame_queue.put(jpeg.tobytes(), timeout=0.2)
                except queue.Empty:
                    pass
    finally:
        if cap is not None:
            cap.release()

def start_stream():
    global stream_thread
    if not streaming_event.is_set():
        streaming_event.set()
        stream_thread = threading.Thread(target=stream_worker, daemon=True)
        stream_thread.start()

def stop_stream():
    streaming_event.clear()
    # Clear frame queue
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break

@app.route('/stream', methods=['POST'])
def activate_stream():
    if streaming_event.is_set():
        return jsonify({"status": "already streaming"}), 200
    start_stream()
    return jsonify({"status": "stream started"}), 200

def gen_mjpeg():
    while streaming_event.is_set():
        try:
            frame = frame_queue.get(timeout=1)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except queue.Empty:
            continue

@app.route('/stream', methods=['GET'])
def get_stream():
    if not streaming_event.is_set():
        return jsonify({"error": "stream is not active. Please POST /stream to start."}), 404
    return Response(gen_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stream', methods=['DELETE'])
def terminate_stream():
    if not streaming_event.is_set():
        return jsonify({"status": "stream not active"}), 200
    stop_stream()
    return jsonify({"status": "stream stopped"}), 200

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)