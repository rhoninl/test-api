import os
import asyncio
import aiohttp
from aiohttp import web
import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.1.64")
RTSP_PORT = os.environ.get("RTSP_PORT", "554")
RTSP_USER = os.environ.get("RTSP_USER", "admin")
RTSP_PASS = os.environ.get("RTSP_PASS", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Build the RTSP URL
if RTSP_USER and RTSP_PASS:
    RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"
else:
    RTSP_URL = f"rtsp://{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

BOUNDARY = "frameboundary"

async def mjpeg_stream(request):
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': f'multipart/x-mixed-replace; boundary=--{BOUNDARY}',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }
    )
    await response.prepare(request)

    loop = asyncio.get_event_loop()

    def open_rtsp():
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            return None
        return cap

    cap = await loop.run_in_executor(None, open_rtsp)
    if cap is None:
        await response.write(b'--%b\r\nContent-Type: text/plain\r\n\r\nUnable to open RTSP stream.\r\n' % BOUNDARY.encode())
        await response.write_eof()
        return response

    try:
        while True:
            ret, frame = await loop.run_in_executor(None, cap.read)
            if not ret:
                break
            _, jpeg = cv2.imencode('.jpg', frame)
            if not _:
                continue
            img_bytes = jpeg.tobytes()
            await response.write(
                b'--%b\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % (BOUNDARY.encode(), len(img_bytes))
            )
            await response.write(img_bytes)
            await response.write(b'\r\n')
            await asyncio.sleep(0.04)  # ~25 fps
    except asyncio.CancelledError:
        pass
    finally:
        cap.release()
        await response.write_eof()
    return response

app = web.Application()
app.router.add_get('/stream', mjpeg_stream)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)