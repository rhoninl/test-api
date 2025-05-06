import os
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import base64
import urllib.request
import socket

import cv2
import numpy as np

# Environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_HTTP_PORT = int(os.environ.get('DEVICE_HTTP_PORT', '80'))
DEVICE_USER = os.environ.get('DEVICE_USER', 'admin')
DEVICE_PASS = os.environ.get('DEVICE_PASS', 'admin123')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
STREAM_PATH = os.environ.get('DEVICE_RTSP_STREAM_PATH', 'Streaming/Channels/101')
# e.g., set to 'Streaming/Channels/101' for main stream, 'Streaming/Channels/102' for sub stream

RTSP_URL = f'rtsp://{DEVICE_USER}:{DEVICE_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{STREAM_PATH}'
SNAPSHOT_URL = f'http://{DEVICE_IP}:{DEVICE_HTTP_PORT}/ISAPI/Streaming/channels/101/picture'

# Shared video capture state
streaming_lock = threading.Lock()
streaming_active = False
frame_buffer = []
frame_buffer_lock = threading.Lock()
frame_clients = set()

def fetch_jpeg_snapshot():
    # Hikvision's snapshot URL may require digest/basic auth
    passman = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passman.add_password(None, SNAPSHOT_URL, DEVICE_USER, DEVICE_PASS)
    authhandler = urllib.request.HTTPBasicAuthHandler(passman)
    opener = urllib.request.build_opener(authhandler)
    try:
        with opener.open(SNAPSHOT_URL, timeout=5) as response:
            return response.read(), response.getheader('Content-Type', 'image/jpeg')
    except Exception:
        return None, None

def stream_rtsp_frames():
    global streaming_active, frame_buffer
    cap = None
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            streaming_active = False
            return
        while streaming_active:
            ret, frame = cap.read()
            if not ret:
                continue
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            with frame_buffer_lock:
                frame_buffer = [jpeg.tobytes()]  # keep only latest frame
            time.sleep(0.04)  # ~25 FPS
    finally:
        if cap:
            cap.release()

def get_latest_frame():
    with frame_buffer_lock:
        if frame_buffer:
            return frame_buffer[-1]
        else:
            return None

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

class HikvisionHandler(BaseHTTPRequestHandler):
    server_version = "HikvisionIPCamHTTP/1.0"
    protocol_version = "HTTP/1.1"

    def _set_headers(self, code=200, content_type='text/html', extra_headers=None):
        self.send_response(code)
        self.send_header('Server', self.server_version)
        self.send_header('Access-Control-Allow-Origin', '*')
        if content_type:
            self.send_header('Content-Type', content_type)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        global streaming_active
        if self.path == '/start':
            with streaming_lock:
                if not streaming_active:
                    streaming_active = True
                    t = threading.Thread(target=stream_rtsp_frames, daemon=True)
                    t.start()
            self._set_headers(200, 'application/json')
            self.wfile.write(b'{"status":"streaming started"}')
        elif self.path == '/stop':
            with streaming_lock:
                streaming_active = False
            self._set_headers(200, 'application/json')
            self.wfile.write(b'{"status":"streaming stopped"}')
        else:
            self._set_headers(404)
            self.wfile.write(b'{"error":"Not found"}')
    
    def do_GET(self):
        if self.path == '/snap':
            image, content_type = fetch_jpeg_snapshot()
            if image:
                self._set_headers(200, content_type)
                self.wfile.write(image)
                return
            # fallback: try to get from stream
            frame = get_latest_frame()
            if frame:
                self._set_headers(200, 'image/jpeg')
                self.wfile.write(frame)
            else:
                self._set_headers(503, 'application/json')
                self.wfile.write(b'{"error":"No image available"}')
        elif self.path == '/video':
            if not streaming_active:
                self._set_headers(503, 'application/json')
                self.wfile.write(b'{"error":"Streaming is not active. POST /start first."}')
                return
            self._set_headers(200, 'multipart/x-mixed-replace; boundary=frame')
            try:
                while streaming_active:
                    frame = get_latest_frame()
                    if frame is None:
                        time.sleep(0.1)
                        continue
                    self.wfile.write(b'--frame\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
                    time.sleep(0.04)
            except (ConnectionResetError, BrokenPipeError):
                pass
        else:
            self._set_headers(404, 'application/json')
            self.wfile.write(b'{"error":"Not found"}')

def run():
    httpd = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHandler)
    print(f"Starting Hikvision IP Camera HTTP server at http://{SERVER_HOST}:{SERVER_PORT}")
    print(f"POST   /start   - Start live video stream")
    print(f"GET    /video   - Watch live MJPEG stream (browser compatible)")
    print(f"GET    /snap    - Get snapshot JPEG image")
    print(f"POST   /stop    - Stop live video stream")
    httpd.serve_forever()

if __name__ == '__main__':
    run()