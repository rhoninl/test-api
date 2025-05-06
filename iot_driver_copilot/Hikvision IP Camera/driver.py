import os
import io
import threading
import queue
import time
from flask import Flask, Response, jsonify, request
import cv2

# ====== Environment Variables ======
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
# ===================================

RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

# ====== Streaming State Management ======
streaming_enabled = threading.Event()
streaming_enabled.clear()
frame_queue = queue.Queue(maxsize=10)
stream_thread = None
stop_thread = threading.Event()

def stream_worker():
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            return
        while not stop_thread.is_set() and streaming_enabled.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                frame_queue.put_nowait(jpeg.tobytes())
            except queue.Full:
                continue
            time.sleep(0.02)
    finally:
        if cap:
            cap.release()

def start_streaming():
    global stream_thread
    if streaming_enabled.is_set():
        return False
    streaming_enabled.set()
    stop_thread.clear()
    # Clear the queue to avoid stale frames
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break
    stream_thread = threading.Thread(target=stream_worker, daemon=True)
    stream_thread.start()
    return True

def stop_streaming():
    streaming_enabled.clear()
    stop_thread.set()
    # Drain the queue
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break
    return True

# ====== API Endpoints ======

@app.route('/start', methods=['POST'])
def api_start():
    started = start_streaming()
    return jsonify({"success": started, "message": "Streaming started" if started else "Already streaming"}), 200

@app.route('/video/start', methods=['POST'])
def api_video_start():
    started = start_streaming()
    return jsonify({"success": started, "message": "Video stream started" if started else "Already streaming"}), 200

@app.route('/stop', methods=['POST'])
def api_stop():
    stopped = stop_streaming()
    return jsonify({"success": stopped, "message": "Streaming stopped"}), 200

@app.route('/video/stop', methods=['POST'])
def api_video_stop():
    stopped = stop_streaming()
    return jsonify({"success": stopped, "message": "Video stream stopped"}), 200

def gen_mjpeg():
    while streaming_enabled.is_set():
        try:
            frame = frame_queue.get(timeout=1)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    # When stream stopped, finish generator

@app.route('/stream', methods=['GET'])
def video_feed():
    if not streaming_enabled.is_set():
        return jsonify({"error": "Streaming not started"}), 400
    return Response(gen_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ====== Main ======
if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)