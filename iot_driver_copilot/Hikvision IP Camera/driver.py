import os
import threading
import queue
import time
import requests
from flask import Flask, Response, request, jsonify
import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', 554))
DEVICE_USER = os.environ.get('DEVICE_USER', 'admin')
DEVICE_PASS = os.environ.get('DEVICE_PASS', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

RTSP_PATH = os.environ.get('DEVICE_RTSP_PATH', '/Streaming/Channels/101')
RTSP_URL_TEMPLATE = "rtsp://{user}:{password}@{ip}:{port}{path}"

app = Flask(__name__)

class CameraStreamer:
    def __init__(self):
        self._stream_thread = None
        self._frame_queue = queue.Queue(maxsize=10)
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._rtsp_url = RTSP_URL_TEMPLATE.format(
            user=DEVICE_USER,
            password=DEVICE_PASS,
            ip=DEVICE_IP,
            port=DEVICE_RTSP_PORT,
            path=RTSP_PATH
        )
        self._last_frame = None

    def start(self):
        with self._lock:
            if self._stream_thread is None or not self._stream_thread.is_alive():
                self._running.set()
                self._stream_thread = threading.Thread(target=self._capture_loop, daemon=True)
                self._stream_thread.start()

    def stop(self):
        with self._lock:
            self._running.clear()
            if self._stream_thread is not None:
                self._stream_thread.join(timeout=3)
                self._stream_thread = None
            self._clear_queue()

    def _clear_queue(self):
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except Exception:
                break

    def _capture_loop(self):
        cap = cv2.VideoCapture(self._rtsp_url)
        if not cap.isOpened():
            self._running.clear()
            return

        while self._running.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                continue
            # We encode as JPEG for browser compatibility
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            data = jpeg.tobytes()
            self._last_frame = data
            try:
                self._frame_queue.put(data, block=False)
            except queue.Full:
                pass  # Drop frame if queue full

        cap.release()

    def frames(self):
        """Generator that yields JPEG frames."""
        while self._running.is_set():
            try:
                frame = self._frame_queue.get(timeout=1)
            except queue.Empty:
                if self._last_frame:
                    frame = self._last_frame
                else:
                    continue
            yield frame

    def is_running(self):
        return self._running.is_set()

camera = CameraStreamer()

def gen_mjpeg(camera: CameraStreamer):
    boundary = '--frame'
    for frame in camera.frames():
        yield (
            b'%s\r\n'
            b'Content-Type: image/jpeg\r\n'
            b'Content-Length: %d\r\n\r\n' % (boundary.encode(), len(frame))
        ) + frame + b'\r\n'

@app.route('/start', methods=['POST'])
@app.route('/video/start', methods=['POST'])
def start_stream():
    camera.start()
    return jsonify({'status': 'streaming started'}), 200

@app.route('/stop', methods=['POST'])
@app.route('/video/stop', methods=['POST'])
def stop_stream():
    camera.stop()
    return jsonify({'status': 'streaming stopped'}), 200

@app.route('/stream', methods=['GET'])
def stream_video():
    if not camera.is_running():
        camera.start()
        # Wait a bit for the stream to be ready
        time.sleep(1)
    return Response(
        gen_mjpeg(camera),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/', methods=['GET'])
def index():
    # Simple HTML player
    return '''
    <html>
      <head><title>Hikvision Camera Stream</title></head>
      <body>
        <h2>Hikvision Camera Live Stream</h2>
        <img src="/stream" style="width: 80vw; border: 2px solid #333;"/>
        <div>
          <form method="post" action="/start"><button>Start Stream</button></form>
          <form method="post" action="/stop"><button>Stop Stream</button></form>
        </div>
      </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)