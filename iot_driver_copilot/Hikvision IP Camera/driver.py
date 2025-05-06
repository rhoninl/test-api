import os
import threading
import io
import time
import requests
from flask import Flask, Response, request, jsonify, stream_with_context

# Environment Variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "80"))
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Hikvision RTSP stream URL (example for mainstream)
RTSP_PATH = os.environ.get("RTSP_PATH", "/Streaming/Channels/101")
RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"

# Hikvision snapshot URL
SNAPSHOT_PATH = os.environ.get("SNAPSHOT_PATH", "/ISAPI/Streaming/channels/101/picture")
SNAPSHOT_URL = f"http://{DEVICE_IP}:{HTTP_PORT}{SNAPSHOT_PATH}"

app = Flask(__name__)

class StreamState:
    def __init__(self):
        self.lock = threading.Lock()
        self.streaming = False
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        with self.lock:
            if not self.streaming:
                self.stop_event.clear()
                self.streaming = True

    def stop(self):
        with self.lock:
            self.stop_event.set()
            self.streaming = False

    def is_streaming(self):
        with self.lock:
            return self.streaming

stream_state = StreamState()

def find_start_code(data, start=0):
    # Search for 0x00000001 in the stream, which marks NAL unit boundaries
    i = data.find(b'\x00\x00\x00\x01', start)
    return i

def h264_to_mjpeg_generator(rtsp_url, stop_event):
    import cv2
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" \
              b"Camera stream not available.\r\n\r\n"
        return

    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" +
                   jpeg.tobytes() + b"\r\n")
    finally:
        cap.release()

@app.route("/snap", methods=["GET"])
def snap():
    try:
        resp = requests.get(SNAPSHOT_URL, auth=(DEVICE_USERNAME, DEVICE_PASSWORD), timeout=5)
        if resp.status_code == 200 and resp.headers['Content-Type'].startswith('image/jpeg'):
            return Response(resp.content, content_type='image/jpeg')
        else:
            return Response("Failed to retrieve snapshot.", status=502)
    except Exception as e:
        return Response(f"Snapshot error: {str(e)}", status=502)

@app.route("/start", methods=["POST"])
def start_stream():
    if stream_state.is_streaming():
        return jsonify({"status": "already streaming"}), 200
    stream_state.start()
    return jsonify({"status": "stream started"}), 200

@app.route("/stop", methods=["POST"])
def stop_stream():
    stream_state.stop()
    return jsonify({"status": "stream stopped"}), 200

@app.route("/stream", methods=["GET"])
def stream():
    if not stream_state.is_streaming():
        return Response("Stream not started. Use /start to begin streaming.", status=503)
    return Response(
        stream_with_context(h264_to_mjpeg_generator(RTSP_URL, stream_state.stop_event)),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)