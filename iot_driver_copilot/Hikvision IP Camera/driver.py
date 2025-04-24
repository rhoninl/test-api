import os
import threading
import queue
import time
from flask import Flask, Response, jsonify, request

import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
RTSP_PORT = int(os.environ.get('RTSP_PORT', '554'))
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')
# e.g., /Streaming/Channels/101

# Compose RTSP URL
RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"

app = Flask(__name__)

streaming_thread = None
streaming_active = threading.Event()
frame_queue = queue.Queue(maxsize=10)

def rtsp_to_http_stream():
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            return
        while streaming_active.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                frame_queue.put(jpeg.tobytes(), timeout=0.1)
            except queue.Full:
                continue
        cap.release()
    except Exception:
        if cap:
            cap.release()
        return

def stream_generator():
    while streaming_active.is_set():
        try:
            frame = frame_queue.get(timeout=1)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/start', methods=['POST'])
def start_stream():
    global streaming_thread
    if streaming_active.is_set():
        return jsonify({'status': 'already streaming', 'stream_url': f'http://{SERVER_HOST}:{SERVER_PORT}/video'}), 200

    streaming_active.set()
    # Clear frame queue before starting
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break
    streaming_thread = threading.Thread(target=rtsp_to_http_stream, daemon=True)
    streaming_thread.start()
    return jsonify({'status': 'stream started', 'stream_url': f'http://{SERVER_HOST}:{SERVER_PORT}/video'}), 200

@app.route('/stop', methods=['POST'])
def stop_stream():
    if not streaming_active.is_set():
        return jsonify({'status': 'no stream active'}), 200
    streaming_active.clear()
    # Wait for thread to finish
    if streaming_thread is not None:
        streaming_thread.join(timeout=2)
    # Clear frame queue
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break
    return jsonify({'status': 'stream stopped'}), 200

@app.route('/video')
def video_feed():
    if not streaming_active.is_set():
        return jsonify({'error': 'stream not started'}), 400
    return Response(stream_generator(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)