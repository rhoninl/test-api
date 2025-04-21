import os
import threading
import time
import cv2
import requests
from flask import Flask, Response, jsonify, send_file
from io import BytesIO

# Config via environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_HTTP_PORT = int(os.environ.get('DEVICE_HTTP_PORT', '80'))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_PATH = os.environ.get('DEVICE_RTSP_PATH', 'Streaming/Channels/101')
RTSP_TRANSPORT = os.environ.get('DEVICE_RTSP_TRANSPORT', 'tcp')  # or 'udp'

SNAPSHOT_PATH = os.environ.get('DEVICE_SNAPSHOT_PATH', '/ISAPI/Streaming/channels/101/picture')
ISAPI_SNAPSHOT = os.environ.get('DEVICE_USE_ISAPI_SNAPSHOT', 'true').lower() == 'true'

# Flask setup
app = Flask(__name__)

def build_rtsp_url():
    return f'rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_PATH}'

@app.route('/camera/rtsp-url', methods=['GET'])
def get_rtsp_url():
    return jsonify({
        "rtsp_url": build_rtsp_url()
    })

def gen_mjpeg_stream():
    rtsp_url = build_rtsp_url()

    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        yield b''
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue
            ret2, jpeg = cv2.imencode('.jpg', frame)
            if not ret2:
                continue
            jpg_bytes = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpg_bytes + b'\r\n')
    finally:
        cap.release()

@app.route('/camera/live', methods=['GET'])
def camera_live():
    return Response(
        gen_mjpeg_stream(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

def get_snapshot_jpeg():
    if ISAPI_SNAPSHOT:
        url = f'http://{DEVICE_IP}:{DEVICE_HTTP_PORT}{SNAPSHOT_PATH}'
        try:
            resp = requests.get(url, auth=(DEVICE_USERNAME, DEVICE_PASSWORD), timeout=5)
            if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image/jpeg'):
                return resp.content
        except Exception:
            pass
    # fallback: use RTSP/mjpeg and grab a single frame
    rtsp_url = build_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    ret, frame = cap.read()
    result = None
    if ret and frame is not None:
        ret2, jpeg = cv2.imencode('.jpg', frame)
        if ret2:
            result = jpeg.tobytes()
    cap.release()
    return result

@app.route('/camera/snapshot', methods=['GET'])
def camera_snapshot():
    jpeg = get_snapshot_jpeg()
    if jpeg is None:
        return 'Failed to capture snapshot', 503
    return Response(jpeg, mimetype='image/jpeg')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)