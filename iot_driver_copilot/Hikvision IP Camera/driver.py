import os
import asyncio
import aiohttp
from aiohttp import web
import cv2
import numpy as np
import base64

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_USERNAME = os.environ.get('RTSP_USERNAME', '')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '')
RTSP_PATH = os.environ.get('RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

def build_rtsp_url():
    auth = ''
    if RTSP_USERNAME and RTSP_PASSWORD:
        auth = f"{RTSP_USERNAME}:{RTSP_PASSWORD}@"
    return f"rtsp://{auth}{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

RTSP_URL = build_rtsp_url()

HTML_TEMPLATE = """
<html>
  <head>
    <title>Hikvision IP Camera Stream</title>
  </head>
  <body>
    <h1>Hikvision Live Stream</h1>
    <img src="/stream" width="720" />
  </body>
</html>
"""

async def index(request):
    return web.Response(text=HTML_TEMPLATE, content_type='text/html')

async def stream(request):
    # This response will use multipart/x-mixed-replace to deliver JPEG frames
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'multipart/x-mixed-replace; boundary=frame'
        }
    )
    await response.prepare(request)

    # OpenCV VideoCapture for RTSP stream
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        await response.write(b"--frame\r\nContent-Type: text/plain\r\n\r\nCould not open RTSP stream.\r\n")
        await response.write_eof()
        return response

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                await response.write(b"--frame\r\nContent-Type: text/plain\r\n\r\nFailed to read frame.\r\n")
                await asyncio.sleep(1)
                continue
            # JPEG encode
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            data = jpeg.tobytes()
            await response.write(
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
            )
            await asyncio.sleep(0.04)  # ~25 fps
    except asyncio.CancelledError:
        pass
    finally:
        cap.release()
        await response.write_eof()
    return response

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/stream', stream)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)