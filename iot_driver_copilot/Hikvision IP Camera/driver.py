import os
import io
import threading
import time
import queue
import requests
from flask import Flask, Response, request, stream_with_context, send_file, jsonify, abort

# --- Configuration from Environment Variables ---
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_HTTP_PORT = int(os.environ.get("CAMERA_HTTP_PORT", "80"))
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
SNAPSHOT_PATH = os.environ.get("SNAPSHOT_PATH", "/ISAPI/Streaming/channels/101/picture")
RTSP_PATH = os.environ.get("RTSP_PATH", "/Streaming/Channels/101")
HTTP_STREAM_TIMEOUT = int(os.environ.get("HTTP_STREAM_TIMEOUT", "10"))

# --- Flask App ---
app = Flask(__name__)

# --- Streaming Session Management ---
stream_active = threading.Event()
stream_queue = queue.Queue(maxsize=100)
stream_thread = None

def get_rtsp_url():
    return f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{RTSP_PATH}"

def get_snapshot_url():
    return f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}{SNAPSHOT_PATH}"

def fetch_snapshot():
    url = get_snapshot_url()
    try:
        r = requests.get(url, auth=(CAMERA_USER, CAMERA_PASSWORD), timeout=HTTP_STREAM_TIMEOUT, stream=True)
        if r.status_code == 200 and r.headers['Content-Type'].startswith("image"):
            return r.content
        else:
            abort(502, description="Failed to fetch snapshot from camera.")
    except Exception as e:
        abort(502, description=f"Error fetching snapshot: {str(e)}")

def rtsp_stream_worker(rtsp_url, stream_queue, stop_event):
    import cv2
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        stop_event.clear()
        return
    while stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            break
        # Encode frame as JPEG for browser streaming (multipart/x-mixed-replace)
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        try:
            stream_queue.put(jpeg.tobytes(), timeout=2)
        except queue.Full:
            continue
    cap.release()

def start_stream():
    global stream_thread
    if stream_active.is_set():
        return
    stream_active.set()
    stream_thread = threading.Thread(target=rtsp_stream_worker, args=(get_rtsp_url(), stream_queue, stream_active))
    stream_thread.daemon = True
    stream_thread.start()

def stop_stream():
    stream_active.clear()
    while not stream_queue.empty():
        try:
            stream_queue.get_nowait()
        except queue.Empty:
            break

# --- API Endpoints ---

@app.route("/play", methods=["POST"])
def api_play():
    """
    Starts the live video stream from Hikvision IP Camera.
    """
    if not stream_active.is_set():
        start_stream()
    return jsonify({"message": "Streaming started", "video_endpoint": "/video"}), 200

@app.route("/halt", methods=["POST"])
def api_halt():
    """
    Stops the currently active video stream.
    """
    stop_stream()
    return jsonify({"message": "Streaming stopped"}), 200

@app.route("/snap", methods=["GET", "POST"])
def api_snap():
    """
    Captures a snapshot from the live video feed.
    """
    img_bytes = fetch_snapshot()
    return Response(img_bytes, mimetype="image/jpeg", headers={"Content-Disposition": "inline; filename=snapshot.jpg"})

@app.route("/video", methods=["GET"])
def api_video():
    """
    Starts and retrieves the live video stream (MJPEG) from the Hikvision IP Camera.
    """
    if not stream_active.is_set():
        start_stream()

    def generate():
        while stream_active.is_set():
            try:
                frame = stream_queue.get(timeout=5)
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            except queue.Empty:
                break

    return Response(stream_with_context(generate()), mimetype='multipart/x-mixed-replace; boundary=frame')

# --- Main ---
if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)