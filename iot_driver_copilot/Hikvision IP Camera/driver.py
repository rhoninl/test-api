import os
import threading
import cv2
from flask import Flask, Response

# Load configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_USER = os.environ.get('RTSP_USER', '')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '')
RTSP_PATH = os.environ.get('RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

# RTSP URL construction
if RTSP_USER and RTSP_PASSWORD:
    RTSP_URL = f'rtsp://{RTSP_USER}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}'
else:
    RTSP_URL = f'rtsp://{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}'

app = Flask(__name__)

def generate_mjpeg():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n'
               b'\xff\xd8\xff\xd9\r\n')
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' +
                   jpeg.tobytes() +
                   b'\r\n')
    finally:
        cap.release()

@app.route('/stream', methods=['GET'])
def stream_video():
    return Response(generate_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

def run_server():
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)

if __name__ == "__main__":
    run_server()