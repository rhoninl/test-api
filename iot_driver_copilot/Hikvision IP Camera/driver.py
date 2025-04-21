import os
import io
import threading
import uuid
import time
from flask import Flask, Response, request, jsonify, abort, stream_with_context
import cv2

# --- Environment Configuration ---
DEVICE_IP = os.environ.get("HIKVISION_IP")
DEVICE_RTSP_PORT = int(os.environ.get("HIKVISION_RTSP_PORT", 554))
DEVICE_USERNAME = os.environ.get("HIKVISION_USERNAME")
DEVICE_PASSWORD = os.environ.get("HIKVISION_PASSWORD")
RTSP_PATH = os.environ.get("HIKVISION_RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT", 600))

if not (DEVICE_IP and DEVICE_USERNAME and DEVICE_PASSWORD):
    raise RuntimeError("HIKVISION_IP, HIKVISION_USERNAME, and HIKVISION_PASSWORD env vars are required.")

# --- Session Management ---
class StreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.active = True
        self.last_access = time.time()
        self.capture = None
        self.lock = threading.Lock()

    def update_access(self):
        self.last_access = time.time()

    def open(self):
        self.capture = cv2.VideoCapture(self.rtsp_url)
        return self.capture.isOpened()

    def close(self):
        with self.lock:
            self.active = False
            if self.capture:
                self.capture.release()
                self.capture = None

sessions = {}
sessions_lock = threading.Lock()

def session_cleanup():
    while True:
        now = time.time()
        with sessions_lock:
            to_remove = [s_id for s_id, sess in sessions.items()
                         if not sess.active or (now - sess.last_access > SESSION_TIMEOUT)]
            for s_id in to_remove:
                try:
                    sessions[s_id].close()
                except Exception:
                    pass
                del sessions[s_id]
        time.sleep(20)

cleanup_thread = threading.Thread(target=session_cleanup, daemon=True)
cleanup_thread.start()

# --- Flask App ---
app = Flask(__name__)

def make_rtsp_url():
    return f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_PATH}"

@app.route("/camera/stream/init", methods=["POST"])
def camera_stream_init():
    session_id = str(uuid.uuid4())
    rtsp_url = make_rtsp_url()
    session = StreamSession(session_id, rtsp_url)
    if not session.open():
        return jsonify({"error": "Failed to connect to camera RTSP stream."}), 502
    with sessions_lock:
        sessions[session_id] = session
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "stream_url": http_url
    })

def generate_mjpeg(session: StreamSession):
    boundary = "--frame"
    while session.active:
        with session.lock:
            ret, frame = session.capture.read()
        if not ret:
            break
        session.update_access()
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        yield (b"%s\r\n"
               b"Content-Type: image/jpeg\r\n"
               b"Content-Length: %d\r\n\r\n" % (boundary.encode(), len(jpeg.tobytes())))
        yield jpeg.tobytes()
        yield b"\r\n"
        time.sleep(0.04)  # ~25fps

@app.route("/camera/stream/live", methods=["GET"])
def camera_stream_live():
    session_id = request.args.get("session_id")
    if not session_id:
        abort(400, "Missing session_id")
    with sessions_lock:
        session = sessions.get(session_id)
    if not session or not session.active:
        abort(404, "Session not found or inactive")
    session.update_access()
    return Response(
        stream_with_context(generate_mjpeg(session)),
        mimetype='multipart/x-mixed-replace; boundary=--frame'
    )

@app.route("/camera/stream/terminate", methods=["POST"])
def camera_stream_terminate():
    session_id = request.args.get("session_id") or (request.get_json() or {}).get("session_id")
    if not session_id:
        abort(400, "Missing session_id")
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.close()
        return jsonify({"status": "terminated", "session_id": session_id})
    else:
        return jsonify({"status": "not_found", "session_id": session_id}), 404

@app.route("/stream/stop", methods=["POST"])
def stream_stop():
    return camera_stream_terminate()

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)