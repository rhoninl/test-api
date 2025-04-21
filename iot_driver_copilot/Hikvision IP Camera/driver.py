import os
import uuid
import threading
import queue
import time
from flask import Flask, Response, request, jsonify, abort

import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get('CAMERA_IP')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', 554))
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '101')
# RTSP URL pattern
RTSP_URL_TEMPLATE = os.environ.get(
    'CAMERA_RTSP_URL_TEMPLATE',
    'rtsp://{username}:{password}@{ip}:{port}/Streaming/Channels/{channel}'
)
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

# Flask app
app = Flask(__name__)

# In-memory session management
sessions = {}
sessions_lock = threading.Lock()

class CameraStreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=10)
        self.active = threading.Event()
        self.active.set()
        self.thread = threading.Thread(target=self._stream_frames, daemon=True)
        self.thread.start()

    def _stream_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.active.clear()
            return
        try:
            while self.active.is_set():
                ret, frame = cap.read()
                if not ret:
                    break
                # Encode frame to jpeg
                ret, jpeg = cv2.imencode('.jpg', frame)
                if not ret:
                    continue
                # Push to queue
                try:
                    self.frame_queue.put(jpeg.tobytes(), timeout=1)
                except queue.Full:
                    pass
        finally:
            cap.release()
            self.active.clear()

    def get_frame(self, timeout=2):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self.active.clear()
        self.thread.join(timeout=2)

def make_rtsp_url():
    return RTSP_URL_TEMPLATE.format(
        username=CAMERA_USERNAME,
        password=CAMERA_PASSWORD,
        ip=CAMERA_IP,
        port=CAMERA_RTSP_PORT,
        channel=CAMERA_CHANNEL
    )

@app.route('/camera/stream/init', methods=['POST'])
def init_stream():
    session_id = str(uuid.uuid4())
    rtsp_url = make_rtsp_url()
    with sessions_lock:
        session = CameraStreamSession(session_id, rtsp_url)
        if not session.active.is_set():
            abort(500, description="Failed to connect to camera stream.")
        sessions[session_id] = session
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "http_stream_url": http_url
    })

def gen_mjpeg(session):
    boundary = "--frame"
    while session.active.is_set():
        frame_bytes = session.get_frame()
        if frame_bytes is None:
            time.sleep(0.05)
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" +
            frame_bytes +
            b"\r\n"
        )
    # End stream
    yield b"--frame--\r\n"

@app.route('/camera/stream/live', methods=['GET'])
def stream_live():
    session_id = request.args.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id")
    with sessions_lock:
        session = sessions.get(session_id)
        if not session or not session.active.is_set():
            abort(404, description="Session not found or inactive")
    return Response(
        gen_mjpeg(session),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/camera/stream/terminate', methods=['POST'])
def terminate_stream():
    session_id = request.args.get('session_id') or request.json.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id")
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return jsonify({"status": "terminated", "session_id": session_id})
    else:
        abort(404, description="Session not found")

@app.route('/stream/stop', methods=['POST'])
def stop_all_streams():
    with sessions_lock:
        for session in list(sessions.values()):
            session.stop()
        sessions.clear()
    return jsonify({"status": "all streams stopped"})

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)