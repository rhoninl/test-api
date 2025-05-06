import os
import threading
import queue
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
from urllib.parse import urlparse
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')

HTTP_SERVER_HOST = os.environ.get('HTTP_SERVER_HOST', '0.0.0.0')
HTTP_SERVER_PORT = int(os.environ.get('HTTP_SERVER_PORT', '8080'))

# RTSP URL construction (main stream)
RTSP_STREAM_PATH = os.environ.get('DEVICE_RTSP_PATH', '/Streaming/Channels/101')
RTSP_URL = f'rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_STREAM_PATH}'

# Camera ONVIF/ISAPI Start/Stop endpoints (for Hikvision, usually ISAPI)
START_STREAM_ENDPOINT = os.environ.get('DEVICE_START_ENDPOINT', f'http://{DEVICE_IP}/ISAPI/Streaming/channels/101/stream')
STOP_STREAM_ENDPOINT = os.environ.get('DEVICE_STOP_ENDPOINT', f'http://{DEVICE_IP}/ISAPI/Streaming/channels/101/stream')

# Threading primitives
streaming_active = threading.Event()
frame_queue = queue.Queue(maxsize=100)

def start_stream_on_camera():
    # For Hikvision, streams are usually always on, but we mimic the 'start' via ISAPI or ONVIF
    # You may want to perform a HTTP request to the camera to start the stream if required
    # For most models, this is not needed, but endpoint is provided for compliance
    try:
        resp = requests.put(START_STREAM_ENDPOINT, auth=HTTPDigestAuth(DEVICE_USERNAME, DEVICE_PASSWORD), timeout=3)
        return resp.status_code in (200, 201, 204)
    except Exception:
        return False

def stop_stream_on_camera():
    # Same as above, for compliance
    try:
        resp = requests.delete(STOP_STREAM_ENDPOINT, auth=HTTPDigestAuth(DEVICE_USERNAME, DEVICE_PASSWORD), timeout=3)
        return resp.status_code in (200, 201, 204)
    except Exception:
        return False

def rtsp_capture_worker():
    cap = None
    while streaming_active.is_set():
        if cap is None:
            cap = cv2.VideoCapture(RTSP_URL)
            if not cap.isOpened():
                time.sleep(1)
                continue
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            try:
                frame_queue.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                pass
    if cap is not None:
        cap.release()

class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/stream':
            if not streaming_active.is_set():
                self.send_response(503)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"Stream is not active.")
                return
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            try:
                while streaming_active.is_set():
                    try:
                        frame = frame_queue.get(timeout=2)
                        self.wfile.write(b'--frame\r\n')
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', str(len(frame)))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                    except queue.Empty:
                        continue
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        # /start and /video/start both trigger the same logic
        if parsed.path in ['/start', '/video/start']:
            if streaming_active.is_set():
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "already running"}')
                return
            # Optionally: Start camera stream via API (if needed)
            start_stream_on_camera()
            streaming_active.set()
            # Start capture worker if not already running
            threading.Thread(target=rtsp_capture_worker, daemon=True).start()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "stream started"}')
            return
        # /stop and /video/stop both trigger the same logic
        elif parsed.path in ['/stop', '/video/stop']:
            if not streaming_active.is_set():
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "already stopped"}')
                return
            streaming_active.clear()
            stop_stream_on_camera()
            # Drain the frame queue
            while not frame_queue.empty():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    break
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "stream stopped"}')
            return
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default logging
        return

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run_server():
    server_address = (HTTP_SERVER_HOST, HTTP_SERVER_PORT)
    httpd = ThreadedHTTPServer(server_address, StreamingHandler)
    print(f"Hikvision Camera driver server running at http://{HTTP_SERVER_HOST}:{HTTP_SERVER_PORT}")
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()