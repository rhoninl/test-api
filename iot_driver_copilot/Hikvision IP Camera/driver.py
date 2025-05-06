import os
import threading
import queue
import time
import base64
from flask import Flask, Response, request, jsonify, stream_with_context

import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
DEVICE_CHANNEL = os.environ.get("DEVICE_CHANNEL", "101")  # Default main stream

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", f"/Streaming/Channels/{DEVICE_CHANNEL}")

# Construct RTSP URL
RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}"

app = Flask(__name__)

class CameraStreamer:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.running = False
        self.thread = None
        self.frame_queue = queue.Queue(maxsize=10)
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.capture = cv2.VideoCapture(self.rtsp_url)
            self.thread = threading.Thread(target=self._update, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            if self.thread:
                self.thread.join(timeout=1)
            if self.capture is not None:
                self.capture.release()
                self.capture = None
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break

    def _update(self):
        while self.running:
            if self.capture is None or not self.capture.isOpened():
                break
            ret, frame = self.capture.read()
            if not ret:
                time.sleep(0.1)
                continue
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                self.frame_queue.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()  # Drop oldest frame
                    self.frame_queue.put(jpeg.tobytes(), timeout=1)
                except queue.Full:
                    pass

    def get_frame(self):
        try:
            return self.frame_queue.get(timeout=2)
        except queue.Empty:
            return None

    def is_running(self):
        with self.lock:
            return self.running

streamer = CameraStreamer(RTSP_URL)

@app.route('/start', methods=['POST'])
def api_start():
    streamer.start()
    return jsonify({"status": "started"}), 200

@app.route('/video/start', methods=['POST'])
def api_video_start():
    streamer.start()
    return jsonify({"status": "video streaming started"}), 200

@app.route('/stop', methods=['POST'])
def api_stop():
    streamer.stop()
    return jsonify({"status": "stopped"}), 200

@app.route('/video/stop', methods=['POST'])
def api_video_stop():
    streamer.stop()
    return jsonify({"status": "video streaming stopped"}), 200

def mjpeg_stream():
    while streamer.is_running():
        frame = streamer.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    # When stopped, signal end of stream
    yield b''

@app.route('/stream', methods=['GET'])
def api_stream():
    if not streamer.is_running():
        streamer.start()
    return Response(stream_with_context(mjpeg_stream()),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)