import os
import uuid
import threading
import queue
import time
from flask import Flask, request, Response, jsonify, abort

import cv2

# Environment Variables
DEVICE_IP = os.environ.get("DEVICE_IP")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", 554))
DEVICE_RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", "/Streaming/Channels/101")
DEVICE_USER = os.environ.get("DEVICE_USER", "admin")
DEVICE_PASS = os.environ.get("DEVICE_PASS", "admin123")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

MJPEG_FPS = int(os.environ.get("MJPEG_FPS", "10"))

if not DEVICE_IP:
    raise ValueError("DEVICE_IP environment variable not set.")

# Session Manager
class StreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.active = True
        self.frame_queue = queue.Queue(maxsize=5)
        self.thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.thread.start()

    def _capture_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.active = False
            return
        while self.active:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            if not self.active:
                break
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                self.frame_queue.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                pass  # Drop frame if queue is full
            time.sleep(1.0 / MJPEG_FPS)
        cap.release()

    def get_frame(self, timeout=5):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self.active = False
        try:
            while not self.frame_queue.empty():
                self.frame_queue.get_nowait()
        except Exception:
            pass

# Global session registry
sessions = {}
sessions_lock = threading.Lock()

app = Flask(__name__)

def build_rtsp_url():
    userpass = ""
    if DEVICE_USER and DEVICE_PASS:
        userpass = f"{DEVICE_USER}:{DEVICE_PASS}@"
    return f"rtsp://{userpass}{DEVICE_IP}:{DEVICE_RTSP_PORT}{DEVICE_RTSP_PATH}"

# API: /camera/stream/init
@app.route('/camera/stream/init', methods=['POST'])
def init_stream():
    session_id = str(uuid.uuid4())
    rtsp_url = build_rtsp_url()
    with sessions_lock:
        if session_id in sessions:
            abort(500, description="Session collision.")
        session = StreamSession(session_id, rtsp_url)
        sessions[session_id] = session
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "http_stream_url": http_url
    }), 200

# API: /camera/stream/live
@app.route('/camera/stream/live', methods=['GET'])
def live_stream():
    session_id = request.args.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id parameter.")
    with sessions_lock:
        session = sessions.get(session_id)
    if not session or not session.active:
        abort(404, description="Session not found or inactive.")

    def gen():
        boundary = "--frame"
        while session.active:
            frame = session.get_frame(timeout=5)
            if frame is None:
                break
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
        yield b"--frame--\r\n"

    return Response(
        gen(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

# API: /camera/stream/terminate
@app.route('/camera/stream/terminate', methods=['POST'])
def terminate_stream():
    session_id = request.args.get('session_id')
    if not session_id:
        if request.is_json:
            payload = request.get_json()
            session_id = payload.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id.")
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return jsonify({"success": True, "message": "Session terminated."}), 200
    else:
        return jsonify({"success": False, "message": "Session not found."}), 404

# API: /stream/stop (alias of terminate)
@app.route('/stream/stop', methods=['POST'])
def stop_stream():
    session_id = request.args.get('session_id')
    if not session_id:
        if request.is_json:
            payload = request.get_json()
            session_id = payload.get('session_id')
    if not session_id:
        abort(400, description="Missing session_id.")
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return jsonify({"success": True, "message": "Session stopped."}), 200
    else:
        return jsonify({"success": False, "message": "Session not found."}), 404

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)