import os
import threading
import io
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "admin123")
DEVICE_RTSP_PATH = os.environ.get("DEVICE_RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{DEVICE_RTSP_PATH}"

class StreamBuffer:
    def __init__(self):
        self.frame = None
        self.lock = threading.Lock()

    def update(self, frame):
        with self.lock:
            self.frame = frame

    def get(self):
        with self.lock:
            return self.frame

stream_buffer = StreamBuffer()
stop_event = threading.Event()

def stream_capture():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        print("Failed to connect to camera stream.")
        return
    while not stop_event.is_set():
        ret, frame = cap.read()
        if ret:
            # Encode frame as JPEG for browser compatibility
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                stream_buffer.update(jpeg.tobytes())
        else:
            # Attempt to reconnect after a short delay
            time.sleep(0.5)
            cap.release()
            cap = cv2.VideoCapture(RTSP_URL)
    cap.release()

class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            try:
                while not stop_event.is_set():
                    frame = stream_buffer.get()
                    if frame is not None:
                        self.wfile.write(b'--frame\r\n')
                        self.wfile.write(b'Content-Type: image/jpeg\r\n')
                        self.wfile.write(b'Content-Length: %d\r\n\r\n' % len(frame))
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                    time.sleep(0.04)  # ~25fps
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'404 Not Found')

    def log_message(self, format, *args):
        return  # Suppress default logging

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    capture_thread = threading.Thread(target=stream_capture, daemon=True)
    capture_thread.start()
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), StreamingHandler)
    try:
        print(f"Starting server at http://{SERVER_HOST}:{SERVER_PORT}/stream")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    run()