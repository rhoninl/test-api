import os
import io
import threading
import cv2
from flask import Flask, Response, stream_with_context

# Read configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
RTSP_PORT = os.environ.get("RTSP_PORT", "554")
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", "admin")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

class CameraStream:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.cap = None
        self.lock = threading.Lock()
        self.running = False
        self.frame = None
        self.thread = None

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.cap = cv2.VideoCapture(self.rtsp_url)
                self.thread = threading.Thread(target=self.update, daemon=True)
                self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            if self.cap:
                self.cap.release()
                self.cap = None

    def update(self):
        while self.running:
            if self.cap:
                ret, frame = self.cap.read()
                if ret:
                    self.frame = frame

    def get_frame(self):
        if self.frame is not None:
            ret, jpeg = cv2.imencode('.jpg', self.frame)
            if ret:
                return jpeg.tobytes()
        return None

camera = CameraStream(RTSP_URL)

@app.route('/stream', methods=['GET'])
def stream():
    camera.start()
    def generate():
        while True:
            frame = camera.get_frame()
            if frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                # If no frame, try to wait a bit
                cv2.waitKey(10)
    return Response(stream_with_context(generate()), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)