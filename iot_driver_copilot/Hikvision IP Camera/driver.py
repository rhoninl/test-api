import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import base64
import socket

import cv2
import numpy as np

# Environment Variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASS = os.environ.get("DEVICE_RTSP_PASS", "12345")
RTSP_STREAM_PATH = os.environ.get("RTSP_STREAM_PATH", "/Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_STREAM_PATH}"

class VideoStreamHandler:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.running = False
        self.frame = None
        self.lock = threading.Lock()
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self):
        # OpenCV VideoCapture for RTSP stream
        self.capture = cv2.VideoCapture(self.rtsp_url)
        while self.running and self.capture.isOpened():
            ret, frame = self.capture.read()
            if not ret:
                time.sleep(0.1)
                continue
            with self.lock:
                self.frame = frame
        if self.capture is not None:
            self.capture.release()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def get_frame(self):
        with self.lock:
            if self.frame is not None:
                return self.frame.copy()
            else:
                return None

# Shared Video Streamer
streamer = VideoStreamHandler(RTSP_URL)

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # /start or /video/start
        if self.path in ["/start", "/video/start"]:
            streamer.start()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"started"}')
            return
        # /stop or /video/stop
        if self.path in ["/stop", "/video/stop"]:
            streamer.stop()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"stopped"}')
            return
        self.send_error(404, "Not Found")

    def do_GET(self):
        # /stream - MJPEG stream
        if self.path == "/stream":
            self.send_response(200)
            self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            # Try to keep streaming as long as possible
            try:
                while streamer.running:
                    frame = streamer.get_frame()
                    if frame is None:
                        time.sleep(0.1)
                        continue
                    # Encode as JPEG
                    ret, jpeg = cv2.imencode('.jpg', frame)
                    if not ret:
                        continue
                    self.wfile.write(b'--frame\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(jpeg.size))
                    self.end_headers()
                    self.wfile.write(jpeg.tobytes())
                    self.wfile.write(b'\r\n')
                    # Adjust frame rate for browser
                    time.sleep(0.04)  # ~25fps
            except (BrokenPipeError, ConnectionResetError, socket.error):
                pass
            return
        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        # Suppress default logging
        return

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHTTPRequestHandler)
    print(f"Serving on http://{SERVER_HOST}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        streamer.stop()
        server.server_close()

if __name__ == "__main__":
    run()