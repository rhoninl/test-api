import os
import threading
import uuid
import time
from flask import Flask, Response, request, jsonify, abort
import cv2

# Environment variables for configuration
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USERNAME = os.environ.get("CAMERA_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "Streaming/Channels/101")
CAMERA_STREAM_TYPE = os.environ.get("CAMERA_STREAM_TYPE", "h264")  # h264 or mjpeg

HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))

# Session management
sessions = {}
sessions_lock = threading.Lock()


def build_rtsp_url():
    return f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"


class CameraSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.active = True
        self.last_access = time.time()
        self.lock = threading.Lock()
        self.cap = None

    def open(self):
        self.cap = cv2.VideoCapture(self.rtsp_url)

    def close(self):
        self.active = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    def keepalive(self):
        with self.lock:
            self.last_access = time.time()

    def read_frame(self):
        if self.cap is None or not self.cap.isOpened():
            self.open()
        ret, frame = self.cap.read()
        if not ret or frame is None:
            return None
        if CAMERA_STREAM_TYPE.lower() == "mjpeg":
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                return None
            return buffer.tobytes()
        else:  # h264, we will encode as JPEG for browser compatibility
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                return None
            return buffer.tobytes()


def create_session():
    session_id = str(uuid.uuid4())
    rtsp_url = build_rtsp_url()
    session = CameraSession(session_id, rtsp_url)
    with sessions_lock:
        sessions[session_id] = session
    return session


def get_session(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
    return session


def remove_session(session_id):
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.close()


def cleanup_sessions(timeout=60):
    while True:
        now = time.time()
        with sessions_lock:
            expired = [sid for sid, sess in sessions.items() if now - sess.last_access > timeout]
        for sid in expired:
            remove_session(sid)
        time.sleep(10)


# Background thread for session cleanup
cleanup_thread = threading.Thread(target=cleanup_sessions, args=(120,), daemon=True)
cleanup_thread.start()

app = Flask(__name__)


@app.route('/camera/stream/init', methods=['POST'])
def init_stream():
    session = create_session()
    # Start the OpenCV VideoCapture as a test; if failed, abort
    try:
        session.open()
        if not session.cap or not session.cap.isOpened():
            remove_session(session.session_id)
            return jsonify({"error": "Unable to connect to camera."}), 500
    except Exception as e:
        remove_session(session.session_id)
        return jsonify({"error": str(e)}), 500
    # Compose HTTP URL for the stream
    http_url = f"http://{request.host}/camera/stream/live?session={session.session_id}"
    return jsonify({
        "session_id": session.session_id,
        "stream_url": http_url
    })


def gen_mjpeg_stream(session):
    boundary = "--frame"
    while session.active:
        frame = session.read_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        session.keepalive()
        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
        )


@app.route('/camera/stream/live', methods=['GET'])
def live_stream():
    session_id = request.args.get('session')
    if not session_id:
        return jsonify({"error": "Session id is required"}), 400
    session = get_session(session_id)
    if not session or not session.active:
        return jsonify({"error": "Invalid or inactive session"}), 404
    session.keepalive()
    return Response(gen_mjpeg_stream(session),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/camera/stream/terminate', methods=['POST'])
def terminate_stream():
    data = request.json or request.form or request.args
    session_id = data.get('session') or data.get('session_id')
    if not session_id:
        return jsonify({"error": "Session id is required"}), 400
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Invalid session id"}), 404
    remove_session(session_id)
    return jsonify({"result": "Session terminated"})


@app.route('/stream/stop', methods=['POST'])
def stop_stream():
    # Accept session id in POST body or query
    data = request.json or request.form or request.args
    session_id = data.get('session') or data.get('session_id')
    if not session_id:
        return jsonify({"error": "Session id is required"}), 400
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "Invalid session id"}), 404
    remove_session(session_id)
    return jsonify({"result": "Stream stopped"})


if __name__ == '__main__':
    app.run(host=HTTP_HOST, port=HTTP_PORT, threaded=True)