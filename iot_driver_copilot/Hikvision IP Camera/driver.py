import os
import threading
import cv2
import time
import io
import requests
from flask import Flask, Response, jsonify, send_file

# Environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_USER = os.environ.get('RTSP_USER', 'admin')
RTSP_PASS = os.environ.get('RTSP_PASS', '12345')
CHANNEL = os.environ.get('CAMERA_CHANNEL', '101')
SNAPSHOT_PORT = int(os.environ.get('SNAPSHOT_PORT', 80))
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

# RTSP URL format for Hikvision
RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{DEVICE_IP}:{RTSP_PORT}/Streaming/Channels/{CHANNEL}"

# Snapshot URL (ISAPI)
SNAPSHOT_URL = f"http://{RTSP_USER}:{RTSP_PASS}@{DEVICE_IP}:{SNAPSHOT_PORT}/ISAPI/Streaming/channels/{CHANNEL}/picture"

app = Flask(__name__)

def mjpeg_stream():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + b"" + b"\r\n"
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.25)
                continue
            # Encode as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            frame_bytes = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        cap.release()

@app.route('/camera/live')
def camera_live():
    return Response(mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/camera/rtsp-url')
def camera_rtsp_url():
    return jsonify({
        "rtsp_url": RTSP_URL
    })

@app.route('/camera/snapshot')
def camera_snapshot():
    # Try to fetch snapshot via HTTP API
    try:
        resp = requests.get(SNAPSHOT_URL, stream=True, timeout=5)
        if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image/jpeg'):
            return Response(resp.content, content_type='image/jpeg')
    except Exception:
        pass
    # Fallback: Capture from RTSP
    cap = cv2.VideoCapture(RTSP_URL)
    ret, frame = cap.read()
    cap.release()
    if ret:
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            return Response(jpeg.tobytes(), content_type='image/jpeg')
    return Response("Could not obtain snapshot", status=503)

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)