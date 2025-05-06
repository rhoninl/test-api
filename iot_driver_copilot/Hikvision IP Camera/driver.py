import os
import threading
import queue
import base64
from flask import Flask, Response, request, jsonify

import socket
import re
import time

# ========== Environment Configuration ==========
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", "admin")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "/Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"

# ========== RTSP Client Implementation ==========

class RTSPClient(threading.Thread):
    def __init__(self, url, queue_size=100):
        super().__init__(daemon=True)
        self.url = url
        self.running = threading.Event()
        self.frame_queue = queue.Queue(maxsize=queue_size)
        self.session = None
        self.transport = None
        self.rtsp_socket = None
        self.rtp_socket = None
        self.seq = 1
        self.ssrc = None
        self.track_id = None
        self.cseq = 1
        self.control_url = None
        self.rtp_port = None
        self.client_ports = self._get_available_ports()
        self._parse_url()
        self.interleaved = False

    def _parse_url(self):
        m = re.match(r"rtsp://([^:]+):([^@]+)@([^:/]+):?(\d*)(/.*)", self.url)
        if not m:
            raise ValueError("Invalid RTSP URL")
        self.username, self.password, self.host, port, self.path = m.groups()
        self.port = int(port) if port else 554

    def _get_available_ports(self):
        # Get two available UDP ports for RTP/RTCP
        import random
        while True:
            p1 = random.randrange(30000, 40000, 2)
            p2 = p1 + 1
            try:
                s1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s1.bind(('', p1))
                s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s2.bind(('', p2))
                s1.close()
                s2.close()
                return (p1, p2)
            except Exception:
                continue

    def _build_auth_header(self, method, uri, www_authenticate):
        # Basic authentication only (Digest not handled for simplicity)
        if "Basic" in www_authenticate:
            userpass = f"{self.username}:{self.password}"
            encoded = base64.b64encode(userpass.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        return {}

    def _send_rtsp(self, req, headers=None):
        if not self.rtsp_socket:
            self.rtsp_socket = socket.create_connection((self.host, self.port), timeout=5)
        if headers is None:
            headers = {}
        headers['CSeq'] = str(self.cseq)
        self.cseq += 1
        header_str = ''.join(f"{k}: {v}\r\n" for k, v in headers.items())
        data = f"{req}\r\n{header_str}\r\n"
        self.rtsp_socket.sendall(data.encode())
        return self._recv_rtsp()

    def _recv_rtsp(self):
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.rtsp_socket.recv(4096)
            if not chunk:
                break
            resp += chunk
        headers, _, body = resp.partition(b"\r\n\r\n")
        code = int(headers.decode().split(' ')[1])
        header_lines = headers.decode().split('\r\n')[1:]
        hdict = {}
        for line in header_lines:
            if ': ' in line:
                k, v = line.split(': ', 1)
                hdict[k.strip()] = v.strip()
        return code, hdict, body

    def _recv_rtp(self):
        # Receive RTP on UDP
        self.rtp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp_socket.bind(('', self.client_ports[0]))
        self.rtp_socket.settimeout(1.0)
        while self.running.is_set():
            try:
                data, addr = self.rtp_socket.recvfrom(65536)
                if data and self.frame_queue.qsize() < self.frame_queue.maxsize:
                    self.frame_queue.put(data)
            except socket.timeout:
                continue
            except Exception:
                break

    def _recv_rtp_interleaved(self):
        # RTP over RTSP TCP interleaved
        self.rtsp_socket.settimeout(1.0)
        while self.running.is_set():
            try:
                b = self.rtsp_socket.recv(4096)
                if not b:
                    break
                # Interleaved RTP data starts with $ <channel> <len_hi> <len_lo>
                i = 0
                while i < len(b):
                    if b[i] == 0x24:  # $
                        channel = b[i + 1]
                        length = (b[i + 2] << 8) | b[i + 3]
                        payload = b[i + 4: i + 4 + length]
                        i += 4 + length
                        if channel == 0:  # RTP
                            if self.frame_queue.qsize() < self.frame_queue.maxsize:
                                self.frame_queue.put(payload)
                    else:
                        i += 1
            except Exception:
                break

    def run(self):
        self.running.set()
        try:
            # 1. OPTIONS
            code, headers, body = self._send_rtsp(f"OPTIONS rtsp://{self.host}{self.path} RTSP/1.0")
            # 2. DESCRIBE (to get SDP)
            code, headers, body = self._send_rtsp(
                f"DESCRIBE rtsp://{self.host}{self.path} RTSP/1.0",
                {"Accept": "application/sdp"}
            )
            sdp = body.decode(errors="ignore")
            # Find trackID
            m = re.findall(r"a=control:(.*)", sdp)
            if m:
                self.control_url = m[0]
                if not self.control_url.startswith("rtsp://"):
                    self.control_url = f"rtsp://{self.host}{self.path}/{self.control_url}"
            else:
                self.control_url = f"rtsp://{self.host}{self.path}"
            # 3. SETUP (UDP or TCP)
            transport = f"RTP/AVP;unicast;client_port={self.client_ports[0]}-{self.client_ports[1]}"
            code, headers, body = self._send_rtsp(
                f"SETUP {self.control_url} RTSP/1.0",
                {"Transport": transport}
            )
            if code == 461 or "interleaved" in headers.get("Transport", ""):
                # Fallback to TCP interleaved
                self.interleaved = True
                code, headers, body = self._send_rtsp(
                    f"SETUP {self.control_url} RTSP/1.0",
                    {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}
                )
            self.session = headers.get("Session", "").split(";")[0]
            # 4. PLAY
            code, headers, body = self._send_rtsp(
                f"PLAY {self.control_url} RTSP/1.0",
                {"Session": self.session}
            )
            # 5. RTP Receiving loop
            if self.interleaved:
                self._recv_rtp_interleaved()
            else:
                self._recv_rtp()
        except Exception as e:
            pass  # Log error in production
        finally:
            self.running.clear()
            if self.rtsp_socket:
                try:
                    self.rtsp_socket.close()
                except Exception:
                    pass
            if self.rtp_socket:
                try:
                    self.rtp_socket.close()
                except Exception:
                    pass

    def stop(self):
        self.running.clear()
        # Send TEARDOWN
        try:
            if self.session:
                self._send_rtsp(f"TEARDOWN {self.control_url} RTSP/1.0", {"Session": self.session})
        except Exception:
            pass

    def get_frame(self, timeout=1):
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

# ========== H264 to HTTP MJPEG/RAW Proxy ==========

class H264HTTPProxy:
    def __init__(self):
        self.rtsp_client = None
        self.lock = threading.Lock()

    def start_stream(self):
        with self.lock:
            if self.rtsp_client is None or not self.rtsp_client.running.is_set():
                self.rtsp_client = RTSPClient(RTSP_URL)
                self.rtsp_client.start()
                # Wait a bit for buffer to fill
                time.sleep(2)
                return True
        return False

    def stop_stream(self):
        with self.lock:
            if self.rtsp_client and self.rtsp_client.running.is_set():
                self.rtsp_client.stop()
                self.rtsp_client = None
                return True
        return False

    def is_streaming(self):
        return self.rtsp_client is not None and self.rtsp_client.running.is_set()

    def stream_generator(self):
        """
        This will output raw H264 stream as HTTP chunked (video/h264 content-type).
        """
        if not self.is_streaming():
            self.start_stream()
        while self.is_streaming():
            frame = self.rtsp_client.get_frame(timeout=2)
            if frame:
                yield frame
            else:
                time.sleep(0.01)

# ========== Flask HTTP Server ==========

app = Flask(__name__)
proxy = H264HTTPProxy()

@app.route("/start", methods=["POST"])
@app.route("/video/start", methods=["POST"])
def start_stream():
    if proxy.start_stream():
        return jsonify({"status": "started"}), 200
    return jsonify({"status": "already running"}), 200

@app.route("/stop", methods=["POST"])
@app.route("/video/stop", methods=["POST"])
def stop_stream():
    if proxy.stop_stream():
        return jsonify({"status": "stopped"}), 200
    return jsonify({"status": "already stopped"}), 200

@app.route("/stream", methods=["GET"])
def stream_video():
    if not proxy.is_streaming():
        proxy.start_stream()
    return Response(
        proxy.stream_generator(),
        mimetype="video/h264"
    )

@app.route("/")
def root():
    return '''
    <html>
    <body>
        <h2>Hikvision IP Camera Stream Proxy</h2>
        <video id="v" width="640" height="480" controls autoplay></video>
        <script>
        var video = document.getElementById("v");
        // A browser can't natively play video/h264, but this is a placeholder.
        // Use ffmpeg, GStreamer, or an HLS/MPEG-DASH player for production.
        video.src = "/stream";
        </script>
        <p>Start stream: <code>curl -X POST http://{host}:{port}/start</code></p>
        <p>Stop stream: <code>curl -X POST http://{host}:{port}/stop</code></p>
        <p>Watch: <code>curl http://{host}:{port}/stream --output camera.h264</code> or open <a href="/stream">/stream</a> in a compatible player.</p>
    </body>
    </html>
    '''.format(host=SERVER_HOST, port=SERVER_PORT)

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)