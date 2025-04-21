import os
import io
import cv2
import uuid
import threading
from flask import Flask, request, jsonify, Response, abort

# ===== Environment Variable Configuration =====
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_PORT = os.environ.get("CAMERA_PORT", "554")
CAMERA_USER = os.environ.get("CAMERA_USER", "")
CAMERA_PASS = os.environ.get("CAMERA_PASS", "")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")  # Hikvision default main stream
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))
STREAM_FORMAT = os.environ.get("STREAM_FORMAT", "mjpeg").lower()  # 'mjpeg' or 'h264'

# ===== RTSP URL Construction =====
if CAMERA_USER and CAMERA_PASS:
    RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{CAMERA_PORT}/{RTSP_PATH}"
else:
    RTSP_URL = f"rtsp://{CAMERA_IP}:{CAMERA_PORT}/{RTSP_PATH}"

# ===== Flask App and Session Management =====
app = Flask(__name__)
sessions = {}
sessions_lock = threading.Lock()


class CameraStreamWorker(threading.Thread):
    def __init__(self, session_id, rtsp_url, stream_format):
        super().__init__()
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.stream_format = stream_format
        self.capture = None
        self.running = threading.Event()
        self.running.set()
        self.frame = None
        self.frame_lock = threading.Lock()

    def run(self):
        # OpenCV VideoCapture with RTSP
        self.capture = cv2.VideoCapture(self.rtsp_url)
        if not self.capture.isOpened():
            self.running.clear()
            return

        while self.running.is_set():
            ret, frame = self.capture.read()
            if not ret:
                continue
            with self.frame_lock:
                self.frame = frame
        self.capture.release()

    def get_frame(self):
        with self.frame_lock:
            if self.frame is None:
                return None
            if self.stream_format == 'mjpeg':
                ret, jpeg = cv2.imencode('.jpg', self.frame)
                if not ret:
                    return None
                return jpeg.tobytes()
            elif self.stream_format == 'h264':
                # For browser compatibility, MJPEG is preferred.
                # H.264 direct browser streaming requires special handling (e.g., mp4 wrapping).
                # Here, just return JPEG for simplicity.
                ret, jpeg = cv2.imencode('.jpg', self.frame)
                if not ret:
                    return None
                return jpeg.tobytes()
            else:
                return None

    def stop(self):
        self.running.clear()
        if self.capture:
            self.capture.release()


def generate_mjpeg(worker):
    while worker.running.is_set():
        frame = worker.get_frame()
        if frame is None:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')


# ===== API Endpoints =====

@app.route("/camera/stream/init", methods=["POST"])
def init_stream():
    session_id = str(uuid.uuid4())
    worker = CameraStreamWorker(session_id, RTSP_URL, STREAM_FORMAT)
    with sessions_lock:
        sessions[session_id] = worker
    worker.start()
    http_url = f"http://{request.host}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "http_stream_url": http_url
    })


@app.route("/camera/stream/live", methods=["GET"])
def live_stream():
    session_id = request.args.get("session_id")
    if not session_id:
        abort(400, "Missing session_id")
    with sessions_lock:
        worker = sessions.get(session_id)
    if not worker or not worker.running.is_set():
        abort(404, "Session not found or inactive")

    return Response(
        generate_mjpeg(worker),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route("/camera/stream/terminate", methods=["POST"])
def terminate_stream():
    session_id = request.args.get("session_id") or request.json.get("session_id")
    if not session_id:
        abort(400, "Missing session_id")
    with sessions_lock:
        worker = sessions.pop(session_id, None)
    if worker:
        worker.stop()
        return jsonify({"result": "terminated", "session_id": session_id})
    else:
        return jsonify({"result": "not_found", "session_id": session_id}), 404


@app.route("/stream/stop", methods=["POST"])
def stop_all_streams():
    with sessions_lock:
        for worker in sessions.values():
            worker.stop()
        sessions.clear()
    return jsonify({"result": "all_streams_stopped"})


if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)