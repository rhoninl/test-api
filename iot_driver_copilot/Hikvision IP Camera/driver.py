import os
import threading
import socket
import struct
from http.server import BaseHTTPRequestHandler, HTTPServer
import base64

# Minimal RTSP client and RTP/H264 parser for proxying to HTTP

class RTSPClient:
    def __init__(self, host, port, username, password, stream_path):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.stream_path = stream_path
        self.rtsp_socket = None
        self.session = None
        self.seq = 1
        self.transport_port = None
        self.client_rtp_socket = None
        self.running = False
        self.sps = None
        self.pps = None

    def _send(self, data):
        self.rtsp_socket.sendall(data.encode())

    def _recv(self):
        data = b""
        while True:
            try:
                chunk = self.rtsp_socket.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b'\r\n\r\n' in data:
                    break
            except socket.timeout:
                break
        return data.decode(errors="ignore")

    def _auth_header(self):
        if self.username and self.password:
            user_pass = f"{self.username}:{self.password}"
            return "Authorization: Basic " + base64.b64encode(user_pass.encode()).decode()
        return None

    def connect(self):
        self.rtsp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.rtsp_socket.settimeout(5)
        self.rtsp_socket.connect((self.host, self.port))

    def options(self):
        req = f"OPTIONS {self.stream_path} RTSP/1.0\r\nCSeq: {self.seq}\r\n"
        auth = self._auth_header()
        if auth:
            req += auth + "\r\n"
        req += "\r\n"
        self._send(req)
        self._recv()
        self.seq += 1

    def describe(self):
        req = f"DESCRIBE {self.stream_path} RTSP/1.0\r\nCSeq: {self.seq}\r\nAccept: application/sdp\r\n"
        auth = self._auth_header()
        if auth:
            req += auth + "\r\n"
        req += "\r\n"
        self._send(req)
        resp = self._recv()
        self.seq += 1
        return resp

    def setup(self, client_port):
        self.transport_port = client_port
        req = (f"SETUP {self.stream_path}/trackID=1 RTSP/1.0\r\n"
               f"CSeq: {self.seq}\r\n"
               f"Transport: RTP/AVP;unicast;client_port={client_port}-{client_port+1}\r\n")
        auth = self._auth_header()
        if auth:
            req += auth + "\r\n"
        req += "\r\n"
        self._send(req)
        resp = self._recv()
        self.seq += 1
        lines = resp.splitlines()
        for line in lines:
            if line.startswith("Session:"):
                self.session = line.split(":",1)[1].strip().split(";")[0]
        return resp

    def play(self):
        req = (f"PLAY {self.stream_path} RTSP/1.0\r\n"
               f"CSeq: {self.seq}\r\n"
               f"Session: {self.session}\r\n")
        auth = self._auth_header()
        if auth:
            req += auth + "\r\n"
        req += "\r\n"
        self._send(req)
        self._recv()
        self.seq += 1

    def teardown(self):
        if self.session:
            req = (f"TEARDOWN {self.stream_path} RTSP/1.0\r\n"
                   f"CSeq: {self.seq}\r\n"
                   f"Session: {self.session}\r\n")
            auth = self._auth_header()
            if auth:
                req += auth + "\r\n"
            req += "\r\n"
            try:
                self._send(req)
                self._recv()
            except Exception:
                pass

    def close(self):
        try:
            self.teardown()
        except Exception:
            pass
        try:
            if self.rtsp_socket:
                self.rtsp_socket.close()
        except Exception:
            pass
        try:
            if self.client_rtp_socket:
                self.client_rtp_socket.close()
        except Exception:
            pass

    def open_rtp_port(self):
        self.client_rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client_rtp_socket.bind(('', self.transport_port))
        self.client_rtp_socket.settimeout(1.0)

    def get_sps_pps(self, sdp):
        lines = sdp.splitlines()
        for line in lines:
            if line.startswith("a=fmtp:"):
                parts = line.split(" ")
                for part in parts:
                    if part.startswith("sprop-parameter-sets="):
                        sprops = part.split("=")[1]
                        spsb64, ppsb64 = sprops.split(",")
                        self.sps = base64.b64decode(spsb64)
                        self.pps = base64.b64decode(ppsb64)

    def read_rtp(self):
        try:
            data, _ = self.client_rtp_socket.recvfrom(4096)
            if len(data) < 12:
                return None
            rtp_header = data[:12]
            payload = data[12:]
            # H264 payload: Remove 2-byte header if present (NALU fragmentation)
            if payload and (payload[0] & 0x1F) in (28, 29):  # FU-A or FU-B
                fu_indicator = payload[0]
                fu_header = payload[1]
                start_bit = (fu_header & 0x80) >> 7
                nal_type = fu_header & 0x1F
                nalu_header = (fu_indicator & 0xE0) | nal_type
                if start_bit:
                    # Start of fragmented NALU
                    payload = b'\x00\x00\x00\x01' + bytes([nalu_header]) + payload[2:]
                else:
                    payload = payload[2:]
            else:
                payload = b'\x00\x00\x00\x01' + payload
            return payload
        except socket.timeout:
            return None
        except Exception:
            return None

class HikvisionStreamHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path != '/stream':
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()

        # RTSP connection info
        rtsp_host = os.environ.get('DEVICE_IP', '127.0.0.1')
        rtsp_port = int(os.environ.get('RTSP_PORT', '554'))
        rtsp_user = os.environ.get('DEVICE_USER', '')
        rtsp_pass = os.environ.get('DEVICE_PASS', '')
        rtsp_path = os.environ.get('RTSP_STREAM_PATH', '/Streaming/Channels/101')
        rtp_port = int(os.environ.get('RTP_CLIENT_PORT', '50000'))

        # Use fMP4 muxer for progressive streaming
        try:
            import av
            AV_AVAILABLE = True
        except ImportError:
            AV_AVAILABLE = False

        rtsp_url = f"rtsp://{rtsp_host}:{rtsp_port}{rtsp_path}"

        rtsp = RTSPClient(rtsp_host, rtsp_port, rtsp_user, rtsp_pass, rtsp_path)
        try:
            rtsp.connect()
            rtsp.options()
            sdp = rtsp.describe()
            rtsp.get_sps_pps(sdp)
            rtsp.setup(rtp_port)
            rtsp.open_rtp_port()
            rtsp.play()
        except Exception as e:
            self.wfile.write(f"RTSP connection error: {e}".encode())
            return

        # MP4 init segment with SPS/PPS
        # Use PyAV if available (recommended), else emit raw H264 as fallback
        if AV_AVAILABLE:
            import io
            output = io.BytesIO()
            container = av.open(output, mode='w', format='mp4')
            stream = container.add_stream('h264', rate=25)
            if rtsp.sps:
                stream.codec_context.extradata = b''.join([
                    b'\x00\x00\x00\x01' + rtsp.sps,
                    b'\x00\x00\x00\x01' + rtsp.pps
                ])
            container.write_header()
            self.wfile.write(output.getvalue())
            output.seek(0)
            output.truncate(0)

        else:
            # Fallback: just output AnnexB H264 stream (can be played in ffplay/vlc)
            if rtsp.sps:
                self.wfile.write(b'\x00\x00\x00\x01' + rtsp.sps)
            if rtsp.pps:
                self.wfile.write(b'\x00\x00\x00\x01' + rtsp.pps)

        try:
            while True:
                nalu = rtsp.read_rtp()
                if not nalu:
                    continue
                if AV_AVAILABLE:
                    packet = av.packet.Packet(nalu)
                    container.mux_one(packet)
                    data = output.getvalue()
                    if data:
                        self.wfile.write(data)
                        output.seek(0)
                        output.truncate(0)
                else:
                    self.wfile.write(nalu)
        except Exception:
            pass
        finally:
            rtsp.close()
            try:
                if AV_AVAILABLE:
                    container.close()
            except Exception:
                pass

    def log_message(self, format, *args):
        return

def run():
    http_host = os.environ.get('SERVER_HOST', '0.0.0.0')
    http_port = int(os.environ.get('SERVER_PORT', '8000'))
    server = HTTPServer((http_host, http_port), HikvisionStreamHandler)
    print(f"HTTP server running on {http_host}:{http_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    run()