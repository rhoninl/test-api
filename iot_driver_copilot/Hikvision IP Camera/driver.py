import os
import threading
import cv2
import flask
from flask import Response, abort

# --- Configuration from Environment Variables ---
DEVICE_IP = os.environ.get("DEVICE_IP")
RTSP_PORT = os.environ.get("RTSP_PORT", "554")
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", "")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

if not DEVICE_IP:
    raise ValueError("DEVICE_IP environment variable not set!")

# --- RTSP URL Construction ---
def build_rtsp_url():
    auth = ""
    if RTSP_USERNAME and RTSP_PASSWORD:
        auth = f"{RTSP_USERNAME}:{RTSP_PASSWORD}@"
    return f"rtsp://{auth}{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

RTSP_URL = build_rtsp_url()

# --- Frame Grabber ---
class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.cap = None
        self.frame = None
        self.running = False
        self.lock = threading.Lock()
        self.thread = None

    def start(self):
        if self.running:
            return
        self.cap = cv2.VideoCapture(self.rtsp_url)
        if not self.cap.isOpened():
            raise RuntimeError("Failed to open RTSP stream")
        self.running = True
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                continue
            with self.lock:
                self.frame = frame

    def get_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', self.frame)
            if not ret:
                return None
            return jpeg.tobytes()

    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        self.cap = None
        self.frame = None

# --- Flask HTTP Server ---
app = flask.Flask(__name__)
camera_stream = None

@app.before_first_request
def initialize_camera():
    global camera_stream
    camera_stream = CameraStream(RTSP_URL)
    try:
        camera_stream.start()
    except Exception as e:
        import sys
        print(f"Could not start camera stream: {e}", file=sys.stderr)

@app.route("/stream", methods=["GET"])
def stream():
    if camera_stream is None or not camera_stream.running:
        abort(503, "Camera stream not available")

    def gen():
        while True:
            frame = camera_stream.get_frame()
            if frame is None:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)