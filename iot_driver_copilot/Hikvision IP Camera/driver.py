import os
import asyncio
import threading
import base64
import struct
from typing import Optional

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware

import aiohttp

# ========== Environment Variables ==========
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
DEVICE_USERNAME = os.environ.get("DEVICE_USERNAME", "admin")
DEVICE_PASSWORD = os.environ.get("DEVICE_PASSWORD", "12345")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_PATH = os.environ.get("RTSP_PATH", "/Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

# ========== RTSP Client Implementation ==========

class RTSPClient:
    def __init__(self, url: str, username: str = "", password: str = ""):
        self.url = url
        self.username = username
        self.password = password
        self.session = None
        self.transport = None
        self.seq = 1
        self.session_id = None
        self.socket_reader = None
        self.socket_writer = None
        self.streaming = False
        self.control_url = None

    async def connect(self):
        from urllib.parse import urlparse
        parsed = urlparse(self.url)
        host = parsed.hostname
        port = parsed.port or 554

        self.socket_reader, self.socket_writer = await asyncio.open_connection(host, port)

        await self.send_options()
        await self.send_describe()
        await self.send_setup()
        await self.send_play()
        self.streaming = True

    async def disconnect(self):
        try:
            await self.send_teardown()
        except Exception:
            pass
        if self.socket_writer:
            self.socket_writer.close()
            await self.socket_writer.wait_closed()
        self.streaming = False

    def _auth_header(self):
        userpass = f"{self.username}:{self.password}"
        token = base64.b64encode(userpass.encode()).decode()
        return f"Authorization: Basic {token}\r\n"

    async def send_options(self):
        req = (
            f"OPTIONS {self.url} RTSP/1.0\r\n"
            f"CSeq: {self.seq}\r\n"
            f"{self._auth_header()}"
            "\r\n"
        )
        self.seq += 1
        self.socket_writer.write(req.encode())
        await self.socket_writer.drain()
        await self._read_response()

    async def send_describe(self):
        req = (
            f"DESCRIBE {self.url} RTSP/1.0\r\n"
            f"CSeq: {self.seq}\r\n"
            "Accept: application/sdp\r\n"
            f"{self._auth_header()}"
            "\r\n"
        )
        self.seq += 1
        self.socket_writer.write(req.encode())
        await self.socket_writer.drain()
        data = await self._read_response()
        # parse control url from SDP
        sdp = data.split('\r\n\r\n', 1)[-1]
        for line in sdp.splitlines():
            if line.startswith('a=control:'):
                self.control_url = line.split(':', 1)[1]
        if not self.control_url.startswith('rtsp://'):
            self.control_url = self.url + '/' + self.control_url

    async def send_setup(self):
        # use TCP interleaved
        req = (
            f"SETUP {self.control_url} RTSP/1.0\r\n"
            f"CSeq: {self.seq}\r\n"
            "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
            f"{self._auth_header()}"
            "\r\n"
        )
        self.seq += 1
        self.socket_writer.write(req.encode())
        await self.socket_writer.drain()
        data = await self._read_response()
        for line in data.splitlines():
            if line.lower().startswith('session:'):
                self.session_id = line.split(':', 1)[1].strip().split(';')[0]

    async def send_play(self):
        req = (
            f"PLAY {self.url} RTSP/1.0\r\n"
            f"CSeq: {self.seq}\r\n"
            f"Session: {self.session_id}\r\n"
            f"{self._auth_header()}"
            "\r\n"
        )
        self.seq += 1
        self.socket_writer.write(req.encode())
        await self.socket_writer.drain()
        await self._read_response()

    async def send_teardown(self):
        req = (
            f"TEARDOWN {self.url} RTSP/1.0\r\n"
            f"CSeq: {self.seq}\r\n"
            f"Session: {self.session_id}\r\n"
            f"{self._auth_header()}"
            "\r\n"
        )
        self.seq += 1
        self.socket_writer.write(req.encode())
        await self.socket_writer.drain()
        await self._read_response()

    async def _read_response(self):
        data = b''
        while True:
            line = await self.socket_reader.readline()
            if not line or line == b'\r\n':
                break
            data += line
        rest = b''
        if b'Content-Length:' in data:
            # Handle SDP or other data
            for l in data.split(b'\r\n'):
                if l.lower().startswith(b'content-length:'):
                    length = int(l.split(b':')[1].strip())
                    break
            rest = await self.socket_reader.readexactly(length)
        return data.decode(errors="ignore") + rest.decode(errors="ignore")

    async def read_interleaved(self):
        # Receives RTP over TCP interleaved stream, yields H264 NAL units
        while self.streaming:
            b = await self.socket_reader.readexactly(1)
            if b != b'$':
                continue  # Not an interleaved packet
            channel = await self.socket_reader.readexactly(1)
            size = await self.socket_reader.readexactly(2)
            packet_length = struct.unpack('>H', size)[0]
            packet = await self.socket_reader.readexactly(packet_length)
            if channel == b'\x00':  # RTP data
                # Extract H264 NAL units from RTP
                nals = self._extract_h264_nals(packet)
                for nalu in nals:
                    yield nalu

    def _extract_h264_nals(self, rtp_packet):
        # Minimal RTP parse for H264 payload (single NAL, FU-A), returns list of NALU bytes
        nals = []
        if len(rtp_packet) < 12:
            return nals
        header_len = 12
        cc = rtp_packet[0] & 0x0F
        header_len += cc * 4
        payload = rtp_packet[header_len:]
        if not payload:
            return nals

        nal_type = payload[0] & 0x1F
        if nal_type >= 1 and nal_type <= 23:
            nals.append(b'\x00\x00\x00\x01' + payload)
        elif nal_type == 28:  # FU-A
            fu_indicator = payload[0]
            fu_header = payload[1]
            start_bit = fu_header & 0x80
            end_bit = fu_header & 0x40
            nal_unit_type = fu_header & 0x1F
            reconstructed_nal = bytes([(fu_indicator & 0xE0) | nal_unit_type])
            if start_bit:
                nalu = b'\x00\x00\x00\x01' + reconstructed_nal + payload[2:]
                self._fu_buffer = nalu
            else:
                if hasattr(self, '_fu_buffer'):
                    self._fu_buffer += payload[2:]
                else:
                    self._fu_buffer = payload[2:]
            if end_bit and hasattr(self, '_fu_buffer'):
                nals.append(self._fu_buffer)
                del self._fu_buffer
        # else ignore other types
        return nals

# ========== Camera Streaming Session Management ==========

class CameraSession:
    def __init__(self, rtsp_url, username, password):
        self.rtsp_url = rtsp_url
        self.username = username
        self.password = password
        self.rtsp_client: Optional[RTSPClient] = None
        self.streaming = False
        self.lock = asyncio.Lock()
        self.clients = 0
        self._stream_task = None
        self._queue = asyncio.Queue(maxsize=100)

    async def start(self):
        async with self.lock:
            if self.streaming:
                return
            self.rtsp_client = RTSPClient(self.rtsp_url, self.username, self.password)
            await self.rtsp_client.connect()
            self.streaming = True
            self._stream_task = asyncio.create_task(self._stream())

    async def stop(self):
        async with self.lock:
            self.streaming = False
            if self.rtsp_client:
                await self.rtsp_client.disconnect()
                self.rtsp_client = None
            if self._stream_task:
                self._stream_task.cancel()
                self._stream_task = None
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except Exception:
                    break

    async def _stream(self):
        try:
            async for nalu in self.rtsp_client.read_interleaved():
                for _ in range(self.clients):
                    await self._queue.put(nalu)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def get_stream(self):
        self.clients += 1
        try:
            while self.streaming:
                nalu = await self._queue.get()
                yield nalu
        finally:
            self.clients -= 1

# ========== FastAPI App & Endpoints ==========

app = FastAPI(
    title="Hikvision IP Camera Driver",
    description="Device driver for Hikvision IP Camera with HTTP streaming proxy.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rtsp_url = f"rtsp://{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}"
camera_session = CameraSession(rtsp_url, DEVICE_USERNAME, DEVICE_PASSWORD)

@app.post("/start")
@app.post("/video/start")
async def start_stream():
    await camera_session.start()
    return JSONResponse({"status": "started"}, status_code=status.HTTP_200_OK)

@app.post("/stop")
@app.post("/video/stop")
async def stop_stream():
    await camera_session.stop()
    return JSONResponse({"status": "stopped"}, status_code=status.HTTP_200_OK)

@app.get("/stream")
async def get_stream():
    if not camera_session.streaming:
        return JSONResponse({"error": "Stream not started"}, status_code=status.HTTP_400_BAD_REQUEST)
    async def streamer():
        async for nalu in camera_session.get_stream():
            yield nalu
    headers = {
        "Content-Type": "video/h264",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    return StreamingResponse(streamer(), media_type="video/h264", headers=headers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)