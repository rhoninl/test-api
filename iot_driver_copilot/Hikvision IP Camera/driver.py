import os
import uuid
import threading
import queue
import time
from flask import Flask, request, Response, jsonify

import cv2

# ==== Configuration from environment variables ====
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.64')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
RTSP_STREAM_PATH = os.environ.get('RTSP_STREAM_PATH', 'Streaming/Channels/101')
CAMERA_PROTOCOL = os.environ.get('CAMERA_PROTOCOL', 'rtsp')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# ==== RTSP URL Construction ====
RTSP_URL = f"{CAMERA_PROTOCOL}://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{RTSP_STREAM_PATH}"

# ==== Session Management ====
sessions = {}
sessions_lock = threading.Lock()

class StreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=10)
        self.running = threading.Event()
        self.running.set()
        self.capture_thread = threading.Thread(target=self._capture_frames)
        self.capture_thread.daemon = True
        self.last_access = time.time()

    def start(self):
        self.capture_thread.start()

    def stop(self):
        self.running.clear()
        self.capture_thread.join(timeout=2)
        with self.frame_queue.mutex:
            self.frame_queue.queue.clear()

    def _capture_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            return
        while self.running.is_set():
            ret, frame = cap.read()
            if not ret:
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                self.frame_queue.put(jpeg.tobytes(), timeout=0.5)
            except queue.Full:
                pass
            self.last_access = time.time()
        cap.release()

    def get_frame(self):
        try:
            frame = self.frame_queue.get(timeout=2)
            self.frame_queue.task_done()
            return frame
        except queue.Empty:
            return None

    def update_access(self):
        self.last_access = time.time()

    def is_inactive(self, timeout=60):
        return (time.time() - self.last_access) > timeout

# ==== Flask App ====
app = Flask(__name__)

def cleanup_sessions():
    while True:
        with sessions_lock:
            inactive_sessions = [sid for sid, s in sessions.items() if s.is_inactive()]
            for sid in inactive_sessions:
                s = sessions.pop(sid)
                s.stop()
        time.sleep(10)

cleanup_thread = threading.Thread(target=cleanup_sessions)
cleanup_thread.daemon = True
cleanup_thread.start()

# ==== API Endpoints ====

@app.route('/camera/stream/init', methods=['POST'])
def init_stream():
    session_id = str(uuid.uuid4())
    stream_session = StreamSession(session_id, RTSP_URL)
    with sessions_lock:
        sessions[session_id] = stream_session
    stream_session.start()
    http_stream_url = f"http://{request.host}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "stream_url": http_stream_url
    }), 200

@app.route('/camera/stream/live', methods=['GET'])
def live_stream():
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid or expired session"}), 404

    session.update_access()

    def gen():
        boundary = "--frame"
        while session.running.is_set():
            frame = session.get_frame()
            if frame is None:
                continue
            yield (b'%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % (boundary.encode(), len(frame)) + frame + b'\r\n')
    headers = {
        'Content-Type': 'multipart/x-mixed-replace; boundary=--frame'
    }
    return Response(gen(), headers=headers)

@app.route('/camera/stream/terminate', methods=['POST'])
def terminate_stream():
    session_id = request.args.get('session_id') or request.json.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return jsonify({"status": "terminated"}), 200
    else:
        return jsonify({"error": "Session not found"}), 404

@app.route('/stream/stop', methods=['POST'])
def stop_stream():
    session_id = request.args.get('session_id') or request.json.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return jsonify({"status": "stopped"}), 200
    else:
        return jsonify({"error": "Session not found"}), 404

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)