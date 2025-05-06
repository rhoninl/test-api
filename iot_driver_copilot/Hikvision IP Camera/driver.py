import os
import threading
import time
from flask import Flask, Response, request, jsonify
import cv2
import base64

app = Flask(__name__)

# Environment variable configuration
DEVICE_IP = os.environ.get("HIKVISION_DEVICE_IP", "192.168.1.64")
DEVICE_USER = os.environ.get("HIKVISION_USER", "admin")
DEVICE_PASS = os.environ.get("HIKVISION_PASSWORD", "12345")
RTSP_PORT = int(os.environ.get("HIKVISION_RTSP_PORT", "554"))
HTTP_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

RTSP_PATH = os.environ.get("HIKVISION_RTSP_PATH", "/Streaming/Channels/101")
# Ex: /Streaming/Channels/1 or /Streaming/Channels/101, may need to be adjusted for camera

# Internal state
streaming_thread = None
streaming_active = False
video_frame = None
frame_lock = threading.Lock()
rtsp_cap = None

def build_rtsp_url():
    return f"rtsp://{DEVICE_USER}:{DEVICE_PASS}@{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"

def video_capture_worker():
    global streaming_active, video_frame, rtsp_cap
    rtsp_url = build_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    rtsp_cap = cap
    if not cap.isOpened():
        streaming_active = False
        return
    while streaming_active:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        with frame_lock:
            video_frame = frame
    cap.release()
    rtsp_cap = None

def start_streaming():
    global streaming_thread, streaming_active
    if streaming_active:
        return False
    streaming_active = True
    streaming_thread = threading.Thread(target=video_capture_worker, daemon=True)
    streaming_thread.start()
    return True

def stop_streaming():
    global streaming_active
    streaming_active = False
    # Wait for thread to terminate, but don't block the API

@app.route("/start", methods=["POST"])
def api_start():
    started = start_streaming()
    return jsonify({"status": "started" if started else "already running"})

@app.route("/video/start", methods=["POST"])
def api_video_start():
    started = start_streaming()
    return jsonify({"status": "started" if started else "already running"})

@app.route("/stop", methods=["POST"])
def api_stop():
    stop_streaming()
    return jsonify({"status": "stopped"})

@app.route("/video/stop", methods=["POST"])
def api_video_stop():
    stop_streaming()
    return jsonify({"status": "stopped"})

def gen_mjpeg_stream():
    global video_frame
    while streaming_active:
        with frame_lock:
            if video_frame is None:
                frame = None
            else:
                frame = video_frame.copy()
        if frame is None:
            time.sleep(0.1)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        time.sleep(0.04)  # ~25fps

@app.route("/stream", methods=["GET"])
def stream():
    if not streaming_active:
        return jsonify({"error": "stream not started"}), 400
    return Response(gen_mjpeg_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    app.run(host=HTTP_HOST, port=HTTP_PORT, threaded=True)