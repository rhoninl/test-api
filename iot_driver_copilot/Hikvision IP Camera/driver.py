import os
import threading
import queue
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
import requests

import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
DEVICE_CHANNEL = os.environ.get("DEVICE_CHANNEL", "101")  # 101 is often the main stream

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# RTSP URL Construction
RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/Streaming/Channels/{DEVICE_CHANNEL}"

STREAM_BOUNDARY = "frame"

class VideoStreamThread(threading.Thread):
    def __init__(self, rtsp_url, frame_queue, stop_event):
        super().__init__()
        self.rtsp_url = rtsp_url
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.daemon = True

    def run(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.frame_queue.put(None)
            return
        while not self.stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                self.frame_queue.put(None)
                break
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            self.frame_queue.put(jpeg.tobytes())
            # Throttle if needed (e.g., to ~10-20 FPS)
            time.sleep(0.05)
        cap.release()

class StreamManager:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=20)
        self.stop_event = threading.Event()
        self.stream_thread = None
        self.active = False
        self.lock = threading.Lock()
        self.viewer_count = 0

    def start_stream(self):
        with self.lock:
            if self.active:
                return True
            self.stop_event.clear()
            self.frame_queue = queue.Queue(maxsize=20)
            self.stream_thread = VideoStreamThread(self.rtsp_url, self.frame_queue, self.stop_event)
            self.stream_thread.start()
            self.active = True
            return True

    def stop_stream(self):
        with self.lock:
            if not self.active:
                return False
            self.stop_event.set()
            if self.stream_thread is not None:
                self.stream_thread.join(timeout=2.0)
            self.active = False
            # Empty queue to unblock any waiting viewers
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except:
                    break
            return True

    def get_frame(self, timeout=5):
        try:
            frame = self.frame_queue.get(timeout=timeout)
            return frame
        except queue.Empty:
            return None

    def is_active(self):
        return self.active

    def add_viewer(self):
        with self.lock:
            self.viewer_count += 1
            # Auto-start stream if needed
            if self.viewer_count == 1 and not self.active:
                self.start_stream()

    def remove_viewer(self):
        with self.lock:
            self.viewer_count = max(0, self.viewer_count - 1)
            # Optionally, auto-stop when all viewers gone
            if self.viewer_count == 0 and self.active:
                self.stop_stream()

stream_manager = StreamManager(RTSP_URL)

class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/video/start":
            success = stream_manager.start_stream()
            self.send_response(200 if success else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "started"}' if success else b'{"status": "error"}')
        elif self.path == "/video/stop":
            success = stream_manager.stop_stream()
            self.send_response(200 if success else 500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "stopped"}' if success else b'{"status": "error"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/video/live":
            if not stream_manager.is_active():
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "stream not started"}')
                return

            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={STREAM_BOUNDARY}")
            self.end_headers()
            stream_manager.add_viewer()
            try:
                while stream_manager.is_active():
                    frame = stream_manager.get_frame(timeout=5)
                    if frame is None:
                        break
                    self.wfile.write(
                        bytes(f"--{STREAM_BOUNDARY}\r\n", "utf-8") +
                        b"Content-Type: image/jpeg\r\n" +
                        bytes(f"Content-Length: {len(frame)}\r\n\r\n", "utf-8") +
                        frame +
                        b"\r\n"
                    )
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                stream_manager.remove_viewer()
        elif self.path == "/video/html":
            # Simple HTML player for browser viewing
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = f"""<html>
<head><title>Hikvision Camera Live Stream</title></head>
<body>
<h2>Live Stream (Hikvision IP Camera)</h2>
<img src="/video/live" style="width: 100%; max-width: 720px;" />
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress console logging for cleaner output; uncomment for debugging
        pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    server_address = (SERVER_HOST, SERVER_PORT)
    httpd = ThreadedHTTPServer(server_address, HikvisionHTTPRequestHandler)
    print(f"HTTP video server running at http://{SERVER_HOST}:{SERVER_PORT}/video/html")
    httpd.serve_forever()

if __name__ == "__main__":
    run()