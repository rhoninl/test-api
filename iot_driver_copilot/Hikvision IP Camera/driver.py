import os
import io
import uuid
import threading
import queue
import time
from flask import Flask, request, jsonify, Response, abort

import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get('CAMERA_IP')
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', 554))
CAMERA_STREAM_PATH = os.environ.get('CAMERA_STREAM_PATH', 'Streaming/Channels/101')  # Default main stream
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

RTSP_URL_TEMPLATE = "rtsp://{username}:{password}@{ip}:{port}/{path}"

# Session management
sessions = {}
sessions_lock = threading.Lock()

app = Flask(__name__)

class CameraStreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=100)
        self.running = threading.Event()
        self.running.set()
        self.capture_thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.last_frame = None

    def start(self):
        self.capture_thread.start()

    def _capture_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.running.clear()
            return
        while self.running.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            self.last_frame = frame
            # Encode as JPEG for HTTP streaming
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                self.frame_queue.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
                self.frame_queue.put(jpeg.tobytes())
        cap.release()

    def get_frame(self):
        try:
            return self.frame_queue.get(timeout=5)
        except queue.Empty:
            if self.last_frame is not None:
                ret, jpeg = cv2.imencode('.jpg', self.last_frame)
                if ret:
                    return jpeg.tobytes()
            return None

    def stop(self):
        self.running.clear()
        self.capture_thread.join(timeout=2)

def get_rtsp_url():
    return RTSP_URL_TEMPLATE.format(
        username=CAMERA_USERNAME,
        password=CAMERA_PASSWORD,
        ip=CAMERA_IP,
        port=CAMERA_RTSP_PORT,
        path=CAMERA_STREAM_PATH
    )

@app.route('/camera/stream/init', methods=['POST'])
def stream_init():
    with sessions_lock:
        session_id = str(uuid.uuid4())
        rtsp_url = get_rtsp_url()
        session = CameraStreamSession(session_id, rtsp_url)
        session.start()
        sessions[session_id] = session
    stream_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "stream_url": stream_url
    })

@app.route('/camera/stream/live', methods=['GET'])
def stream_live():
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session_id"}), 404

    def generate():
        boundary = 'frame'
        while session.running.is_set():
            frame = session.get_frame()
            if frame is None:
                break
            yield (
                b'--' + boundary.encode() + b'\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' +
                frame + b'\r\n'
            )
        yield b''

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/camera/stream/terminate', methods=['POST'])
def stream_terminate():
    session_id = request.args.get('session_id') or request.json.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if not session:
        return jsonify({"error": "Invalid session_id"}), 404
    session.stop()
    return jsonify({"status": "terminated", "session_id": session_id})

@app.route('/stream/stop', methods=['POST'])
def stream_stop():
    # For compatibility, stop all active sessions
    with sessions_lock:
        stopped_sessions = list(sessions.keys())
        for session_id in stopped_sessions:
            session = sessions.pop(session_id)
            session.stop()
    return jsonify({"stopped_sessions": stopped_sessions, "status": "all streams stopped"})

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)