import os
import threading
import io
import time
import cv2
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# Environment variable configuration
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# RTSP URL template for Hikvision cameras
RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/Streaming/Channels/101"

# Streaming state
streaming_active = threading.Event()
frame_lock = threading.Lock()
latest_frame = [None]

def grab_frames():
    global latest_frame
    cap = cv2.VideoCapture(RTSP_URL)
    while streaming_active.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        with frame_lock:
            latest_frame[0] = frame
        time.sleep(0.03)  # ~30 fps
    cap.release()

@app.route('/start', methods=['POST'])
def start_stream():
    if not streaming_active.is_set():
        streaming_active.set()
        t = threading.Thread(target=grab_frames, daemon=True)
        t.start()
    stream_url = f"http://{SERVER_HOST}:{SERVER_PORT}/video"
    return jsonify({"status": "streaming", "stream_url": stream_url})

@app.route('/stop', methods=['POST'])
def stop_stream():
    streaming_active.clear()
    return jsonify({"status": "stopped"})

def mjpeg_stream():
    while streaming_active.is_set():
        with frame_lock:
            frame = latest_frame[0]
        if frame is not None:
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            else:
                time.sleep(0.1)
        else:
            time.sleep(0.1)
    # Stream ended
    yield b''

@app.route('/video')
def video_feed():
    if not streaming_active.is_set():
        return Response('Stream Not Started', status=503)
    return Response(mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)