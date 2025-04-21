import os
import uuid
import threading
import time
from flask import Flask, Response, request, jsonify, abort, stream_with_context
import cv2

app = Flask(__name__)

# Environment variable configuration
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.64')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '1')
CAMERA_STREAM_TYPE = os.environ.get('CAMERA_STREAM_TYPE', 'main')  # 'main' or 'sub'
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# In-memory session storage
sessions = {}

def build_rtsp_url():
    stream_id = '1' if CAMERA_STREAM_TYPE == 'main' else '2'
    return f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/Streaming/Channels/{CAMERA_CHANNEL}0{stream_id}"

class VideoStreamSession:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.lock = threading.Lock()
        self.active = False

    def start(self):
        with self.lock:
            if not self.active:
                self.capture = cv2.VideoCapture(self.rtsp_url)
                if not self.capture.isOpened():
                    raise RuntimeError("Failed to open RTSP stream")
                self.active = True

    def get_frame(self):
        with self.lock:
            if not self.active or self.capture is None:
                raise RuntimeError("Stream not active")
            ret, frame = self.capture.read()
            if not ret:
                raise RuntimeError("Failed to read frame from stream")
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                raise RuntimeError("Failed to encode frame")
            return jpeg.tobytes()

    def stop(self):
        with self.lock:
            if self.capture:
                self.capture.release()
                self.capture = None
            self.active = False

@app.route("/camera/stream/init", methods=["POST"])
def init_stream():
    session_id = str(uuid.uuid4())
    rtsp_url = build_rtsp_url()
    try:
        session = VideoStreamSession(rtsp_url)
        session.start()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    sessions[session_id] = session
    stream_url = f"http://{request.host}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "success": True,
        "session_id": session_id,
        "stream_url": stream_url
    })

@app.route("/camera/stream/live", methods=["GET"])
def live_stream():
    session_id = request.args.get("session_id")
    if not session_id or session_id not in sessions:
        return abort(404, description="Session not found")
    session = sessions[session_id]

    def generate():
        try:
            while True:
                frame = session.get_frame()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(0.03)  # ~33 fps
        except Exception:
            pass

    return Response(stream_with_context(generate()),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/camera/stream/terminate", methods=["POST"])
def terminate_stream():
    session_id = request.args.get("session_id") or request.json.get("session_id")
    if not session_id or session_id not in sessions:
        return abort(404, description="Session not found")
    session = sessions.pop(session_id)
    session.stop()
    return jsonify({"success": True, "message": "Stream session terminated."})

@app.route("/stream/stop", methods=["POST"])
def stop_stream():
    session_id = request.args.get("session_id") or request.json.get("session_id")
    if not session_id or session_id not in sessions:
        return abort(404, description="Session not found")
    session = sessions.pop(session_id)
    session.stop()
    return jsonify({"success": True, "message": "Stream stopped."})

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)