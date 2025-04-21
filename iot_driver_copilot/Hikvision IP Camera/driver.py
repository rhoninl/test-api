import os
import threading
import queue
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import base64
import requests
import cv2
import numpy as np

# Configuration from environment
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', '554'))
DEVICE_RTSP_USER = os.environ.get('DEVICE_RTSP_USER', 'admin')
DEVICE_RTSP_PASS = os.environ.get('DEVICE_RTSP_PASS', '12345')
DEVICE_HTTP_PORT = int(os.environ.get('DEVICE_HTTP_PORT', '80'))

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')
RTSP_PARAMS = os.environ.get('RTSP_PARAMS', '')  # e.g. '?transportmode=unicast'

# RTSP URL Construction
if DEVICE_RTSP_USER and DEVICE_RTSP_PASS:
    RTSP_URL = f'rtsp://{DEVICE_RTSP_USER}:{DEVICE_RTSP_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}{RTSP_PARAMS}'
else:
    RTSP_URL = f'rtsp://{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}{RTSP_PARAMS}'

# HTTP snapshot URL
SNAPSHOT_URL = f'http://{DEVICE_IP}:{DEVICE_HTTP_PORT}/ISAPI/Streaming/channels/101/picture'

# Streaming control
streaming_active = False
streaming_lock = threading.Lock()
frame_queue = queue.Queue(maxsize=10)
stream_thread = None


def fetch_rtsp_stream():
    global streaming_active
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
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            try:
                frame_queue.put(jpeg.tobytes(), timeout=1)
            except queue.Full:
                pass  # Drop frame if queue is full
    finally:
        if cap:
            cap.release()


class CameraHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_PUT(self):
        if self.path == '/stream':
            self.handle_start_stream()
        else:
            self.send_error(404, 'Not Found')

    def do_DELETE(self):
        if self.path == '/stream':
            self.handle_stop_stream()
        else:
            self.send_error(404, 'Not Found')

    def do_GET(self):
        if self.path == '/snapshot':
            self.handle_snapshot()
        elif self.path == '/stream':
            self.handle_stream()
        else:
            self.send_error(404, 'Not Found')

    def handle_start_stream(self):
        global streaming_active, stream_thread
        with streaming_lock:
            if not streaming_active:
                streaming_active = True
                stream_thread = threading.Thread(target=fetch_rtsp_stream, daemon=True)
                stream_thread.start()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "stream started"}')
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "stream already running"}')

    def handle_stop_stream(self):
        global streaming_active, stream_thread
        with streaming_lock:
            if streaming_active:
                streaming_active = False
                # Give thread some time to clean up
                if stream_thread is not None:
                    stream_thread.join(timeout=2)
                # Empty frame queue
                while not frame_queue.empty():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        break
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "stream stopped"}')
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "no active stream"}')

    def handle_snapshot(self):
        auth = None
        if DEVICE_RTSP_USER and DEVICE_RTSP_PASS:
            auth = (DEVICE_RTSP_USER, DEVICE_RTSP_PASS)
        headers = {
            'Accept': 'image/jpeg'
        }
        try:
            resp = requests.get(SNAPSHOT_URL, auth=auth, headers=headers, timeout=3)
            if resp.status_code == 200:
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.end_headers()
                self.wfile.write(resp.content)
            else:
                self.send_error(502, f"Camera returned status code {resp.status_code}")
        except Exception as e:
            self.send_error(502, f"Snapshot error: {str(e)}")

    def handle_stream(self):
        global streaming_active
        with streaming_lock:
            if not streaming_active:
                self.send_error(409, 'Stream not started. PUT /stream first.')
                return
        self.send_response(200)
        self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        try:
            while streaming_active:
                try:
                    frame = frame_queue.get(timeout=2)
                except queue.Empty:
                    continue
                self.wfile.write(b'--frame\r\n')
                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(0.04)  # ~25fps limit
        except Exception:
            pass

    def log_message(self, format, *args):
        # Suppress default logging
        return


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server():
    server_address = (SERVER_HOST, SERVER_PORT)
    httpd = ThreadedHTTPServer(server_address, CameraHTTPRequestHandler)
    print(f"Starting server at http://{SERVER_HOST}:{SERVER_PORT}")
    httpd.serve_forever()


if __name__ == '__main__':
    run_server()