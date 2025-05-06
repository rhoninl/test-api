import os
import io
import threading
import time
import queue
import cv2
from flask import Flask, Response, request, jsonify, send_file, abort

app = Flask(__name__)

# --- Environment Variables ---
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.64')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_RTSP_USER = os.environ.get('CAMERA_RTSP_USER', 'admin')
CAMERA_RTSP_PASS = os.environ.get('CAMERA_RTSP_PASS', '12345')
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '1')
CAMERA_STREAM_TYPE = os.environ.get('CAMERA_STREAM_TYPE', 'main')  # main or sub

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# --- RTSP URL Construction ---
def get_rtsp_url():
    stream_path = 'Streaming/Channels/{0}01'.format(CAMERA_CHANNEL)
    if CAMERA_STREAM_TYPE == 'sub':
        stream_path = 'Streaming/Channels/{0}02'.format(CAMERA_CHANNEL)
    return f'rtsp://{CAMERA_RTSP_USER}:{CAMERA_RTSP_PASS}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{stream_path}'

# --- Camera Video Stream Management ---
class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.lock = threading.Lock()
        self.running = False
        self.frame = None
        self.frame_time = 0
        self.subscribers = 0
        self.q = queue.Queue(maxsize=5)
        self.thread = None

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self._reader, daemon=True)
                self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            if self.capture is not None:
                self.capture.release()
                self.capture = None

    def subscribe(self):
        with self.lock:
            self.subscribers += 1
            if self.subscribers == 1:
                self.start()

    def unsubscribe(self):
        with self.lock:
            self.subscribers = max(0, self.subscribers - 1)
            if self.subscribers == 0:
                self.stop()

    def _reader(self):
        self.capture = cv2.VideoCapture(self.rtsp_url)
        while self.running and self.capture.isOpened():
            ret, frame = self.capture.read()
            if not ret:
                time.sleep(0.2)
                continue
            now = time.time()
            with self.lock:
                self.frame = frame
                self.frame_time = now
            try:
                # For MJPEG frame queue
                if not self.q.full():
                    self.q.put_nowait(frame)
            except queue.Full:
                pass
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def get_latest_frame(self):
        with self.lock:
            return self.frame

    def get_jpeg(self):
        frame = self.get_latest_frame()
        if frame is not None:
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                return jpeg.tobytes()
        return None

    def get_h264_stream(self):
        # Here, we simulate H.264 streaming via MJPEG as browsers do not natively support raw H.264 in HTTP.
        # For actual H.264 streaming, a compatible client (not browser) should consume raw frames.
        while self.running:
            frame = None
            try:
                frame = self.q.get(timeout=1)
            except queue.Empty:
                continue
            if frame is not None:
                ret, jpeg = cv2.imencode('.jpg', frame)
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            else:
                time.sleep(0.01)

# --- Global Camera Stream Instance ---
camera_stream = CameraStream(get_rtsp_url())

# --- API Routes ---

@app.route('/play', methods=['POST'])
def api_play():
    camera_stream.subscribe()
    return jsonify({
        'message': 'Streaming session started.',
        'video_url': '/video',
        'snapshot_url': '/snap'
    })

@app.route('/halt', methods=['POST'])
def api_halt():
    camera_stream.unsubscribe()
    return jsonify({
        'message': 'Streaming session halted.'
    })

@app.route('/video', methods=['GET'])
def api_video():
    camera_stream.subscribe()
    def generate():
        try:
            for frame in camera_stream.get_h264_stream():
                yield frame
        finally:
            camera_stream.unsubscribe()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/snap', methods=['GET', 'POST'])
def api_snap():
    camera_stream.subscribe()
    # Wait for a frame if not ready
    timeout = 5
    start_time = time.time()
    while camera_stream.get_latest_frame() is None and (time.time() - start_time) < timeout:
        time.sleep(0.1)
    jpeg = camera_stream.get_jpeg()
    camera_stream.unsubscribe()
    if jpeg:
        return Response(jpeg, mimetype='image/jpeg')
    else:
        abort(504, description="Snapshot timeout or camera unavailable.")

# --- Main Entrypoint ---
if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)