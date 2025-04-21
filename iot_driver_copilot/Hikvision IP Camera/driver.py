import os
import io
import threading
import queue
import time
from flask import Flask, Response, stream_with_context

import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "127.0.0.1")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", "")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Build RTSP URL
if RTSP_USERNAME and RTSP_PASSWORD:
    RTSP_URL = f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"
else:
    RTSP_URL = f"rtsp://{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

# Global queue for communication between capture thread and HTTP stream
frame_queues = {}

def rtsp_frame_reader(rtsp_url, client_id, frame_queue):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        frame_queue.put(None)
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                # End of stream or error
                frame_queue.put(None)
                break
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            # Put JPEG bytes in queue
            try:
                frame_queue.put(jpeg.tobytes(), timeout=2)
            except queue.Full:
                continue
    finally:
        cap.release()

def generate_mjpeg_stream(client_id):
    frame_queue = frame_queues[client_id]
    try:
        while True:
            frame = frame_queue.get()
            if frame is None:
                break
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    finally:
        del frame_queues[client_id]

@app.route('/stream', methods=['GET'])
def stream():
    client_id = str(time.time()) + str(threading.get_ident())
    frame_queue = queue.Queue(maxsize=10)
    frame_queues[client_id] = frame_queue

    t = threading.Thread(target=rtsp_frame_reader, args=(RTSP_URL, client_id, frame_queue), daemon=True)
    t.start()

    return Response(
        stream_with_context(generate_mjpeg_stream(client_id)),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)