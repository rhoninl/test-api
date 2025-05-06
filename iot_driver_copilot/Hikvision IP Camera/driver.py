import os
import io
import threading
import queue
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import socket
import struct
import base64

# Configuration from environment variables
DEVICE_IP = os.environ.get("HIKVISION_DEVICE_IP", "192.168.1.64")
DEVICE_RTSP_PORT = int(os.environ.get("HIKVISION_RTSP_PORT", "554"))
DEVICE_USERNAME = os.environ.get("HIKVISION_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("HIKVISION_PASSWORD", "12345")
SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))
RTSP_PATH = os.environ.get("HIKVISION_RTSP_PATH", "/Streaming/Channels/101")
SNAPSHOT_PATH = os.environ.get("HIKVISION_SNAPSHOT_PATH", "/ISAPI/Streaming/channels/101/picture")

# Simple RTSP over TCP client to fetch H264 stream
class RTSPClient(threading.Thread):
    def __init__(self, ip, port, username, password, path):
        super().__init__()
        self.daemon = True
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.path = path
        self.session = None
        self.seq = 1
        self.cseq_lock = threading.Lock()
        self.running = threading.Event()
        self.running.set()
        self.sock = None
        self.rtp_queue = queue.Queue(maxsize=100)
        self.track_url = None

    def send_rtsp(self, conn, request):
        with self.cseq_lock:
            lines = request.strip().split('\n')
            req = lines[0]
            headers = lines[1:]
            msg = req + '\r\n'
            for h in headers:
                msg += h.strip() + '\r\n'
            msg += '\r\n'
            conn.sendall(msg.encode())

    def recv_rtsp(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode(errors="ignore")

    def parse_sdp(self, sdp):
        for line in sdp.splitlines():
            if line.startswith("a=control:"):
                track = line.split(":", 1)[1]
                if track.startswith("rtsp://"):
                    self.track_url = track
                else:
                    self.track_url = f"rtsp://{self.ip}:{self.port}{self.path}/{track}"
                break

    def run(self):
        try:
            conn = socket.create_connection((self.ip, self.port), timeout=8)
            self.sock = conn
            base64_auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            # 1. OPTIONS
            self.send_rtsp(conn, f"OPTIONS rtsp://{self.ip}:{self.port}{self.path} RTSP/1.0\nCSeq: {self.seq}\nAuthorization: Basic {base64_auth}")
            self.seq += 1
            self.recv_rtsp(conn)
            # 2. DESCRIBE
            self.send_rtsp(conn, f"DESCRIBE rtsp://{self.ip}:{self.port}{self.path} RTSP/1.0\nCSeq: {self.seq}\nAccept: application/sdp\nAuthorization: Basic {base64_auth}")
            self.seq += 1
            reply = self.recv_rtsp(conn)
            sdp = reply.split("\r\n\r\n")[-1]
            self.parse_sdp(sdp)
            if not self.track_url:
                return
            # 3. SETUP (TCP interleaved RTP/RTCP)
            self.send_rtsp(conn, f"SETUP {self.track_url} RTSP/1.0\nCSeq: {self.seq}\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\nAuthorization: Basic {base64_auth}")
            self.seq += 1
            reply = self.recv_rtsp(conn)
            for line in reply.splitlines():
                if line.lower().startswith("session:"):
                    self.session = line.split(":", 1)[1].split(";")[0].strip()
                    break
            # 4. PLAY
            self.send_rtsp(conn, f"PLAY {self.track_url} RTSP/1.0\nCSeq: {self.seq}\nSession: {self.session}\nRange: npt=0.000-\nAuthorization: Basic {base64_auth}")
            self.seq += 1
            self.recv_rtsp(conn)
            # 5. Read RTP over RTSP (interleaved TCP)
            while self.running.is_set():
                # RTSP interleaved frames: $ <channel> <16-bit len> <payload>
                header = conn.recv(4)
                if len(header) < 4 or header[0] != 36:  # ord('$') == 36
                    continue
                channel, length = header[1], struct.unpack(">H", header[2:4])[0]
                payload = b''
                while len(payload) < length:
                    chunk = conn.recv(length - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                if channel == 0:  # RTP
                    try:
                        self.rtp_queue.put(payload, timeout=0.5)
                    except queue.Full:
                        continue
        except Exception:
            pass

    def stop(self):
        self.running.clear()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def read_h264(self):
        # H264 in RTP: parse, extract NALUs, yield as Annex B
        sps_pps = []
        while self.running.is_set():
            try:
                pkt = self.rtp_queue.get(timeout=1)
            except queue.Empty:
                continue
            if len(pkt) < 12:
                continue
            header = pkt[:12]
            payload = pkt[12:]
            if not payload:
                continue
            nalu_type = payload[0] & 0x1F
            if 1 <= nalu_type <= 23:
                nalu = b'\x00\x00\x00\x01' + payload
                if nalu_type in (7, 8):  # SPS, PPS
                    sps_pps.append(nalu)
                yield nalu
            elif nalu_type == 28:  # FU-A
                fu_header = payload[1]
                start_bit = fu_header >> 7
                end_bit = (fu_header >> 6) & 1
                nalu_hdr = (payload[0] & 0xE0) | (fu_header & 0x1F)
                if start_bit:
                    nalu = b'\x00\x00\x00\x01' + bytes([nalu_hdr]) + payload[2:]
                    if (nalu_hdr & 0x1F) in (7, 8):  # SPS, PPS
                        sps_pps.append(nalu)
                    yield nalu
                else:
                    yield payload[2:]
        # If asked, return SPS/PPS for new clients
        if sps_pps:
            for nalu in sps_pps:
                yield nalu

# HTTP Handler
class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):
    rtsp_client = None
    rtsp_client_lock = threading.Lock()
    clients = set()
    clients_lock = threading.Lock()

    def _parse_auth(self):
        if DEVICE_USERNAME and DEVICE_PASSWORD:
            return (DEVICE_USERNAME, DEVICE_PASSWORD)
        return (None, None)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/play":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            # Start RTSP client if not running
            with self.rtsp_client_lock:
                if not self.rtsp_client or not self.rtsp_client.is_alive():
                    self.rtsp_client = RTSPClient(DEVICE_IP, DEVICE_RTSP_PORT, DEVICE_USERNAME, DEVICE_PASSWORD, RTSP_PATH)
                    self.rtsp_client.start()
            self.wfile.write(b'{"status":"ok","message":"Video stream started. Use /video to view."}')
        elif parsed.path == "/halt":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with self.rtsp_client_lock:
                if self.rtsp_client:
                    self.rtsp_client.stop()
                    self.rtsp_client = None
            self.wfile.write(b'{"status":"ok","message":"Video stream stopped."}')
        elif parsed.path == "/snap":
            # POST /snap triggers a capture and returns image
            img = self._fetch_snapshot()
            if img:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(img)
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Failed to get snapshot")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/video":
            self._handle_video_stream()
        elif parsed.path == "/snap":
            img = self._fetch_snapshot()
            if img:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(img)
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Failed to get snapshot")
        else:
            self.send_response(404)
            self.end_headers()

    def _fetch_snapshot(self):
        # GET JPEG snapshot from camera HTTP API
        try:
            import http.client
            conn = http.client.HTTPConnection(DEVICE_IP, 80, timeout=6)
            auth = f"{DEVICE_USERNAME}:{DEVICE_PASSWORD}"
            headers = {
                "Authorization": "Basic " + base64.b64encode(auth.encode()).decode()
            }
            conn.request("GET", SNAPSHOT_PATH, headers=headers)
            resp = conn.getresponse()
            if resp.status == 200:
                return resp.read()
        except Exception:
            return None
        return None

    def _handle_video_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        # Start RTSP client if not running
        with self.rtsp_client_lock:
            if not self.rtsp_client or not self.rtsp_client.is_alive():
                self.rtsp_client = RTSPClient(DEVICE_IP, DEVICE_RTSP_PORT, DEVICE_USERNAME, DEVICE_PASSWORD, RTSP_PATH)
                self.rtsp_client.start()
        # Wait for RTSP client to be ready
        time.sleep(1)
        # Send H264 stream as Annex B NALUs
        try:
            for nalu in self.rtsp_client.read_h264():
                self.wfile.write(nalu)
                self.wfile.flush()
        except Exception:
            pass

    def log_message(self, format, *args):
        # Silence logs
        pass

def run():
    server = HTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHTTPRequestHandler)
    print(f"Hikvision IP Camera HTTP Driver running at http://{SERVER_HOST}:{SERVER_PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with HikvisionHTTPRequestHandler.rtsp_client_lock:
            if HikvisionHTTPRequestHandler.rtsp_client:
                HikvisionHTTPRequestHandler.rtsp_client.stop()
    server.server_close()

if __name__ == "__main__":
    run()