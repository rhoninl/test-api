import os
import threading
import queue
import time
import logging
from flask import Flask, Response, request, jsonify

import cv2

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hikvision-driver")

# Configuration from environment variables
CAMERA_IP = os.environ.get('DEVICE_IP')
CAMERA_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
CAMERA_RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
CAMERA_STREAM_PATH = os.environ.get('RTSP_STREAM_PATH', '/Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# Build RTSP URL
RTSP_URL = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{CAMERA_STREAM_PATH}"

app = Flask(__name__)

# Streaming session state
stream_state = {
    "active": False,
    "thread": None,
    "frame_queue": None,
    "stop_event": None
}

FRAME_QUEUE_SIZE = 60

def camera_stream_worker(rtsp_url, frame_queue, stop_event):
    logger.info(f"Connecting to camera: {rtsp_url}")
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        logger.error("Failed to open RTSP stream.")
        return

    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            logger.warning("Failed to read frame from camera.")
            time.sleep(0.1)
            continue
        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            logger.warning("Failed to encode frame.")
            continue
        try:
            frame_queue.put(jpeg.tobytes(), timeout=0.5)
        except queue.Full:
            logger.debug("Frame queue full, dropping frame.")

    cap.release()
    logger.info("Camera stream worker stopped.")

@app.route('/stream', methods=['POST'])
def activate_stream():
    if stream_state["active"]:
        return jsonify({"status": "already_active"}), 200

    stream_state["frame_queue"] = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
    stream_state["stop_event"] = threading.Event()
    worker_thread = threading.Thread(
        target=camera_stream_worker,
        args=(RTSP_URL, stream_state["frame_queue"], stream_state["stop_event"]),
        daemon=True
    )
    worker_thread.start()
    stream_state["thread"] = worker_thread
    stream_state["active"] = True
    logger.info("Video stream activated.")
    return jsonify({"status": "activated"}), 200

@app.route('/stream', methods=['GET'])
def get_stream():
    if not stream_state["active"]:
        return jsonify({"error": "Stream not active. Please POST /stream to activate."}), 400

    def generate():
        while stream_state["active"]:
            try:
                frame = stream_state["frame_queue"].get(timeout=1.0)
            except queue.Empty:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stream', methods=['DELETE'])
def stop_stream():
    if not stream_state["active"]:
        return jsonify({"status": "not_active"}), 200

    stream_state["stop_event"].set()
    if stream_state["thread"]:
        stream_state["thread"].join(timeout=2)
    stream_state["thread"] = None
    stream_state["frame_queue"] = None
    stream_state["stop_event"] = None
    stream_state["active"] = False
    logger.info("Video stream deactivated.")
    return jsonify({"status": "deactivated"}), 200

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)