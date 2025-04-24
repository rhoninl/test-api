import os
import threading
import io
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import base64
import sys

import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USER = os.environ.get("RTSP_USER", "admin")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# --- Video Stream Session Management ---
class StreamSession:
    def __init__(self):
        self.active = False
        self.lock = threading.Lock()
        self.frame = None
        self.thread = None
        self.stop_event = threading.Event()

    def start(self):
        with self.lock:
            if not self.active:
                self.stop_event.clear()
                self.thread = threading.Thread(target=self._capture_thread, daemon=True)
                self.active = True
                self.thread.start()

    def stop(self):
        with self.lock:
            self.active = False
            self.stop_event.set()
            if self.thread is not None:
                self.thread.join(timeout=3)
                self.thread = None

    def is_active(self):
        with self.lock:
            return self.active

    def get_frame(self):
        return self.frame

    def _capture_thread(self):
        rtsp_url = f"rtsp://{RTSP_USER}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            self.active = False
            return
        while not self.stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.2)
                continue
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                self.frame = jpeg.tobytes()
            time.sleep(0.03)  # ~30 FPS
        cap.release()
        self.active = False

stream_session = StreamSession()

# --- HTTP Server and Handlers ---
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class CameraRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        if self.path == "/stream":
            self._handle_activate_stream()
        else:
            self.send_error(404, "Not Found")

    def do_GET(self):
        if self.path == "/stream":
            self._handle_get_stream()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        if self.path == "/stream":
            self._handle_deactivate_stream()
        else:
            self.send_error(404, "Not Found")

    def _handle_activate_stream(self):
        if not stream_session.is_active():
            stream_session.start()
            time.sleep(0.5)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"streaming","message":"Stream activated"}')

    def _handle_deactivate_stream(self):
        stream_session.stop()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"stopped","message":"Stream stopped"}')

    def _handle_get_stream(self):
        if not stream_session.is_active():
            self.send_response(409)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"Stream not active. POST /stream to activate."}')
            return

        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while stream_session.is_active():
                frame = stream_session.get_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue
                self.wfile.write(b'--frame\r\n')
                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
                self.wfile.flush()
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        return

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), CameraRequestHandler)
    print(f"HTTP server running at http://{SERVER_HOST}:{SERVER_PORT}/stream")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stream_session.stop()
        server.server_close()

if __name__ == "__main__":
    run()