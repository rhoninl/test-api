import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify, stream_with_context
import cv2

# Configuration from environment
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USER = os.environ.get("DEVICE_USER", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
RTSP_STREAM_PATH = os.environ.get("RTSP_STREAM_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{DEVICE_USER}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_STREAM_PATH}"

app = Flask(__name__)

class VideoStreamManager:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.frame_queue = queue.Queue(maxsize=10)
        self.running = False
        self.lock = threading.Lock()
        self.thread = None

    def start_stream(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self._capture_frames, daemon=True)
                self.thread.start()
                return True
            return False

    def stop_stream(self):
        with self.lock:
            self.running = False
        # Clear the queue
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break
        # Release capture
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def _capture_frames(self):
        self.capture = cv2.VideoCapture(self.rtsp_url)
        if not self.capture.isOpened():
            self.running = False
            return
        while self.running:
            ret, frame = self.capture.read()
            if not ret:
                time.sleep(0.1)
                continue
            # Encode as JPEG for HTTP streaming
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            data = buffer.tobytes()
            try:
                if self.frame_queue.full():
                    self.frame_queue.get_nowait()
                self.frame_queue.put_nowait(data)
            except queue.Full:
                pass
        self.capture.release()
        self.capture = None

    def get_frame(self, timeout=2):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

video_manager = VideoStreamManager(RTSP_URL)

@app.route('/start', methods=['POST'])
def start_cmd():
    started = video_manager.start_stream()
    return jsonify({'status': 'ok', 'started': started})

@app.route('/video/start', methods=['POST'])
def start_video():
    started = video_manager.start_stream()
    return jsonify({'status': 'ok', 'started': started})

@app.route('/stop', methods=['POST'])
def stop_cmd():
    video_manager.stop_stream()
    return jsonify({'status': 'ok', 'stopped': True})

@app.route('/video/stop', methods=['POST'])
def stop_video():
    video_manager.stop_stream()
    return jsonify({'status': 'ok', 'stopped': True})

def generate_mjpeg():
    while video_manager.running:
        frame = video_manager.get_frame()
        if frame is None:
            time.sleep(0.1)
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/stream', methods=['GET'])
def stream_video():
    if not video_manager.running:
        video_manager.start_stream()
    return Response(stream_with_context(generate_mjpeg()),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)