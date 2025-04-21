import os
import uuid
import threading
import time
from flask import Flask, request, Response, jsonify, abort
import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get('CAMERA_IP')
CAMERA_PORT = os.environ.get('CAMERA_RTSP_PORT', '554')
CAMERA_USER = os.environ.get('CAMERA_USER')
CAMERA_PASS = os.environ.get('CAMERA_PASS')
CAMERA_STREAM = os.environ.get('CAMERA_STREAM', 'Streaming/Channels/101')
RTSP_TRANSPORT = os.environ.get('RTSP_TRANSPORT', 'tcp')  # 'tcp' or 'udp'
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

app = Flask(__name__)

# Session management for streams
sessions = {}
sessions_lock = threading.Lock()

def build_rtsp_url():
    if CAMERA_USER and CAMERA_PASS:
        return f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{CAMERA_PORT}/{CAMERA_STREAM}"
    else:
        return f"rtsp://{CAMERA_IP}:{CAMERA_PORT}/{CAMERA_STREAM}"

class CameraStream:
    def __init__(self, rtsp_url, transport='tcp'):
        self.rtsp_url = rtsp_url
        self.transport = transport
        self.capture = None
        self.running = False
        self.lock = threading.Lock()
        self.last_frame = None
        self.thread = None

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.capture = cv2.VideoCapture(self._build_opencv_url(), cv2.CAP_FFMPEG)
            self.thread = threading.Thread(target=self._update, daemon=True)
            self.thread.start()

    def _build_opencv_url(self):
        # OpenCV does not support explicit transport selection, but some builds support ?rtsp_transport=xxx
        # We'll try to use it if provided
        return f"{self.rtsp_url}?rtsp_transport={self.transport}"

    def _update(self):
        while self.running and self.capture and self.capture.isOpened():
            ret, frame = self.capture.read()
            if not ret:
                time.sleep(0.1)
                continue
            with self.lock:
                self.last_frame = frame
        if self.capture:
            self.capture.release()

    def read_mjpeg(self):
        while self.running:
            with self.lock:
                frame = self.last_frame
            if frame is not None:
                ret, jpeg = cv2.imencode('.jpg', frame)
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(0.04)  # ~25FPS

    def stop(self):
        with self.lock:
            self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.capture:
            self.capture.release()

@app.route('/camera/stream/init', methods=['POST'])
def init_stream():
    session_id = str(uuid.uuid4())
    rtsp_url = build_rtsp_url()
    cam_stream = CameraStream(rtsp_url, RTSP_TRANSPORT)
    cam_stream.start()
    with sessions_lock:
        sessions[session_id] = cam_stream
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        'session_id': session_id,
        'http_url': http_url
    })

@app.route('/camera/stream/live', methods=['GET'])
def stream_live():
    session_id = request.args.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id")
    with sessions_lock:
        cam_stream = sessions.get(session_id)
    if not cam_stream:
        abort(404, description="Session not found")
    return Response(cam_stream.read_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/camera/stream/terminate', methods=['POST'])
def terminate_stream():
    data = request.get_json(silent=True)
    session_id = None
    if data and 'session_id' in data:
        session_id = data['session_id']
    else:
        session_id = request.args.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id")
    with sessions_lock:
        cam_stream = sessions.pop(session_id, None)
    if cam_stream:
        cam_stream.stop()
        return jsonify({'status': 'terminated', 'session_id': session_id})
    else:
        abort(404, description="Session not found")

@app.route('/stream/stop', methods=['POST'])
def stop_all_streams():
    with sessions_lock:
        session_ids = list(sessions.keys())
        for session_id in session_ids:
            cam_stream = sessions.pop(session_id, None)
            if cam_stream:
                cam_stream.stop()
    return jsonify({'status': 'all streams stopped'})

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)