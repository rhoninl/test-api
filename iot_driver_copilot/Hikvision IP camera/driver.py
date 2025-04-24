import os
import threading
import queue
import time
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
import base64
import requests

import cv2
import numpy as np

# Configuration from environment variables
CAMERA_IP = os.environ.get("CAMERA_IP", "192.168.1.64")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USERNAME = os.environ.get("CAMERA_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "admin")
CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "Streaming/Channels/101")
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8000"))
MJPEG_FPS = int(os.environ.get("MJPEG_FPS", "10"))

RTSP_URL = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"

STREAM_ACTIVE_FLAG = threading.Event()
STREAM_EXIT_FLAG = threading.Event()
FRAME_QUEUE = queue.Queue(maxsize=10)
STREAM_LOCK = threading.Lock()


def camera_stream_worker():
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            return
        while STREAM_ACTIVE_FLAG.is_set() and not STREAM_EXIT_FLAG.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                FRAME_QUEUE.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                pass
            time.sleep(1 / MJPEG_FPS)
    finally:
        if cap:
            cap.release()
        FRAME_QUEUE.queue.clear()
        STREAM_EXIT_FLAG.clear()


class HikvisionHTTPHandler(BaseHTTPRequestHandler):
    server_version = "HikvisionDriverHTTP/1.0"

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/stream":
            self.handle_start_stream()
        else:
            self.send_error(404, "Not Found")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/stream":
            self.handle_get_stream()
        else:
            self.send_error(404, "Not Found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == "/stream":
            self.handle_stop_stream()
        else:
            self.send_error(404, "Not Found")

    def handle_start_stream(self):
        with STREAM_LOCK:
            if not STREAM_ACTIVE_FLAG.is_set():
                STREAM_ACTIVE_FLAG.set()
                STREAM_EXIT_FLAG.clear()
                thread = threading.Thread(target=camera_stream_worker, daemon=True)
                thread.start()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "stream started"}')

    def handle_get_stream(self):
        if not STREAM_ACTIVE_FLAG.is_set():
            self.send_response(409)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error": "Stream is not active. POST /stream to start."}')
            return

        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            while STREAM_ACTIVE_FLAG.is_set():
                try:
                    frame = FRAME_QUEUE.get(timeout=2)
                except queue.Empty:
                    continue
                self.wfile.write(b'--frame\r\n')
                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
        except Exception:
            pass  # Client closed connection
        finally:
            pass

    def handle_stop_stream(self):
        with STREAM_LOCK:
            STREAM_ACTIVE_FLAG.clear()
            STREAM_EXIT_FLAG.set()
            FRAME_QUEUE.queue.clear()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "stream stopped"}')

    def log_message(self, format, *args):
        # Optional: comment out to suppress logs
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server():
    server = ThreadedHTTPServer((HTTP_SERVER_HOST, HTTP_SERVER_PORT), HikvisionHTTPHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()