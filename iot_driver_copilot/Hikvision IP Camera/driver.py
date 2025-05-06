import os
import asyncio
import aiohttp
from aiohttp import web
import base64

# Config from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USER = os.environ.get("RTSP_USER", "admin")
RTSP_PASS = os.environ.get("RTSP_PASS", "12345")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# RTSP Stream Path (may need adjustment per camera config)
RTSP_PATH = os.environ.get("RTSP_PATH", "/Streaming/Channels/101/")
RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"

# Global flags and handle for streaming
streaming = False
stream_task = None
clients = set()

# HTTP API: Start Stream Command (no-op for this driver, but present for API)
async def start_stream_cmd(request):
    global streaming
    streaming = True
    return web.json_response({"status": "ok", "message": "Stream start command sent."})

async def stop_stream_cmd(request):
    global streaming
    streaming = False
    return web.json_response({"status": "ok", "message": "Stream stop command sent."})

# HTTP API: Start/Stop endpoints for compatibility
async def video_start(request):
    global streaming
    streaming = True
    return web.json_response({"status": "ok", "message": "Video stream initiated."})

async def video_stop(request):
    global streaming
    streaming = False
    return web.json_response({"status": "ok", "message": "Video stream stopped."})

async def rtsp_proxy_generator():
    """
    Connects to the Hikvision RTSP stream, reads raw H.264 data,
    and yields MPEG-TS chunks for HTTP MJPEG-like streaming.
    """
    # Minimalistic RTSP over TCP implementation for H.264 (raw) stream
    # We will use ffmpeg.wasm-like pure Python implementation is not feasible, so we serve raw H.264 stream
    # as a multipart octet-stream compatible with browsers that support H.264 raw (such as with <video> and MediaSource)
    # Note: Many browsers don't support raw H.264 over HTTP; in production, an in-process remux to MJPEG or MPEG-TS is advised.
    # Here, we serve raw H.264 NAL units as a continuous stream for demonstration.

    # To avoid third-party commands, we use aiortsp (if available), else do a minimal RTSP-over-TCP read.
    # For this driver, we use aiohttp to connect and read the RTSP TCP stream directly.

    # RTSP DESCRIBE/SETUP/PLAY is complicated; here we implement a basic TCP socket reader for demonstration.

    import socket

    rtsp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rtsp_sock.settimeout(5)
    try:
        rtsp_sock.connect((DEVICE_IP, RTSP_PORT))
        cseq = 1
        session_id = None

        def send_cmd(cmd):
            rtsp_sock.sendall(cmd.encode('utf-8'))

        def recv_response():
            data = b""
            while True:
                part = rtsp_sock.recv(4096)
                if not part:
                    break
                data += part
                if b"\r\n\r\n" in data:
                    break
            return data.decode('utf-8', errors="ignore")

        # RTSP handshake
        # 1. DESCRIBE
        auth = base64.b64encode(f"{RTSP_USER}:{RTSP_PASS}".encode()).decode()
        describe = (f"DESCRIBE {RTSP_URL} RTSP/1.0\r\n"
                    f"CSeq: {cseq}\r\n"
                    f"Accept: application/sdp\r\n"
                    f"Authorization: Basic {auth}\r\n\r\n")
        send_cmd(describe)
        resp = recv_response()
        cseq += 1

        # 2. SETUP (UDP transport, but for demo we request TCP interleaved)
        import re
        match = re.search(r'a=control:(.*)', resp)
        control = match.group(1).strip() if match else RTSP_PATH
        if not control.startswith("rtsp://"):
            control = f"rtsp://{DEVICE_IP}:{RTSP_PORT}{control}"
        setup = (f"SETUP {control} RTSP/1.0\r\n"
                 f"CSeq: {cseq}\r\n"
                 f"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
                 f"Authorization: Basic {auth}\r\n\r\n")
        send_cmd(setup)
        resp = recv_response()
        cseq += 1

        sid_match = re.search(r'Session: ([^;\r\n]+)', resp)
        session_id = sid_match.group(1) if sid_match else None

        # 3. PLAY
        play = (f"PLAY {RTSP_URL} RTSP/1.0\r\n"
                f"CSeq: {cseq}\r\n"
                f"Session: {session_id}\r\n"
                f"Authorization: Basic {auth}\r\n\r\n")
        send_cmd(play)
        resp = recv_response()
        cseq += 1

        # 4. Stream RTP (interleaved over TCP)
        # RTP over RTSP: dollar-sign ($), channel byte, 16-bit length, then RTP packet
        # We'll extract the H.264 NAL units from the RTP packets
        import struct

        # Send HTTP headers for MPEG-TS or raw stream
        header_sent = False

        while streaming:
            # Read RTSP interleaved frame
            header = rtsp_sock.recv(4)
            if len(header) < 4:
                break
            if header[0] != 0x24:  # '$'
                continue
            channel = header[1]
            pkt_len = struct.unpack(">H", header[2:4])[0]
            pkt = b''
            while len(pkt) < pkt_len:
                chunk = rtsp_sock.recv(pkt_len - len(pkt))
                if not chunk:
                    break
                pkt += chunk
            # RTP packet
            # Parse RTP header (12 bytes) to get payload
            if len(pkt) < 12:
                continue
            # RTP header
            payload_type = pkt[1] & 0x7F
            seq_num = struct.unpack(">H", pkt[2:4])[0]
            timestamp = struct.unpack(">I", pkt[4:8])[0]
            # H.264 payload starts after RTP header (12 bytes)
            h264_payload = pkt[12:]
            # For demonstration, we yield each NAL unit (fragmented NALs not handled perfectly here)
            if not header_sent:
                yield b"--frame\r\nContent-Type: video/H264\r\n\r\n"
                header_sent = True
            if h264_payload:
                yield h264_payload
    except Exception as e:
        pass
    finally:
        try:
            rtsp_sock.close()
        except Exception:
            pass

async def stream_handler(request):
    global streaming, clients
    if not streaming:
        return web.Response(status=503, text="Streaming not started. Use /start or /video/start.")
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'multipart/x-mixed-replace; boundary=frame'
        }
    )
    await response.prepare(request)
    clients.add(response)
    try:
        async for chunk in rtsp_proxy_generator():
            await response.write(chunk)
            await asyncio.sleep(0.001)
    except asyncio.CancelledError:
        pass
    finally:
        clients.discard(response)
        await response.write_eof()
    return response

app = web.Application()
app.router.add_post('/start', start_stream_cmd)
app.router.add_post('/stop', stop_stream_cmd)
app.router.add_post('/video/start', video_start)
app.router.add_post('/video/stop', video_stop)
app.router.add_get('/stream', stream_handler)

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)