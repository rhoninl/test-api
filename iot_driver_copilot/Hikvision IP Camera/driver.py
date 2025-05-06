import os
import io
import threading
import time
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import socket
import struct

import requests

# ---- Environment Variables ----
CAMERA_IP = os.environ.get('HIKVISION_IP', '192.168.1.64')
CAMERA_RTSP_PORT = int(os.environ.get('HIKVISION_RTSP_PORT', '554'))
CAMERA_HTTP_PORT = int(os.environ.get('HIKVISION_HTTP_PORT', '80'))
CAMERA_USER = os.environ.get('HIKVISION_USER', 'admin')
CAMERA_PASS = os.environ.get('HIKVISION_PASS', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_PATH = os.environ.get('HIKVISION_RTSP_PATH', '/Streaming/Channels/101')
SNAPSHOT_PATH = os.environ.get('HIKVISION_SNAPSHOT_PATH', '/ISAPI/Streaming/channels/101/picture')
RTSP_URL = f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{RTSP_PATH}'
SNAPSHOT_URL = f'http://{CAMERA_IP}:{CAMERA_HTTP_PORT}{SNAPSHOT_PATH}'

# ---- Streaming Session Control ----
streaming_session = {
    'active': False,
    'lock': threading.Lock()
}

# ---- RTSP/RTP Packet Parsing ----
class RTSPClient:
    def __init__(self, url):
        self.url = url
        self.rtsp_socket = None
        self.session = None
        self.track_id = None
        self.server_port = None
        self.client_port = None
        self.rtp_socket = None
        self.seq = 1
        self.state = 'INIT'
        self.base_url = url[:url.find('/', url.find('//') + 2)]
        self.stream_url = url
        self.stopped = threading.Event()
        self.last_rtp_timestamp = None

    def _send_rtsp(self, cmd, headers=None, extra=''):
        req = f"{cmd} {self.stream_url} RTSP/1.0\r\nCSeq: {self.seq}\r\n"
        if self.session:
            req += f"Session: {self.session}\r\n"
        if headers:
            req += headers
        req += "\r\n"
        if extra:
            req += extra
        self.rtsp_socket.send(req.encode())
        self.seq += 1
        resp = b''
        while True:
            chunk = self.rtsp_socket.recv(4096)
            resp += chunk
            if b'\r\n\r\n' in resp:
                break
        return resp.decode(errors='ignore')

    def connect(self):
        urlinfo = urlparse(self.url)
        self.rtsp_socket = socket.create_connection((urlinfo.hostname, urlinfo.port or 554))
        return self.options()

    def options(self):
        resp = self._send_rtsp("OPTIONS")
        return resp

    def describe(self):
        resp = self._send_rtsp("DESCRIBE", "Accept: application/sdp\r\n")
        # Find trackID in SDP
        sdp = resp.split('\r\n\r\n', 1)[-1]
        lines = sdp.split('\n')
        for l in lines:
            if 'trackID' in l:
                self.track_id = l.strip().split('trackID=')[-1].split()[0]
                break
        return resp

    def setup(self, client_port=50000):
        self.client_port = client_port
        track_url = f"{self.base_url}{RTSP_PATH}/trackID=1"
        setup_hdr = f"Transport: RTP/UDP;unicast;client_port={client_port}-{client_port+1}\r\n"
        req = f"SETUP {track_url} RTSP/1.0\r\nCSeq: {self.seq}\r\n{setup_hdr}\r\n"
        self.rtsp_socket.send(req.encode())
        self.seq += 1
        resp = b''
        while True:
            chunk = self.rtsp_socket.recv(4096)
            resp += chunk
            if b'\r\n\r\n' in resp:
                break
        response = resp.decode(errors='ignore')
        for line in response.split('\r\n'):
            if line.lower().startswith('session:'):
                self.session = line.split(':',1)[1].split(';')[0].strip()
            if 'server_port' in line:
                parts = line.split('server_port=')[-1].split('-')
                self.server_port = int(parts[0])
        return response

    def play(self):
        resp = self._send_rtsp("PLAY")
        return resp

    def teardown(self):
        try:
            self._send_rtsp("TEARDOWN")
        except Exception:
            pass
        try:
            self.rtsp_socket.close()
        except Exception:
            pass
        self.stopped.set()

    def start_rtp(self):
        self.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp_socket.bind(('', self.client_port))
        self.rtp_socket.settimeout(1.0)
        return self.rtp_socket

    def receive_h264(self):
        # Returns generator yielding H264 NAL units
        self.connect()
        self.describe()
        self.setup()
        self.play()
        self.start_rtp()
        try:
            while not self.stopped.is_set():
                try:
                    packet, _ = self.rtp_socket.recvfrom(2048)
                except socket.timeout:
                    continue
                if len(packet) < 12:
                    continue
                # RTP Header = 12 bytes
                payload_type = packet[1] & 0x7F
                if payload_type != 96:  # dynamic H264
                    continue
                # RTP header parsing
                marker = (packet[1] & 0x80) >> 7
                seqnum = struct.unpack('!H', packet[2:4])[0]
                timestamp = struct.unpack('!I', packet[4:8])[0]
                payload = packet[12:]
                # H264 NALU parsing
                if len(payload) < 1:
                    continue
                nal_type = payload[0] & 0x1F
                # Single NALU
                if 1 <= nal_type <= 23:
                    yield b'\x00\x00\x00\x01' + payload
                # FU-A Fragmented NALU
                elif nal_type == 28:
                    fu_header = payload[1]
                    start_bit = (fu_header & 0x80) >> 7
                    end_bit = (fu_header & 0x40) >> 6
                    nal = (payload[0] & 0xE0) | (fu_header & 0x1F)
                    if start_bit:
                        frag = b'\x00\x00\x00\x01' + bytes([nal]) + payload[2:]
                    else:
                        frag = payload[2:]
                    yield frag
                # else: skip
        finally:
            self.teardown()
            if self.rtp_socket:
                self.rtp_socket.close()

# ---- HTTP Handler ----
class HikvisionHTTPRequestHandler(BaseHTTPRequestHandler):

    protocol_version = 'HTTP/1.1'

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Allow', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def do_POST(self):
        if self.path == '/play':
            with streaming_session['lock']:
                if not streaming_session['active']:
                    streaming_session['active'] = True
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"status":"playing","video_url":"/video"}')

        elif self.path == '/halt':
            with streaming_session['lock']:
                streaming_session['active'] = False
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"status":"stopped"}')

        elif self.path == '/snap':
            # Equivalent to GET /snap, for compatibility
            self.handle_snapshot()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == '/snap':
            self.handle_snapshot()
        elif self.path == '/video':
            self.handle_video_stream()
        else:
            self.send_error(404)

    def handle_snapshot(self):
        try:
            resp = requests.get(SNAPSHOT_URL, auth=(CAMERA_USER, CAMERA_PASS), timeout=8, stream=True)
            if resp.status_code == 200:
                img_bytes = resp.content
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(img_bytes)))
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(img_bytes)
            else:
                self.send_error(502, "Failed to fetch snapshot")
        except Exception as e:
            self.send_error(502, str(e))

    def handle_video_stream(self):
        with streaming_session['lock']:
            if not streaming_session['active']:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"error":"stream not active. POST /play first."}')
                return
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')  # Let browser handle it with extension or compatible player
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        client = RTSPClient(RTSP_URL)
        try:
            for nalu in client.receive_h264():
                if not streaming_session['active']:
                    break
                # H.264 Annex B stream to browser/ffmpeg/mpv/vlc, etc.
                self.wfile.write(nalu)
                self.wfile.flush()
        except BrokenPipeError:
            pass
        except Exception:
            pass
        finally:
            client.teardown()
            with streaming_session['lock']:
                streaming_session['active'] = False

    def log_message(self, format, *args):
        # Suppress console logs
        return

# ---- HTTP Server ----
def run_server():
    httpd = HTTPServer((SERVER_HOST, SERVER_PORT), HikvisionHTTPRequestHandler)
    print(f"Hikvision driver HTTP server running at http://{SERVER_HOST}:{SERVER_PORT}")
    httpd.serve_forever()

if __name__ == '__main__':
    run_server()