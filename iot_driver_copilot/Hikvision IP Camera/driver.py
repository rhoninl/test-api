import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify
import cv2
import base64

# Environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", 554))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASS = os.environ.get("DEVICE_RTSP_PASS", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))

RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", "/Streaming/Channels/101")
RTSP_PROTOCOL = os.environ.get("DEVICE_RTSP_PROTOCOL", "rtsp")
RTSP_URL = f"{RTSP_PROTOCOL}://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}"

app = Flask(__name__)

stream_thread = None
stream_queue = queue.Queue(maxsize=10)
streaming_active = threading.Event()

def video_stream_worker(rtsp_url, stream_queue, streaming_active):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        streaming_active.clear()
        return
    while streaming_active.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        # Put to queue, drop oldest if full
        try:
            stream_queue.put(jpeg.tobytes(), timeout=0.2)
        except queue.Full:
            try:
                stream_queue.get_nowait()
                stream_queue.put(jpeg.tobytes(), timeout=0.2)
            except queue.Empty:
                pass
    cap.release()

def start_streaming():
    global stream_thread
    if not streaming_active.is_set():
        streaming_active.set()
        while not stream_queue.empty():
            stream_queue.get()
        stream_thread = threading.Thread(target=video_stream_worker, args=(RTSP_URL, stream_queue, streaming_active))
        stream_thread.daemon = True
        stream_thread.start()

def stop_streaming():
    streaming_active.clear()
    while not stream_queue.empty():
        stream_queue.get()

def generate_mjpeg():
    while streaming_active.is_set():
        try:
            frame = stream_queue.get(timeout=2)
        except queue.Empty:
            break
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    stop_streaming()

@app.route('/start', methods=['POST'])
def api_start():
    start_streaming()
    return jsonify({
        "status": "streaming_started",
        "stream_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video"
    }), 200

@app.route('/stop', methods=['POST'])
def api_stop():
    stop_streaming()
    return jsonify({"status": "streaming_stopped"}), 200

@app.route('/video')
def video_feed():
    if not streaming_active.is_set():
        return "Streaming not active. POST to /start first.", 400
    return Response(generate_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)