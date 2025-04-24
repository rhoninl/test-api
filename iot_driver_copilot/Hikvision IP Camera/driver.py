import os
import io
import threading
import time
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import base64
import socket

import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USER = os.environ.get("DEVICE_USER", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "admin")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
DEVICE_STREAM_PATH = os.environ.get("DEVICE_STREAM_PATH", "/Streaming/Channels/101")

RTSP_URL_TEMPLATE = "rtsp://{user}:{pwd}@{ip}:{port}{path}"

class CameraStreamer:
    def __init__(self):
        self.rtsp_url = RTSP_URL_TEMPLATE.format(
            user=DEVICE_USER,
            pwd=DEVICE_PASSWORD,
            ip=DEVICE_IP,
            port=DEVICE_RTSP_PORT,
            path=DEVICE_STREAM_PATH
        )
        self.running = False
        self.thread = None
        self.frame_queue = queue.Queue(maxsize=10)
        self.lock = threading.Lock()
        self.last_frame = None

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        # Empty the queue
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break

    def is_running(self):
        with self.lock:
            return self.running

    def _capture_loop(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.running = False
            return
        while self.running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            img_bytes = jpeg.tobytes()
            self.last_frame = img_bytes
            try:
                self.frame_queue.put_nowait(img_bytes)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frame_queue.put_nowait(img_bytes)
                except queue.Full:
                    pass
        cap.release()

    def get_frame(self, timeout=1):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            # If no new frame, return the last available one
            return self.last_frame

camera_streamer = CameraStreamer()

class HikvisionHandler(BaseHTTPRequestHandler):
    # Only allow /start, /stop, /video endpoints

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/start":
            camera_streamer.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            # Provide the direct HTTP MJPEG stream URL
            url = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}/video"
            self.wfile.write(('{"status":"started","video_url":"%s"}' % url).encode("utf-8"))
        elif parsed.path == "/stop":
            camera_streamer.stop()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"stopped"}')
        else:
            self.send_error(404, "Not Found")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/video":
            if not camera_streamer.is_running():
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Stream not started. Use POST /start first.')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            try:
                while camera_streamer.is_running():
                    frame = camera_streamer.get_frame(timeout=2)
                    if frame is None:
                        continue
                    self.wfile.write(b'--frame\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                    # To avoid spamming, sleep a bit (target ~15fps)
                    time.sleep(0.066)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        return  # Silence default logging

class ThreadedHTTPServer(HTTPServer):
    allow_reuse_address = True
    def server_bind(self):
        # If SERVER_HOST is 0.0.0.0, use local IP for returned URLs
        HTTPServer.server_bind(self)
        if SERVER_HOST == "0.0.0.0":
            self.server_address = (self.get_local_ip(), self.server_port)
    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't need to be reachable
            s.connect(('10.255.255.255', 1))
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHandler)
    print(f"Hikvision Camera HTTP Driver running at http://{server.server_address[0]}:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera_streamer.stop()
        server.server_close()

if __name__ == "__main__":
    run()