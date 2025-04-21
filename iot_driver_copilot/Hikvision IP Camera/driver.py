import os
import threading
import queue
import time
import cv2
from flask import Flask, Response, stream_with_context

# Environment variables for configuration
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_RTSP_USER = os.environ.get('DEVICE_RTSP_USER', 'admin')
DEVICE_RTSP_PASS = os.environ.get('DEVICE_RTSP_PASS', '12345')
DEVICE_RTSP_PATH = os.environ.get('DEVICE_RTSP_PATH', '/Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_URL = f"rtsp://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{DEVICE_RTSP_PATH}"

# MJPEG streaming settings
FRAME_QUEUE_SIZE = int(os.environ.get('FRAME_QUEUE_SIZE', '8'))
FPS = int(os.environ.get('FPS', '10'))

app = Flask(__name__)

frame_queue = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
stop_event = threading.Event()

def rtsp_to_mjpeg_worker():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        return
    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                if frame_queue.full():
                    frame_queue.get_nowait()
                frame_queue.put_nowait(jpeg.tobytes())
            except queue.Full:
                pass
            time.sleep(1.0 / FPS)
    finally:
        cap.release()

def mjpeg_stream_generator():
    while not stop_event.is_set():
        try:
            frame = frame_queue.get(timeout=2)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/stream', methods=['GET'])
def stream():
    return Response(
        stream_with_context(mjpeg_stream_generator()),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

def start_rtsp_thread():
    t = threading.Thread(target=rtsp_to_mjpeg_worker, daemon=True)
    t.start()
    return t

if __name__ == '__main__':
    rtsp_thread = start_rtsp_thread()
    try:
        app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)
    finally:
        stop_event.set()
        rtsp_thread.join(timeout=5)