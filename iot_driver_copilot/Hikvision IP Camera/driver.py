import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import socket

import cv2
import numpy as np

# Env configuration
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASS = os.environ.get("DEVICE_RTSP_PASS", "12345")
DEVICE_RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

# Build RTSP URL
RTSP_URL = f"rtsp://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{DEVICE_RTSP_PATH}"

# Stream management
class StreamSession:
    def __init__(self):
        self.streaming = False
        self.clients = []
        self.lock = threading.Lock()
        self.capture_thread = None
        self.frame = None
        self.last_frame_time = 0

    def start(self):
        with self.lock:
            if not self.streaming:
                self.streaming = True
                self.capture_thread = threading.Thread(target=self._capture, daemon=True)
                self.capture_thread.start()

    def stop(self):
        with self.lock:
            self.streaming = False
        if self.capture_thread:
            self.capture_thread.join(timeout=1)
            self.capture_thread = None

    def _capture(self):
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            self.frame = None
            return
        while self.streaming:
            ret, frame = cap.read()
            if not ret:
                continue
            # Encode frame as JPEG
            ret2, jpeg = cv2.imencode('.jpg', frame)
            if not ret2:
                continue
            with self.lock:
                self.frame = jpeg.tobytes()
                self.last_frame_time = time.time()
        cap.release()
        self.frame = None

    def get_frame(self):
        with self.lock:
            return self.frame

stream_session = StreamSession()

class Handler(BaseHTTPRequestHandler):
    def _set_headers(self, status=200, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-type', content_type)
        self.end_headers()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path in ["/start", "/video/start"]:
            stream_session.start()
            self._set_headers(200)
            self.wfile.write(b'{"status": "streaming_started"}')
        elif parsed_path.path in ["/stop", "/video/stop"]:
            stream_session.stop()
            self._set_headers(200)
            self.wfile.write(b'{"status": "streaming_stopped"}')
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "not_found"}')

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/stream":
            if not stream_session.streaming:
                stream_session.start()
            self._stream_mjpeg()
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error": "not_found"}')

    def _stream_mjpeg(self):
        self.send_response(200)
        self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        try:
            while stream_session.streaming:
                frame = stream_session.get_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue
                self.wfile.write(b'--frame\r\n')
                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                # Adjust frame rate if needed
                time.sleep(0.04)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            pass

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), Handler)
    print(f"HTTP server running at http://{SERVER_HOST}:{SERVER_PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stream_session.stop()

if __name__ == "__main__":
    run()