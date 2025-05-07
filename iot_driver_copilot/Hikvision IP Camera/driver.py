import os
import io
import threading
import time
import queue
from flask import Flask, Response, request, jsonify

import cv2
import numpy as np

# Environment variable configuration
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.10")
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))
STREAM_PATH = os.environ.get("RTSP_STREAM_PATH", "Streaming/Channels/101")
DEFAULT_FORMAT = os.environ.get("DEFAULT_STREAM_FORMAT", "mjpeg").lower()

# RTSP Stream URL builder
def build_rtsp_url(format_hint=None):
    fmt = (format_hint or DEFAULT_FORMAT).lower()
    # Hikvision standard path
    return f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{STREAM_PATH}"

app = Flask(__name__)

# Streaming session state
stream_state = {
    "is_streaming": False,
    "format": DEFAULT_FORMAT,
    "thread": None,
    "frame_queue": queue.Queue(maxsize=10),
    "rtsp_url": "",
    "stop_event": threading.Event(),
}

def mjpeg_streamer():
    rtsp_url = stream_state["rtsp_url"]
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        stream_state["is_streaming"] = False
        return
    try:
        while not stream_state["stop_event"].is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            # JPEG encode
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            # Remove old frames if queue is full
            try:
                stream_state["frame_queue"].put(jpeg.tobytes(), timeout=0.1)
            except queue.Full:
                try:
                    stream_state["frame_queue"].get_nowait()
                except queue.Empty:
                    pass
                stream_state["frame_queue"].put_nowait(jpeg.tobytes())
    finally:
        cap.release()
        stream_state["is_streaming"] = False

def gen_mjpeg_frames():
    while stream_state["is_streaming"]:
        try:
            frame = stream_state["frame_queue"].get(timeout=1)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    yield b''

@app.route("/stream/start", methods=["POST"])
def start_stream():
    data = request.get_json(silent=True) or {}
    fmt = data.get("format", DEFAULT_FORMAT).lower()
    if fmt not in ("mjpeg", "h264"):
        return jsonify({"error": "Unsupported format"}), 400

    if stream_state["is_streaming"]:
        return jsonify({"result": "already streaming", "format": stream_state["format"]})

    rtsp_url = build_rtsp_url(fmt)
    stream_state["rtsp_url"] = rtsp_url
    stream_state["format"] = fmt
    stream_state["stop_event"].clear()
    stream_state["is_streaming"] = True

    if fmt == "mjpeg":
        stream_state["thread"] = threading.Thread(target=mjpeg_streamer, daemon=True)
        stream_state["thread"].start()
    else:
        # For H264, we just proxy the RTSP bytes (raw, not browser-playable)
        stream_state["thread"] = None

    return jsonify({"result": "stream started", "format": fmt, "rtsp_url": rtsp_url})

@app.route("/stream/stop", methods=["POST"])
def stop_stream():
    if not stream_state["is_streaming"]:
        return jsonify({"result": "not streaming"})
    stream_state["stop_event"].set()
    if stream_state["thread"]:
        stream_state["thread"].join(timeout=2)
    stream_state["is_streaming"] = False
    # Empty frame queue
    try:
        while True:
            stream_state["frame_queue"].get_nowait()
    except queue.Empty:
        pass
    return jsonify({"result": "stream stopped"})

@app.route("/stream/url", methods=["GET"])
def stream_url():
    fmt = request.args.get("format", stream_state["format"]).lower()
    if fmt not in ("mjpeg", "h264"):
        return jsonify({"error": "Unsupported format"}), 400
    if fmt == "mjpeg":
        # HTTP endpoint for MJPEG
        url = f"http://{HTTP_SERVER_HOST}:{HTTP_SERVER_PORT}/video"
    else:
        # HTTP endpoint for H264 (raw proxy)
        url = f"http://{HTTP_SERVER_HOST}:{HTTP_SERVER_PORT}/video_h264"
    return jsonify({"format": fmt, "url": url})

@app.route("/video")
def video_feed():
    if not stream_state["is_streaming"] or stream_state["format"] != "mjpeg":
        return jsonify({"error": "Stream not started or wrong format"}), 400
    return Response(
        gen_mjpeg_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route("/video_h264")
def video_feed_h264():
    if not stream_state["is_streaming"] or stream_state["format"] != "h264":
        return jsonify({"error": "Stream not started or wrong format"}), 400

    def gen_h264():
        rtsp_url = stream_state["rtsp_url"]
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            yield b''
            return
        try:
            while not stream_state["stop_event"].is_set():
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue
                # Encode raw H264
                ret, buf = cv2.imencode('.mp4', frame)
                if not ret:
                    continue
                yield buf.tobytes()
        finally:
            cap.release()
    return Response(gen_h264(), mimetype="video/mp4")

if __name__ == "__main__":
    app.run(host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT, threaded=True)