import os
import io
import threading
import base64
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import requests
import socket
import struct

# Environment Variables
DEVICE_IP = os.getenv("DEVICE_IP", "192.168.1.64")
DEVICE_HTTP_PORT = int(os.getenv("DEVICE_HTTP_PORT", "80"))
DEVICE_RTSP_PORT = int(os.getenv("DEVICE_RTSP_PORT", "554"))
DEVICE_USER = os.getenv("DEVICE_USER", "admin")
DEVICE_PASS = os.getenv("DEVICE_PASS", "12345")
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8080"))

# RTSP stream path (may be /Streaming/Channels/101 for main stream)
RTSP_PATH = os.getenv("RTSP_PATH", "/Streaming/Channels/101")

# Helper: Basic Auth Header
def get_auth_header():
    auth_str = f"{DEVICE_USER}:{DEVICE_PASS}"
    auth_bytes = auth_str.encode('utf-8')
    return "Basic " + base64.b64encode(auth_bytes).decode('utf-8')

# Helper: Get Snapshot JPEG
def get_snapshot_jpeg():
    url = f"http://{DEVICE_IP}:{DEVICE_HTTP_PORT}/ISAPI/Streaming/channels/101/picture"
    headers = {"Authorization": get_auth_header()}
    resp = requests.get(url, headers=headers, timeout=8)
    if resp.status_code == 200:
        return resp.content
    else:
        raise Exception(f"Snapshot failed with code {resp.status_code}")

# RTSP Stream Proxy (RTP over TCP) to HTTP (multipart/x-mixed-replace with JPEG)
# This decodes only JPEG frames (if available in stream)
class RTSPProxyStream(threading.Thread):
    def __init__(self, handler):
        threading.Thread.__init__(self)
        self.handler = handler
        self.running = True
        self.rtsp_socket = None

    def stop(self):
        self.running = False
        if self.rtsp_socket:
            try:
                self.rtsp_socket.shutdown(socket.SHUT_RDWR)
                self.rtsp_socket.close()
            except:
                pass

    def run(self):
        try:
            url = f"rtsp://{DEVICE_USER}:{DEVICE_PASS}@{DEVICE_IP}:{DEVICE_RTSP_PORT}{RTSP_PATH}"
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((DEVICE_IP, DEVICE_RTSP_PORT))
            self.rtsp_socket = s

            cseq = 1
            session = None

            def send_rtsp(cmd):
                nonlocal cseq, session
                s.sendall((cmd % {"cseq": cseq, "session": session if session else ""}).encode('utf-8'))
                cseq += 1
                data = b""
                while b"\r\n\r\n" not in data:
                    data += s.recv(4096)
                return data.decode('utf-8')

            # 1. OPTIONS
            send_rtsp(
                "OPTIONS rtsp://%s:%d%s RTSP/1.0\r\nCSeq: %(cseq)d\r\nAuthorization: %s\r\n\r\n"
                % (DEVICE_IP, DEVICE_RTSP_PORT, RTSP_PATH, get_auth_header())
            )
            # 2. DESCRIBE
            describe = send_rtsp(
                "DESCRIBE rtsp://%s:%d%s RTSP/1.0\r\nCSeq: %(cseq)d\r\nAccept: application/sdp\r\nAuthorization: %s\r\n\r\n"
                % (DEVICE_IP, DEVICE_RTSP_PORT, RTSP_PATH, get_auth_header())
            )
            # 3. SETUP (RTP over TCP, interleaved 0/1)
            setup = send_rtsp(
                "SETUP rtsp://%s:%d%s/trackID=1 RTSP/1.0\r\nCSeq: %(cseq)d\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nAuthorization: %s\r\n\r\n"
                % (DEVICE_IP, DEVICE_RTSP_PORT, RTSP_PATH, get_auth_header())
            )
            # Extract Session ID
            for line in setup.split("\r\n"):
                if line.lower().startswith("session:"):
                    session = line.split(":")[1].split(";")[0].strip()
                    break
            # 4. PLAY
            play = send_rtsp(
                "PLAY rtsp://%s:%d%s RTSP/1.0\r\nCSeq: %(cseq)d\r\nSession: %(session)s\r\nAuthorization: %s\r\nRange: npt=0.000-\r\n\r\n"
                % (DEVICE_IP, DEVICE_RTSP_PORT, RTSP_PATH, get_auth_header())
            )
            # 5. Start reading RTP over TCP (interleaved)
            self.handler.send_response(200)
            self.handler.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
            self.handler.end_headers()
            buf = b''
            s.settimeout(2)
            while self.running:
                try:
                    pkt = s.recv(4096)
                    if not pkt:
                        break
                    buf += pkt
                    while True:
                        if len(buf) < 4:
                            break
                        if buf[0] != 0x24:
                            buf = buf[1:]
                            continue
                        channel = buf[1]
                        pkt_len = struct.unpack(">H", buf[2:4])[0]
                        if len(buf) < 4 + pkt_len:
                            break
                        rtp_payload = buf[4:4+pkt_len]
                        # Only look for JPEG payloads (not full video)
                        # A proper implementation would parse RTP and MJPEG payloads.
                        # Here, we look for JPEG SOI/EOI markers in the payload.
                        soi = rtp_payload.find(b'\xff\xd8')
                        eoi = rtp_payload.find(b'\xff\xd9')
                        if soi != -1 and eoi != -1 and eoi > soi:
                            jpeg = rtp_payload[soi:eoi+2]
                            self.handler.wfile.write(b"--frame\r\n")
                            self.handler.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                            self.handler.wfile.write(jpeg)
                            self.handler.wfile.write(b"\r\n")
                        buf = buf[4+pkt_len:]
                except socket.timeout:
                    continue
                except Exception:
                    break
        finally:
            try:
                self.rtsp_socket.close()
            except:
                pass

# HTTP Server Handler
class HikvisionHandler(BaseHTTPRequestHandler):
    server_version = "HikvisionPythonProxy/1.0"
    stream_proxy_thread = None

    def do_POST(self):
        if self.path == "/start":
            if self.stream_proxy_thread and self.stream_proxy_thread.is_alive():
                self.send_response(409)
                self.end_headers()
                self.wfile.write(b"Stream already running.")
                return
            # Respond with HTTP stream URL for browser
            stream_url = f"http://{SERVER_HOST if SERVER_HOST != '0.0.0.0' else 'localhost'}:{SERVER_PORT}/stream"
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(f'{{"stream_url": "{stream_url}"}}'.encode('utf-8'))
            return

        elif self.path == "/stop":
            if self.stream_proxy_thread:
                self.stream_proxy_thread.stop()
                self.stream_proxy_thread = None
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Stream stopped")
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_GET(self):
        if self.path == "/snap":
            try:
                img = get_snapshot_jpeg()
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.send_header('Content-length', str(len(img)))
                self.end_headers()
                self.wfile.write(img)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
            return

        elif self.path == "/stream":
            # Start the RTSP-to-HTTP proxy stream
            if self.stream_proxy_thread and self.stream_proxy_thread.is_alive():
                self.send_response(409)
                self.end_headers()
                self.wfile.write(b"Stream already running.")
                return
            self.stream_proxy_thread = RTSPProxyStream(self)
            self.stream_proxy_thread.start()
            while self.stream_proxy_thread.is_alive():
                time.sleep(0.1)
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        # Suppress logging
        return

def run():
    server = HTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    run()