import os
import io
import threading
import queue
import time
from flask import Flask, Response, stream_with_context, request, jsonify

import cv2

# Environment variables
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.100')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
CAMERA_STREAM_PATH = os.environ.get('CAMERA_STREAM_PATH', '/Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8000'))

RTSP_URL = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{CAMERA_STREAM_PATH}"

app = Flask(__name__)

# Stream control
streaming_state = {
    "active": False,
    "thread": None,
    "frame_queue": None,
    "cv_capture": None,
    "stop_event": None,
}

def video_capture_worker(rtsp_url, frame_queue, stop_event):
    cap = cv2.VideoCapture(rtsp_url)
    streaming_state["cv_capture"] = cap
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        try:
            # Limit queue size to prevent memory leak
            if frame_queue.qsize() < 10:
                frame_queue.put(jpeg.tobytes())
            else:
                # Drop frames if queue is full
                pass
        except Exception:
            break
    cap.release()

def start_stream():
    if streaming_state["active"]:
        return
    streaming_state["frame_queue"] = queue.Queue()
    streaming_state["stop_event"] = threading.Event()
    t = threading.Thread(
        target=video_capture_worker,
        args=(RTSP_URL, streaming_state["frame_queue"], streaming_state["stop_event"]),
        daemon=True,
    )
    streaming_state["thread"] = t
    streaming_state["active"] = True
    t.start()

def stop_stream():
    if not streaming_state["active"]:
        return
    streaming_state["stop_event"].set()
    # Wait a bit for the worker to clean up
    time.sleep(0.5)
    streaming_state["active"] = False
    streaming_state["thread"] = None
    streaming_state["frame_queue"] = None
    if streaming_state.get("cv_capture") is not None:
        try:
            streaming_state["cv_capture"].release()
        except Exception:
            pass
        streaming_state["cv_capture"] = None

def mjpeg_stream_generator():
    while streaming_state["active"]:
        try:
            frame = streaming_state["frame_queue"].get(timeout=1)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    # When stopped, finish gracefully

@app.route('/video/start', methods=['POST'])
def api_video_start():
    start_stream()
    return jsonify({"status": "stream_started"}), 200

@app.route('/video/stop', methods=['POST'])
def api_video_stop():
    stop_stream()
    return jsonify({"status": "stream_stopped"}), 200

@app.route('/video/stream', methods=['GET'])
def video_stream():
    if not streaming_state["active"]:
        return jsonify({"error": "Stream is not active. POST /video/start first."}), 503
    return Response(
        stream_with_context(mjpeg_stream_generator()),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/')
def index():
    return '''
    <html>
      <head><title>Hikvision Camera Stream</title></head>
      <body>
        <h1>Live Camera Stream</h1>
        <img src="/video/stream" width="640" height="480"/>
        <form action="/video/start" method="post"><button type="submit">Start Stream</button></form>
        <form action="/video/stop" method="post"><button type="submit">Stop Stream</button></form>
      </body>
    </html>
    '''

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)