import os
import threading
import time
from flask import Flask, Response, request, jsonify, abort
import cv2

# Configuration from environment variables
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "/Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{CAMERA_STREAM_PATH}"

app = Flask(__name__)

# Shared video capture object and state
stream_lock = threading.Lock()
video_capture = None
streaming_active = False
capture_thread = None

def open_video_stream():
    global video_capture
    with stream_lock:
        if video_capture is None or not video_capture.isOpened():
            video_capture = cv2.VideoCapture(RTSP_URL)

def close_video_stream():
    global video_capture
    with stream_lock:
        if video_capture is not None:
            video_capture.release()
            video_capture = None

def frame_generator():
    global video_capture, streaming_active
    while streaming_active:
        with stream_lock:
            if video_capture is None or not video_capture.isOpened():
                break
            ret, frame = video_capture.read()
        if not ret or frame is None:
            time.sleep(0.1)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = jpeg.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.04)  # ~25 FPS

@app.route('/stream', methods=['POST'])
def start_stream():
    global streaming_active, capture_thread
    if streaming_active:
        return jsonify({"status": "already started"}), 200
    open_video_stream()
    if video_capture is None or not video_capture.isOpened():
        close_video_stream()
        return jsonify({"error": "Failed to open camera stream"}), 500
    streaming_active = True
    return jsonify({"status": "stream started"}), 200

@app.route('/stream', methods=['GET'])
def get_stream():
    global streaming_active
    if not streaming_active:
        abort(404, description="Stream not active. POST to /stream to start.")
    return Response(frame_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stream', methods=['DELETE'])
def stop_stream():
    global streaming_active
    if not streaming_active:
        return jsonify({"status": "already stopped"}), 200
    streaming_active = False
    time.sleep(0.2)  # Allow frame_generator to exit
    close_video_stream()
    return jsonify({"status": "stream stopped"}), 200

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)