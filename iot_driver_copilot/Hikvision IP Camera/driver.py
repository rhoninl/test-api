import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import cv2
import numpy as np

DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", "admin")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

BOUNDARY = "frameBOUNDARY"

class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', f'multipart/x-mixed-replace; boundary={BOUNDARY}')
            self.end_headers()
            try:
                cap = cv2.VideoCapture(RTSP_URL)
                if not cap.isOpened():
                    self.send_error(502, f"Could not connect to RTSP stream at {RTSP_URL}")
                    return
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        continue
                    ret, jpg = cv2.imencode('.jpg', frame)
                    if not ret:
                        continue
                    self.wfile.write(bytes(f"--{BOUNDARY}\r\n", 'utf-8'))
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(bytes(f"Content-Length: {len(jpg.tobytes())}\r\n\r\n", 'utf-8'))
                    self.wfile.write(jpg.tobytes())
                    self.wfile.write(b"\r\n")
            except BrokenPipeError:
                pass
            except Exception:
                pass
            finally:
                try:
                    cap.release()
                except Exception:
                    pass
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')

    def log_message(self, format, *args):
        return  # Suppress logging to stdout

class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), StreamingHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    run()