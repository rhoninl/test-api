import os
import threading
import io
import time
from flask import Flask, Response, request, jsonify
import cv2

# Environment Variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USER = os.environ.get("RTSP_USER", "admin")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

# Construct RTSP URL
RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

# Camera Stream Control State
streaming_state = {
    "active": False,
    "lock": threading.Lock(),
    "capture": None,
    "clients": 0
}

def start_stream():
    with streaming_state["lock"]:
        if not streaming_state["active"]:
            cap = cv2.VideoCapture(RTSP_URL)
            if not cap.isOpened():
                return False
            streaming_state["capture"] = cap
            streaming_state["active"] = True
    return True

def stop_stream():
    with streaming_state["lock"]:
        if streaming_state["active"]:
            if streaming_state["capture"]:
                streaming_state["capture"].release()
            streaming_state["capture"] = None
            streaming_state["active"] = False

@app.route('/video/start', methods=['POST'])
def api_video_start():
    success = start_stream()
    return jsonify({"status": "started" if success else "failed"}), 200 if success else 500

@app.route('/video/stop', methods=['POST'])
def api_video_stop():
    stop_stream()
    return jsonify({"status": "stopped"}), 200

def generate_mjpeg():
    try:
        with streaming_state["lock"]:
            streaming_state["clients"] += 1
        while True:
            with streaming_state["lock"]:
                if not streaming_state["active"] or streaming_state["capture"] is None:
                    break
                ret, frame = streaming_state["capture"].read()
            if not ret:
                time.sleep(0.1)
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            frame_bytes = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        with streaming_state["lock"]:
            streaming_state["clients"] -= 1
            if streaming_state["clients"] == 0 and streaming_state["active"]:
                stop_stream()

@app.route('/video/stream')
def video_stream():
    with streaming_state["lock"]:
        if not streaming_state["active"]:
            return "Stream is not active. Start stream via POST /video/start.", 503
    return Response(generate_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return '''
        <html>
            <head><title>Hikvision Camera Stream</title></head>
            <body>
                <h1>Hikvision Camera Live Stream</h1>
                <img src="/video/stream" width="640" height="480" />
                <form action="/video/start" method="post"><button type="submit">Start Stream</button></form>
                <form action="/video/stop" method="post"><button type="submit">Stop Stream</button></form>
            </body>
        </html>
    '''

if __name__ == '__main__':
    app.run(host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT, threaded=True)