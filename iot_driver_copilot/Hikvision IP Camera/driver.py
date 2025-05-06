import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify, stream_with_context

import cv2

# Environment variables for configuration
CAMERA_IP = os.environ.get('DEVICE_IP')
CAMERA_RTSP_PORT = os.environ.get('DEVICE_RTSP_PORT', '554')
CAMERA_USER = os.environ.get('DEVICE_USER', '')
CAMERA_PASS = os.environ.get('DEVICE_PASS', '')
CAMERA_RTSP_PATH = os.environ.get('DEVICE_RTSP_PATH', 'Streaming/Channels/101')
CAMERA_RTSP_PROTOCOL = os.environ.get('DEVICE_RTSP_PROTOCOL', 'rtsp')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8000'))
HTTP_STREAM_PATH = os.environ.get('HTTP_STREAM_PATH', '/video/stream')
MJPEG_FRAME_RATE = int(os.environ.get('MJPEG_FRAME_RATE', '15'))

# RTSP URL Construction
if CAMERA_USER and CAMERA_PASS:
    RTSP_URL = f"{CAMERA_RTSP_PROTOCOL}://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_RTSP_PATH}"
else:
    RTSP_URL = f"{CAMERA_RTSP_PROTOCOL}://{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_RTSP_PATH}"

app = Flask(__name__)

class VideoStreamer(threading.Thread):
    def __init__(self, rtsp_url, frame_rate=15):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.frame_rate = frame_rate
        self.running = threading.Event()
        self.running.clear()
        self.frame_queue = queue.Queue(maxsize=10)
        self.capture = None
        self.lock = threading.Lock()

    def run(self):
        while True:
            self.running.wait()
            with self.lock:
                if self.capture is None or not self.capture.isOpened():
                    self.capture = cv2.VideoCapture(self.rtsp_url)
                    if not self.capture.isOpened():
                        time.sleep(1)
                        continue
            while self.running.is_set():
                ret, frame = self.capture.read()
                if not ret:
                    time.sleep(0.1)
                    continue
                # Encode as JPEG
                ret, jpeg = cv2.imencode('.jpg', frame)
                if not ret:
                    continue
                try:
                    # Drop oldest if full
                    if self.frame_queue.full():
                        self.frame_queue.get_nowait()
                    self.frame_queue.put(jpeg.tobytes())
                except queue.Full:
                    pass
                time.sleep(1 / self.frame_rate)
            # Release capture on stop
            with self.lock:
                if self.capture:
                    self.capture.release()
                    self.capture = None
            # Clear any old frames
            with self.frame_queue.mutex:
                self.frame_queue.queue.clear()

    def start_stream(self):
        self.running.set()

    def stop_stream(self):
        self.running.clear()

    def get_frame(self):
        try:
            return self.frame_queue.get(timeout=1)
        except queue.Empty:
            return None

    def is_running(self):
        return self.running.is_set()

# Shared video streamer instance
video_streamer = VideoStreamer(RTSP_URL, MJPEG_FRAME_RATE)
video_streamer.daemon = True
video_streamer.start()

@app.route('/video/start', methods=['POST'])
def start_video():
    video_streamer.start_stream()
    return jsonify({"status": "started"}), 200

@app.route('/video/stop', methods=['POST'])
def stop_video():
    video_streamer.stop_stream()
    return jsonify({"status": "stopped"}), 200

@app.route(HTTP_STREAM_PATH, methods=['GET'])
def video_feed():
    if not video_streamer.is_running():
        # Automatically start if not started
        video_streamer.start_stream()
    def generate():
        while video_streamer.is_running():
            frame = video_streamer.get_frame()
            if frame is None:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    return Response(stream_with_context(generate()),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def info():
    return jsonify({
        "device": "Hikvision IP Camera",
        "stream_url": f"http://{SERVER_HOST}:{SERVER_PORT}{HTTP_STREAM_PATH}",
        "start_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video/start (POST)",
        "stop_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video/stop (POST)"
    })

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)