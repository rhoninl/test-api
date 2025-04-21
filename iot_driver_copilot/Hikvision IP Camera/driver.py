import os
import io
import uuid
import threading
import queue
import time
import cv2
from flask import Flask, request, Response, jsonify

# Configuration from environment variables
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.64')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_RTSP_USER = os.environ.get('CAMERA_RTSP_USER', 'admin')
CAMERA_RTSP_PASSWORD = os.environ.get('CAMERA_RTSP_PASSWORD', '12345')
CAMERA_RTSP_PATH = os.environ.get('CAMERA_RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('HTTP_SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('HTTP_SERVER_PORT', '8080'))
MJPEG_FPS = int(os.environ.get('MJPEG_FPS', '10'))

RTSP_URL_TEMPLATE = "rtsp://{user}:{pwd}@{ip}:{port}/{path}"

app = Flask(__name__)

# Session management
sessions = {}
sessions_lock = threading.Lock()

class StreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=10)
        self.active = threading.Event()
        self.active.set()
        self.capture_thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.capture_thread.start()

    def _capture_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.active.clear()
            return
        last_frame_time = 0
        while self.active.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            now = time.time()
            if now - last_frame_time < 1.0 / MJPEG_FPS:
                continue
            last_frame_time = now
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                self.frame_queue.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                pass  # Drop frame if queue is full
        cap.release()

    def get_frame(self, timeout=5):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self.active.clear()
        self.capture_thread.join(timeout=3)
        with self.frame_queue.mutex:
            self.frame_queue.queue.clear()

def get_rtsp_url():
    return RTSP_URL_TEMPLATE.format(
        user=CAMERA_RTSP_USER,
        pwd=CAMERA_RTSP_PASSWORD,
        ip=CAMERA_IP,
        port=CAMERA_RTSP_PORT,
        path=CAMERA_RTSP_PATH
    )

@app.route('/camera/stream/init', methods=['POST'])
def camera_stream_init():
    session_id = str(uuid.uuid4())
    rtsp_url = get_rtsp_url()
    sess = StreamSession(session_id, rtsp_url)
    with sessions_lock:
        sessions[session_id] = sess
    http_url = f"http://{SERVER_HOST}:{SERVER_PORT}/camera/stream/live?session_id={session_id}"
    return jsonify({
        "session_id": session_id,
        "http_stream_url": http_url
    }), 200

@app.route('/camera/stream/live', methods=['GET'])
def camera_stream_live():
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        sess = sessions.get(session_id)
    if not sess or not sess.active.is_set():
        return jsonify({"error": "Invalid or inactive session"}), 404

    def generate():
        boundary = "--frame"
        while sess.active.is_set():
            frame = sess.get_frame(timeout=5)
            if frame is None:
                continue
            yield (f"{boundary}\r\n"
                   "Content-Type: image/jpeg\r\n"
                   f"Content-Length: {len(frame)}\r\n\r\n").encode('utf-8') + frame + b"\r\n"
        yield f"{boundary}--\r\n".encode('utf-8')

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/camera/stream/terminate', methods=['POST'])
def camera_stream_terminate():
    session_id = request.args.get('session_id') or request.json.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        sess = sessions.pop(session_id, None)
    if sess:
        sess.stop()
        return jsonify({"message": "Session terminated"}), 200
    else:
        return jsonify({"error": "Invalid session_id"}), 404

@app.route('/stream/stop', methods=['POST'])
def stream_stop():
    session_id = request.args.get('session_id') or request.json.get('session_id')
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400
    with sessions_lock:
        sess = sessions.pop(session_id, None)
    if sess:
        sess.stop()
        return jsonify({"message": "Stream stopped"}), 200
    else:
        return jsonify({"error": "Invalid session_id"}), 404

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)