import os
import threading
import time
import queue
import requests
from flask import Flask, Response, request, jsonify, stream_with_context

import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Camera Stream Path (adjust if needed)
RTSP_STREAM_PATH = os.environ.get(
    "RTSP_STREAM_PATH",
    f"/Streaming/Channels/101"
)

# MJPEG stream buffer size
FRAME_QUEUE_SIZE = int(os.environ.get("FRAME_QUEUE_SIZE", "10"))

# Flask app
app = Flask(__name__)

# Global streaming state
streaming_enabled = threading.Event()
streaming_enabled.clear()
frame_queue = queue.Queue(maxsize=FRAME_QUEUE_SIZE)

# Thread handle
capture_thread = None

def get_rtsp_url():
    return f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_STREAM_PATH}"

def capture_frames():
    rtsp_url = get_rtsp_url()
    cap = None
    reconnect_delay = 5
    while streaming_enabled.is_set():
        if not cap or not cap.isOpened():
            cap = cv2.VideoCapture(rtsp_url)
            if not cap.isOpened():
                time.sleep(reconnect_delay)
                continue
        ret, frame = cap.read()
        if not ret:
            cap.release()
            time.sleep(reconnect_delay)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            try:
                frame_queue.put(jpeg.tobytes(), block=False)
            except queue.Full:
                pass  # Drop frame if queue is full
        else:
            continue
    if cap and cap.isOpened():
        cap.release()
    frame_queue.queue.clear()

def start_stream():
    global capture_thread
    if not streaming_enabled.is_set():
        streaming_enabled.set()
        capture_thread = threading.Thread(target=capture_frames, daemon=True)
        capture_thread.start()
        return True
    return False

def stop_stream():
    streaming_enabled.clear()
    # Wait for thread to finish and clear frames
    if capture_thread:
        capture_thread.join(timeout=3)
    frame_queue.queue.clear()
    return True

@app.route('/video/start', methods=['POST'])
def api_start_stream():
    if start_stream():
        return jsonify({"status": "started"}), 200
    else:
        return jsonify({"status": "already running"}), 200

@app.route('/video/stop', methods=['POST'])
def api_stop_stream():
    if streaming_enabled.is_set():
        stop_stream()
        return jsonify({"status": "stopped"}), 200
    else:
        return jsonify({"status": "already stopped"}), 200

def mjpeg_generator():
    while streaming_enabled.is_set():
        try:
            frame = frame_queue.get(timeout=3)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    # If streaming stops, end stream
    yield (b'--frame\r\n'
           b'Content-Type: text/plain\r\n\r\nStream Ended\r\n')

@app.route('/video')
def video_feed():
    if not streaming_enabled.is_set():
        return jsonify({"error": "Stream is not started"}), 503
    return Response(
        stream_with_context(mjpeg_generator()),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/')
def index():
    return '''
    <html>
    <head>
        <title>Hikvision IP Camera Stream</title>
    </head>
    <body>
        <h1>Hikvision Camera Live Stream</h1>
        <img src="/video" width="640" />
        <form method="post" action="/video/start">
            <button type="submit">Start Stream</button>
        </form>
        <form method="post" action="/video/stop">
            <button type="submit">Stop Stream</button>
        </form>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)