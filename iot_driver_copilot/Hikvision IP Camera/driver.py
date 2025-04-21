import os
import uuid
import threading
import queue
from flask import Flask, Response, request, jsonify, abort

import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASS = os.environ.get("DEVICE_RTSP_PASS", "12345")
CAMERA_CHANNEL = os.environ.get("CAMERA_CHANNEL", "101")
CAMERA_STREAM_TYPE = os.environ.get("CAMERA_STREAM_TYPE", "h264")  # h264 or mjpeg
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL_TEMPLATE = "rtsp://{user}:{pwd}@{ip}:{port}/Streaming/Channels/{channel}"

app = Flask(__name__)

# Session store: session_id -> {'cap': cv2.VideoCapture, 'queue': queue.Queue, 'thread': Thread, 'active': bool}
active_streams = {}
active_streams_lock = threading.Lock()

def generate_mjpeg_frames(session_id):
    with active_streams_lock:
        stream_info = active_streams.get(session_id)
    if not stream_info or not stream_info['active']:
        return
    cap = stream_info["cap"]
    while stream_info['active']:
        success, frame = cap.read()
        if not success:
            break
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
    cap.release()

def start_video_capture(rtsp_url):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        return None
    return cap

def stop_stream(session_id):
    with active_streams_lock:
        stream_info = active_streams.pop(session_id, None)
    if stream_info:
        stream_info['active'] = False
        if stream_info['cap'] and stream_info['cap'].isOpened():
            stream_info['cap'].release()
        if stream_info.get('thread') and stream_info['thread'].is_alive():
            stream_info['thread'].join(timeout=1)

@app.route("/camera/stream/init", methods=["POST"])
def camera_stream_init():
    session_id = str(uuid.uuid4())
    if CAMERA_STREAM_TYPE.lower() == 'h264':
        # For browser compatibility, we proxy as MJPEG since native H264 is not supported in most browsers
        stream_format = 'mjpeg'
    else:
        stream_format = CAMERA_STREAM_TYPE.lower()
    rtsp_url = RTSP_URL_TEMPLATE.format(
        user=DEVICE_RTSP_USER,
        pwd=DEVICE_RTSP_PASS,
        ip=DEVICE_IP,
        port=DEVICE_RTSP_PORT,
        channel=CAMERA_CHANNEL
    )
    cap = start_video_capture(rtsp_url)
    if not cap:
        return jsonify({"error": "Unable to connect to camera RTSP stream"}), 500
    with active_streams_lock:
        active_streams[session_id] = {'cap': cap, 'active': True, 'thread': None}
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "http_url": http_url
    })

@app.route("/camera/stream/live", methods=["GET"])
def camera_stream_live():
    session_id = request.args.get('session_id')
    if not session_id:
        return abort(400, description="Missing session_id")
    with active_streams_lock:
        stream_info = active_streams.get(session_id)
        if not stream_info or not stream_info['active']:
            return abort(404, description="Session not found or not active")
    return Response(
        generate_mjpeg_frames(session_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route("/camera/stream/terminate", methods=["POST"])
def camera_stream_terminate():
    session_id = request.args.get('session_id') or request.json.get('session_id') if request.is_json else None
    if not session_id:
        return abort(400, description="Missing session_id")
    stop_stream(session_id)
    return jsonify({"status": "terminated", "session_id": session_id})

@app.route("/stream/stop", methods=["POST"])
def stream_stop():
    session_id = request.args.get('session_id') or request.json.get('session_id') if request.is_json else None
    if not session_id:
        return abort(400, description="Missing session_id")
    stop_stream(session_id)
    return jsonify({"status": "stopped", "session_id": session_id})

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)