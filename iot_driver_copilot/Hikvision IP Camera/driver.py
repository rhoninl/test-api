import os
import uuid
import threading
import queue
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import json
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np

# Environment variables
CAMERA_IP = os.environ.get('CAMERA_IP')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', 554))
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '101')  # e.g. "101" for main stream
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))
HTTP_STREAM_TIMEOUT = int(os.environ.get('HTTP_STREAM_TIMEOUT', 60))  # seconds

# Session management
sessions = {}
sessions_lock = threading.Lock()

def build_rtsp_url():
    return f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/Streaming/Channels/{CAMERA_CHANNEL}"

class CameraStreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=100)
        self.running = threading.Event()
        self.running.set()
        self.last_access = time.time()
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def _capture_loop(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.running.clear()
            return
        try:
            while self.running.is_set():
                ret, frame = cap.read()
                if not ret:
                    break
                # Encode as JPEG
                ret2, jpeg = cv2.imencode('.jpg', frame)
                if not ret2:
                    continue
                # Put encoded frame into the queue
                try:
                    self.frame_queue.put(jpeg.tobytes(), timeout=1)
                except queue.Full:
                    continue
        finally:
            cap.release()

    def get_frame(self, timeout=5):
        self.last_access = time.time()
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self.running.clear()
        self.thread.join(timeout=5)

    def is_active(self):
        # Session is considered active if recently accessed
        return self.running.is_set() and (time.time() - self.last_access < HTTP_STREAM_TIMEOUT)

def clean_expired_sessions():
    while True:
        time.sleep(10)
        with sessions_lock:
            expired = [sid for sid, sess in sessions.items() if not sess.is_active()]
            for sid in expired:
                sess = sessions.pop(sid)
                sess.stop()

class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "HikvisionCameraDriver/1.0"

    def _set_headers(self, code=200, content_type="application/json"):
        self.send_response(code)
        self.send_header('Content-type', content_type)
        self.end_headers()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/camera/stream/init":
            self.handle_stream_init()
        elif parsed_path.path == "/camera/stream/terminate":
            self.handle_stream_terminate()
        elif parsed_path.path == "/stream/stop":
            self.handle_stream_terminate()
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "Not found"}')

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/camera/stream/live":
            self.handle_stream_live(parsed_path)
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "Not found"}')

    def handle_stream_init(self):
        # Create a new streaming session
        session_id = str(uuid.uuid4())
        rtsp_url = build_rtsp_url()
        session = CameraStreamSession(session_id, rtsp_url)
        with sessions_lock:
            sessions[session_id] = session
        http_url = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}/camera/stream/live?session_id={session_id}"
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "session_id": session_id,
            "stream_url": http_url
        }).encode())

    def handle_stream_live(self, parsed_path):
        # Serve MJPEG stream for the session
        query = parse_qs(parsed_path.query)
        session_id = query.get('session_id', [None])[0]
        if not session_id:
            self._set_headers(400)
            self.wfile.write(b'{"error": "session_id required"}')
            return
        with sessions_lock:
            session = sessions.get(session_id)
        if not session or not session.is_active():
            self._set_headers(404)
            self.wfile.write(b'{"error": "Session not found or expired"}')
            return

        self.send_response(200)
        self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        try:
            while session.is_active():
                frame = session.get_frame(timeout=5)
                if frame is None:
                    continue
                self.wfile.write(b'--frame\r\n')
                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
        except (BrokenPipeError, ConnectionAbortedError):
            pass
        except Exception:
            pass

    def handle_stream_terminate(self):
        # Terminate a streaming session
        content_length = int(self.headers.get('Content-Length', 0))
        session_id = None
        if content_length:
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode())
                session_id = payload.get('session_id')
            except Exception:
                pass
        if not session_id:
            # Try to get from query params
            parsed_path = urlparse(self.path)
            query = parse_qs(parsed_path.query)
            session_id = query.get('session_id', [None])[0]
        if not session_id:
            self._set_headers(400)
            self.wfile.write(b'{"error": "session_id required"}')
            return
        with sessions_lock:
            session = sessions.pop(session_id, None)
        if session:
            session.stop()
            self._set_headers(200)
            self.wfile.write(b'{"status": "terminated"}')
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "Session not found"}')

    def log_message(self, format, *args):
        # Suppress default logging
        return

class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run_server():
    clean_thread = threading.Thread(target=clean_expired_sessions, daemon=True)
    clean_thread.start()
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHTTPRequestHandler)
    print(f"Serving on {SERVER_HOST}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    run_server()