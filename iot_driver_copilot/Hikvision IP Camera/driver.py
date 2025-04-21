import os
import io
import uuid
import threading
import queue
import time
from flask import Flask, Response, request, jsonify, abort, stream_with_context

import cv2

# --- CONFIGURATION FROM ENVIRONMENT VARIABLES ---
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USERNAME = os.environ.get("CAMERA_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "Streaming/Channels/101")
CAMERA_STREAM_TYPE = os.environ.get("CAMERA_STREAM_TYPE", "h264")  # h264 or mjpeg

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

# --- SESSION AND STREAM MANAGEMENT ---
app = Flask(__name__)
sessions = {}
sessions_lock = threading.Lock()

class CameraStreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.running = False
        self.frame_queue = queue.Queue(maxsize=10)
        self.thread = None
        self.last_active = time.time()

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._capture_frames, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        self.thread = None
        # Drain queue
        while not self.frame_queue.empty():
            self.frame_queue.get_nowait()

    def _capture_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.running = False
            return
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        while self.running:
            ret, frame = cap.read()
            if not ret:
                break
            if CAMERA_STREAM_TYPE.lower() == 'h264':
                # Encode as JPEG for HTTP stream
                ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
                if not ret:
                    continue
                frame_bytes = jpeg.tobytes()
            elif CAMERA_STREAM_TYPE.lower() == 'mjpeg':
                ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
                if not ret:
                    continue
                frame_bytes = jpeg.tobytes()
            else:
                continue
            try:
                # Drop frame if queue is full
                if self.frame_queue.full():
                    self.frame_queue.get_nowait()
                self.frame_queue.put_nowait(frame_bytes)
                self.last_active = time.time()
            except queue.Full:
                pass
        cap.release()
        self.running = False

    def get_frame(self, timeout=5):
        try:
            frame = self.frame_queue.get(timeout=timeout)
            return frame
        except queue.Empty:
            return None

def build_rtsp_url():
    return f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"

def get_session(session_id):
    with sessions_lock:
        return sessions.get(session_id)

def cleanup_inactive_sessions(timeout=60):
    while True:
        with sessions_lock:
            now = time.time()
            to_remove = []
            for sid, sess in sessions.items():
                if not sess.running or (now - sess.last_active) > timeout:
                    sess.stop()
                    to_remove.append(sid)
            for sid in to_remove:
                sessions.pop(sid, None)
        time.sleep(15)

# --- API ENDPOINTS ---

@app.route('/camera/stream/init', methods=['POST'])
def init_stream():
    session_id = str(uuid.uuid4())
    rtsp_url = build_rtsp_url()
    session = CameraStreamSession(session_id, rtsp_url)
    session.start()
    with sessions_lock:
        sessions[session_id] = session
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"

    return jsonify({
        "session_id": session_id,
        "stream_url": http_url
    })

@app.route('/camera/stream/live', methods=['GET'])
def stream_live():
    session_id = request.args.get("session_id")
    if not session_id:
        abort(400, description="session_id is required")
    session = get_session(session_id)
    if not session or not session.running:
        abort(404, description="Session not found or not running")

    def generate():
        boundary = "--frame"
        while session.running:
            frame = session.get_frame(timeout=10)
            if frame is None:
                break
            yield (b'%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % (boundary.encode(), len(frame)))
            yield frame
            yield b"\r\n"
        yield b"--frame--\r\n"
    headers = {
        'Content-Type': 'multipart/x-mixed-replace; boundary=frame'
    }
    return Response(stream_with_context(generate()), headers=headers)

@app.route('/camera/stream/terminate', methods=['POST'])
def terminate_stream():
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id') or request.args.get('session_id')
    if not session_id:
        abort(400, description="session_id is required")
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return jsonify({"status": "terminated", "session_id": session_id})
    else:
        abort(404, description="Session not found")

@app.route('/stream/stop', methods=['POST'])
def stop_stream():
    # This can be used to stop all streams
    with sessions_lock:
        for session in sessions.values():
            session.stop()
        sessions.clear()
    return jsonify({"status": "all streams stopped"})

# --- BACKGROUND CLEANUP THREAD ---
cleanup_thread = threading.Thread(target=cleanup_inactive_sessions, args=(60,), daemon=True)
cleanup_thread.start()

# --- MAIN ENTRY ---
if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)