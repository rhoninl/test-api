import os
import threading
import queue
import time
import cv2
from flask import Flask, Response, abort

# Load environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
RTSP_PORT = os.environ.get('RTSP_PORT', '554')
RTSP_USERNAME = os.environ.get('RTSP_USERNAME', '')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '')
RTSP_PATH = os.environ.get('RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

if not DEVICE_IP:
    raise ValueError("DEVICE_IP environment variable is required.")

# Construct RTSP URL
if RTSP_USERNAME and RTSP_PASSWORD:
    RTSP_URL = f'rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}'
else:
    RTSP_URL = f'rtsp://{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}'

app = Flask(__name__)

class FrameBuffer:
    def __init__(self, maxsize=100):
        self.q = queue.Queue(maxsize)
        self.last_frame = None

    def put(self, frame):
        try:
            self.q.put_nowait(frame)
            self.last_frame = frame
        except queue.Full:
            # Drop the oldest frame and put the new one
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put_nowait(frame)
            self.last_frame = frame

    def get(self):
        try:
            return self.q.get(timeout=1)
        except queue.Empty:
            return self.last_frame

frame_buffer = FrameBuffer()

def rtsp_reader(rtsp_url, frame_buffer):
    cap = None
    while True:
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            time.sleep(1)
            continue
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            frame_buffer.put(jpeg.tobytes())
        else:
            time.sleep(0.01)
    if cap is not None:
        cap.release()

def generate_frames(frame_buffer):
    while True:
        frame = frame_buffer.get()
        if frame is None:
            time.sleep(0.01)
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/stream', methods=['GET'])
def stream():
    return Response(generate_frames(frame_buffer),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

def start_rtsp_thread():
    t = threading.Thread(target=rtsp_reader, args=(RTSP_URL, frame_buffer), daemon=True)
    t.start()

if __name__ == "__main__":
    start_rtsp_thread()
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)