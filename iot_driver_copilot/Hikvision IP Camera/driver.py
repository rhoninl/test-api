import os
import threading
import cv2
import queue
from flask import Flask, Response, abort

# Load configuration from environment variables
CAMERA_RTSP_IP = os.environ.get('CAMERA_RTSP_IP', '192.168.1.64')
CAMERA_RTSP_PORT = os.environ.get('CAMERA_RTSP_PORT', '554')
CAMERA_RTSP_USER = os.environ.get('CAMERA_RTSP_USER', 'admin')
CAMERA_RTSP_PASSWORD = os.environ.get('CAMERA_RTSP_PASSWORD', '12345')
CAMERA_RTSP_PATH = os.environ.get('CAMERA_RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# Build RTSP URL
RTSP_URL = f'rtsp://{CAMERA_RTSP_USER}:{CAMERA_RTSP_PASSWORD}@{CAMERA_RTSP_IP}:{CAMERA_RTSP_PORT}/{CAMERA_RTSP_PATH}'

app = Flask(__name__)

class RTSPStream:
    def __init__(self, src, max_queue=128):
        self.src = src
        self.frame_queue = queue.Queue(maxsize=max_queue)
        self.running = False
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self.update, daemon=True)
                self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
        if self.thread:
            self.thread.join()
            self.thread = None

    def update(self):
        cap = cv2.VideoCapture(self.src)
        if not cap.isOpened():
            self.running = False
            return
        while self.running:
            ret, frame = cap.read()
            if not ret:
                break
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                if self.frame_queue.full():
                    self.frame_queue.get_nowait()
                self.frame_queue.put_nowait(jpeg.tobytes())
            except queue.Full:
                pass
        cap.release()

    def get_frame(self):
        try:
            return self.frame_queue.get(timeout=1)
        except queue.Empty:
            return None

rtsp_stream = RTSPStream(RTSP_URL)

@app.route('/stream', methods=['GET'])
def stream_video():
    def gen():
        rtsp_stream.start()
        while True:
            frame = rtsp_stream.get_frame()
            if frame is None:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    rtsp_stream.start()
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)