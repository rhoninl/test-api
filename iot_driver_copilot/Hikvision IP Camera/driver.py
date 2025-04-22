import os
import io
import threading
import queue
import cv2
from flask import Flask, Response, stream_with_context

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD')
RTSP_PATH = os.environ.get('DEVICE_RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

if not DEVICE_IP or not DEVICE_USERNAME or not DEVICE_PASSWORD:
    raise EnvironmentError("DEVICE_IP, DEVICE_USERNAME, and DEVICE_PASSWORD must be set in environment variables.")

RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

def mjpeg_frame_generator():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        raise RuntimeError("Unable to connect to the RTSP stream")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            frame_bytes = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        cap.release()

@app.route('/stream', methods=['GET'])
def stream():
    return Response(
        stream_with_context(mjpeg_frame_generator()),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)