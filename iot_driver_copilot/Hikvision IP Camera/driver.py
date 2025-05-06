import os
import threading
import queue
import time
import io

from flask import Flask, Response, request, jsonify

import cv2

app = Flask(__name__)

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
RTSP_STREAM_PATH = os.environ.get('RTSP_STREAM_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# RTSP URL construction
RTSP_URL = f'rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_STREAM_PATH}'

# Threading and state management
streaming_thread = None
frame_queue = queue.Queue(maxsize=10)
streaming_active = threading.Event()
stream_lock = threading.Lock()

def grab_frames():
    global frame_queue, streaming_active, RTSP_URL
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            streaming_active.clear()
            return
        while streaming_active.is_set():
            ret, frame = cap.read()
            if not ret:
                continue
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            frame_bytes = buffer.tobytes()
            try:
                if not frame_queue.full():
                    frame_queue.put(frame_bytes, block=False)
            except queue.Full:
                # Drop oldest frame to avoid latency buildup
                try:
                    frame_queue.get(block=False)
                    frame_queue.put(frame_bytes, block=False)
                except Exception:
                    pass
    finally:
        if cap is not None:
            cap.release()
        streaming_active.clear()

@app.route('/start', methods=['POST'])
def start_stream():
    with stream_lock:
        if not streaming_active.is_set():
            streaming_active.set()
            global streaming_thread
            streaming_thread = threading.Thread(target=grab_frames, daemon=True)
            streaming_thread.start()
            return jsonify({'status': 'stream started'}), 200
        else:
            return jsonify({'status': 'stream already running'}), 200

@app.route('/video/start', methods=['POST'])
def video_start():
    return start_stream()

@app.route('/stop', methods=['POST'])
def stop_stream():
    with stream_lock:
        if streaming_active.is_set():
            streaming_active.clear()
            # Clear frame queue
            while not frame_queue.empty():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    break
            return jsonify({'status': 'stream stopped'}), 200
        else:
            return jsonify({'status': 'stream not running'}), 200

@app.route('/video/stop', methods=['POST'])
def video_stop():
    return stop_stream()

def gen_frames():
    boundary = '--frame'
    while streaming_active.is_set():
        try:
            frame = frame_queue.get(timeout=2)
        except queue.Empty:
            continue
        yield (
            b'%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % (boundary.encode(), len(frame))
            + frame + b'\r\n'
        )

@app.route('/stream', methods=['GET'])
def video_stream():
    if not streaming_active.is_set():
        return jsonify({'error': 'Stream not started. Please POST to /start first.'}), 400
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=--frame'
    )

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)