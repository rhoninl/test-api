import os
import threading
import queue
import time
from flask import Flask, Response, request, abort

import cv2

app = Flask(__name__)

DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_STREAM_PORT = int(os.environ.get("DEVICE_STREAM_PORT", "554"))
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))
RTSP_PATH = os.environ.get("RTSP_PATH", "/Streaming/Channels/101")
RTSP_PROTOCOL = os.environ.get("RTSP_PROTOCOL", "rtsp")

STREAM_URI = f"{RTSP_PROTOCOL}://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_STREAM_PORT}{RTSP_PATH}"

streaming_thread = None
frame_queue = queue.Queue(maxsize=10)
streaming_active = threading.Event()
thread_lock = threading.Lock()

def stream_worker():
    cap = None
    try:
        cap = cv2.VideoCapture(STREAM_URI)
        if not cap.isOpened():
            streaming_active.clear()
            return
        while streaming_active.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                if frame_queue.full():
                    frame_queue.get_nowait()
                frame_queue.put_nowait(jpeg.tobytes())
            except queue.Full:
                continue
            time.sleep(0.03)
    finally:
        if cap:
            cap.release()
        streaming_active.clear()
        with frame_queue.mutex:
            frame_queue.queue.clear()

@app.route("/stream", methods=["POST"])
def start_stream():
    with thread_lock:
        if streaming_active.is_set():
            return {"status": "stream already active"}, 200
        streaming_active.set()
        global streaming_thread
        streaming_thread = threading.Thread(target=stream_worker, daemon=True)
        streaming_thread.start()
    return {"status": "stream started"}, 200

@app.route("/stream", methods=["GET"])
def get_stream():
    if not streaming_active.is_set():
        abort(404, description="Stream not active. Start with POST /stream.")
    
    def generate():
        while streaming_active.is_set():
            try:
                frame = frame_queue.get(timeout=2)
            except queue.Empty:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/stream", methods=["DELETE"])
def stop_stream():
    with thread_lock:
        if streaming_active.is_set():
            streaming_active.clear()
            if streaming_thread and streaming_thread.is_alive():
                streaming_thread.join(timeout=2)
            return {"status": "stream stopped"}, 200
        else:
            return {"status": "stream not active"}, 200

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)