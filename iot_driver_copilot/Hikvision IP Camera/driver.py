import os
import asyncio
import aiohttp
from aiohttp import web
import cv2
import numpy as np
import base64

# Config from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "127.0.0.1")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USERNAME = os.environ.get("RTSP_USERNAME", "admin")
RTSP_PASSWORD = os.environ.get("RTSP_PASSWORD", "admin")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL = f"rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

# HTML page for browser viewing
HTML_PAGE = """
<html>
<head>
    <title>Hikvision Camera Stream</title>
</head>
<body>
    <h2>Hikvision Camera Live Stream (MJPEG over HTTP)</h2>
    <img src="/stream" width="720" />
</body>
</html>
"""

async def mjpeg_stream(request):
    boundary = "frame"
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': f'multipart/x-mixed-replace; boundary=--{boundary}',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
    )
    await response.prepare(request)

    # OpenCV VideoCapture needs to run in a thread, not event loop
    loop = asyncio.get_event_loop()

    def get_video_capture():
        return cv2.VideoCapture(RTSP_URL)

    cap = await loop.run_in_executor(None, get_video_capture)

    if not cap.isOpened():
        await response.write(b"--frame\r\nContent-Type: text/plain\r\n\r\nUnable to connect to camera\r\n")
        await response.write_eof()
        return response

    try:
        while True:
            ret, frame = await loop.run_in_executor(None, cap.read)
            if not ret or frame is None:
                await asyncio.sleep(0.1)
                continue
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            data = jpeg.tobytes()
            part = (
                f"--{boundary}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(data)}\r\n"
                "\r\n"
            ).encode('utf-8') + data + b"\r\n"
            try:
                await response.write(part)
                await response.drain()
            except (ConnectionResetError, asyncio.CancelledError):
                break
            await asyncio.sleep(0.03)  # ~30fps
    finally:
        try:
            cap.release()
        except Exception:
            pass
        await response.write_eof()
    return response

async def index(request):
    return web.Response(text=HTML_PAGE, content_type='text/html')

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/stream', mjpeg_stream)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)