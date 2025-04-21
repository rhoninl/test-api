import os
import threading
import queue
import cv2
from flask import Flask, Response, request, jsonify, abort
from dotenv import load_dotenv

load_dotenv()

# Configuration from environment variables
CAMERA_IP = os.getenv('CAMERA_IP')
CAMERA_RTSP_PORT = os.getenv('CAMERA_RTSP_PORT', '554')
CAMERA_USER = os.getenv('CAMERA_USER', 'admin')
CAMERA_PASSWORD = os.getenv('CAMERA_PASSWORD', '12345')
CAMERA_CHANNEL = os.getenv('CAMERA_CHANNEL', '1')
RTSP_STREAM_PATH = os.getenv('RTSP_STREAM_PATH', f'/Streaming/Channels/{CAMERA_CHANNEL}01')
RTSP_TRANSPORT = os.getenv('RTSP_TRANSPORT', 'tcp')
HTTP_SERVER_HOST = os.getenv('HTTP_SERVER_HOST', '0.0.0.0')
HTTP_SERVER_PORT = int(os.getenv('HTTP_SERVER_PORT', 8000))

app = Flask(__name__)

class RTSPStreamProxy:
    def __init__(self):
        self.rtsp_url = f'rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{RTSP_STREAM_PATH}'
        self.transport = RTSP_TRANSPORT
        self._capture = None
        self._frame_queue = queue.Queue(maxsize=10)
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._thread = threading.Thread(target=self._reader, daemon=True)
            self._thread.start()
        return True

    def stop(self):
        with self._lock:
            self._running = False
            if self._capture:
                self._capture.release()
                self._capture = None
            while not self._frame_queue.empty():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass
        return True

    def _reader(self):
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        self._capture = cap
        while self._running and cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                continue
            # Keep only latest frame
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self._frame_queue.put(frame)
        cap.release()
        self._capture = None

    def get_frame(self):
        try:
            frame = self._frame_queue.get(timeout=2)
            return frame
        except queue.Empty:
            return None

stream_proxy = RTSPStreamProxy()

@app.route('/stream', methods=['PUT'])
def start_stream():
    ok = stream_proxy.start()
    if ok:
        return jsonify({"status": "started"}), 200
    else:
        abort(500, "Failed to start stream")

@app.route('/stream', methods=['DELETE'])
def stop_stream():
    ok = stream_proxy.stop()
    if ok:
        return jsonify({"status": "stopped"}), 200
    else:
        abort(500, "Failed to stop stream")

def mjpeg_generator():
    while stream_proxy._running:
        frame = stream_proxy.get_frame()
        if frame is None:
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')

@app.route('/stream', methods=['GET'])
def stream_video():
    if not stream_proxy._running:
        abort(404, "Stream is not started. Use PUT /stream to start.")
    return Response(mjpeg_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/snapshot', methods=['GET'])
def get_snapshot():
    if not stream_proxy._running:
        # Temporarily start, grab one frame, stop
        stream_proxy.start()
        threading.Event().wait(1.5)  # Give time to fill frame queue
        frame = stream_proxy.get_frame()
        stream_proxy.stop()
    else:
        frame = stream_proxy.get_frame()
    if frame is None:
        abort(500, "Unable to capture snapshot")
    ret, jpeg = cv2.imencode('.jpg', frame)
    if not ret:
        abort(500, "JPEG encoding failed")
    return Response(jpeg.tobytes(), mimetype='image/jpeg')

if __name__ == '__main__':
    app.run(host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT, threaded=True)