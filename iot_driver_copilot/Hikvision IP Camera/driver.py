import os
import threading
import io
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
import cv2
import numpy as np

# Environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASS = os.environ.get("DEVICE_RTSP_PASS", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", "/Streaming/Channels/101")
RTSP_PROTOCOL = os.environ.get("DEVICE_RTSP_PROTOCOL", "rtsp")
RTSP_URL = f"{RTSP_PROTOCOL}://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}"

STREAM_BOUNDARY = "frame"

class VideoStreamHandler:
    def __init__(self):
        self._capture = None
        self._lock = threading.Lock()
        self._is_streaming = False
        self._frame = None
        self._last_frame_time = 0
        self._thread = None

    def start(self):
        with self._lock:
            if self._is_streaming:
                return
            self._is_streaming = True
            self._capture = cv2.VideoCapture(RTSP_URL)
            self._thread = threading.Thread(target=self._update, daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._is_streaming = False
            if self._capture:
                self._capture.release()
                self._capture = None
            self._thread = None

    def _update(self):
        while True:
            with self._lock:
                if not self._is_streaming or not self._capture:
                    break
            ret, frame = self._capture.read()
            if not ret:
                time.sleep(0.2)
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                self._frame = jpeg.tobytes()
                self._last_frame_time = time.time()
            # To avoid CPU hogging
            time.sleep(0.03)  # ~30fps

    def get_frame(self, timeout=2):
        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                if self._frame is not None:
                    return self._frame
            time.sleep(0.02)
        return None

    def is_streaming(self):
        with self._lock:
            return self._is_streaming

streamer = VideoStreamHandler()

class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/video":
            if not streamer.is_streaming():
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Stream not started. Use POST /video/start to begin streaming.")
                return

            self.send_response(200)
            self.send_header('Content-type', f'multipart/x-mixed-replace; boundary={STREAM_BOUNDARY}')
            self.end_headers()
            try:
                while streamer.is_streaming():
                    frame = streamer.get_frame()
                    if frame is None:
                        continue
                    self.wfile.write(
                        bytes(f"--{STREAM_BOUNDARY}\r\n", "utf-8") +
                        b"Content-Type: image/jpeg\r\n" +
                        bytes(f"Content-Length: {len(frame)}\r\n\r\n", "utf-8") +
                        frame +
                        b"\r\n"
                    )
            except (ConnectionResetError, BrokenPipeError):
                pass  # Client disconnected
            return
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not found")
            return

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/video/start":
            streamer.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"stream started"}')
            return
        elif parsed.path == "/video/stop":
            streamer.stop()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"stream stopped"}')
            return
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not found")
            return

    def log_message(self, format, *args):
        return  # Silence default logging

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def main():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHTTPRequestHandler)
    print(f"HTTP server running on {SERVER_HOST}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        streamer.stop()
        server.server_close()

if __name__ == "__main__":
    main()