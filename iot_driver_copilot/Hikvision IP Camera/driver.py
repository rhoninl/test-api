import os
import io
import threading
import cv2
import time
from flask import Flask, Response, request, jsonify

# --- Configuration from environment variables ---
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASSWORD = os.environ.get("DEVICE_RTSP_PASSWORD", "12345")
DEVICE_RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{DEVICE_RTSP_PATH}"

# --- Stream State Management ---
class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.frame = None
        self.running = False
        self.lock = threading.Lock()
        self.thread = None

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self._update_stream, daemon=True)
                self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            if self.capture:
                self.capture.release()
                self.capture = None
            self.thread = None

    def _update_stream(self):
        self.capture = cv2.VideoCapture(self.rtsp_url)
        while self.running and self.capture.isOpened():
            ret, frame = self.capture.read()
            if not ret:
                time.sleep(0.1)
                continue
            with self.lock:
                self.frame = frame
        if self.capture:
            self.capture.release()
            self.capture = None

    def get_frame(self):
        with self.lock:
            if self.frame is not None:
                ret, jpeg = cv2.imencode('.jpg', self.frame)
                if ret:
                    return jpeg.tobytes()
            return None

camera_stream = CameraStream(RTSP_URL)

# --- Flask HTTP Server ---
app = Flask(__name__)

@app.route('/video/start', methods=['POST'])
def api_video_start():
    camera_stream.start()
    return jsonify({"status": "streaming_started"}), 200

@app.route('/video/stop', methods=['POST'])
def api_video_stop():
    camera_stream.stop()
    return jsonify({"status": "streaming_stopped"}), 200

def gen_mjpeg_stream():
    while True:
        if not camera_stream.running:
            time.sleep(0.2)
            continue
        frame = camera_stream.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.02)

@app.route('/video')
def video_feed():
    if not camera_stream.running:
        return "Stream not started. POST to /video/start first.", 503
    return Response(gen_mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)