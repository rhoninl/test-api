import os
import threading
import cv2
from flask import Flask, Response, abort

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP")
RTSP_PORT = int(os.environ.get("RTSP_PORT", 554))
RTSP_USERNAME = os.environ.get("RTSP_USERNAME")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))

if not (DEVICE_IP and RTSP_USERNAME and RTSP_PASSWORD):
    raise RuntimeError("Missing required environment variables: DEVICE_IP, RTSP_USERNAME, RTSP_PASSWORD")

app = Flask(__name__)

RTSP_URL = f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

def gen_mjpeg():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("Unable to open RTSP stream.")

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
    try:
        return Response(gen_mjpeg(), mimetype='multipart/x-mixed-replace; boundary=frame')
    except Exception:
        abort(503, description="Camera stream unavailable.")

def run_server():
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True, use_reloader=False)

if __name__ == '__main__':
    run_server()