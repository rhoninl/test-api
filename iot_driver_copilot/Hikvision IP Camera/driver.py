import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import requests
import cv2
import numpy as np

# Environment variables
DEVICE_IP = os.getenv('DEVICE_IP', '192.168.1.64')
DEVICE_RTSP_PORT = int(os.getenv('DEVICE_RTSP_PORT', '554'))
DEVICE_USERNAME = os.getenv('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.getenv('DEVICE_PASSWORD', '12345')
DEVICE_HTTP_PORT = int(os.getenv('DEVICE_HTTP_PORT', '80'))

SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', '8080'))

RTSP_STREAM_PATH = os.getenv('RTSP_STREAM_PATH', '/Streaming/Channels/101')
SNAPSHOT_PATH = os.getenv('SNAPSHOT_PATH', '/ISAPI/Streaming/channels/101/picture')
RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_STREAM_PATH}"

# Global streaming control
streaming_active = threading.Event()
streaming_active.clear()
stream_lock = threading.Lock()

class StreamingHandler:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.capture = None
        self.thread = None
        self.frame = None
        self.frame_lock = threading.Lock()
        self.running = False

    def start(self):
        with stream_lock:
            if self.running:
                return
            self.running = True
            self.capture = cv2.VideoCapture(self.rtsp_url)
            self.thread = threading.Thread(target=self.update, daemon=True)
            self.thread.start()
            streaming_active.set()

    def stop(self):
        with stream_lock:
            self.running = False
            streaming_active.clear()
            if self.capture:
                self.capture.release()
                self.capture = None

    def update(self):
        while self.running:
            if self.capture is None:
                break
            ret, frame = self.capture.read()
            if ret:
                with self.frame_lock:
                    self.frame = frame
            else:
                time.sleep(0.05)
        self.stop()

    def get_frame(self):
        with self.frame_lock:
            if self.frame is not None:
                ret, jpeg = cv2.imencode('.jpg', self.frame)
                if ret:
                    return jpeg.tobytes()
            return None

    def mjpeg_generator(self):
        while streaming_active.is_set():
            frame = self.get_frame()
            if frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                time.sleep(0.05)

stream_handler = StreamingHandler(RTSP_URL)

class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == '/snap':
            snapshot_url = f"http://{DEVICE_IP}:{DEVICE_HTTP_PORT}{SNAPSHOT_PATH}"
            try:
                resp = requests.get(snapshot_url, auth=(DEVICE_USERNAME, DEVICE_PASSWORD), timeout=5, stream=True)
                if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image'):
                    self.send_response(200)
                    self.send_header('Content-type', 'image/jpeg')
                    self.end_headers()
                    for chunk in resp.iter_content(4096):
                        self.wfile.write(chunk)
                else:
                    self.send_response(502)
                    self.end_headers()
                    self.wfile.write(b"Unable to fetch snapshot")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Snapshot fetch error: " + str(e).encode())
        elif self.path == '/stream':
            if not streaming_active.is_set():
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Streaming not started")
                return
            self.send_response(200)
            self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            for frame in stream_handler.mjpeg_generator():
                try:
                    self.wfile.write(frame)
                except Exception:
                    break
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_POST(self):
        if self.path == '/start':
            stream_handler.start()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Streaming started")
        elif self.path == '/stop':
            stream_handler.stop()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Streaming stopped")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        # Suppress default logging to keep output clean
        pass

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHTTPRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stream_handler.stop()
        server.server_close()

if __name__ == '__main__':
    run()