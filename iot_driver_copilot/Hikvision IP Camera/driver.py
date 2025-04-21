import os
import asyncio
import aiohttp
import aiohttp.web
import cv2
import numpy as np

# Environment variables
CAMERA_IP = os.environ.get("CAMERA_IP", "127.0.0.1")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", 554))
CAMERA_RTSP_USER = os.environ.get("CAMERA_RTSP_USER", "admin")
CAMERA_RTSP_PASSWORD = os.environ.get("CAMERA_RTSP_PASSWORD", "admin")
CAMERA_CHANNEL = os.environ.get("CAMERA_CHANNEL", "1")
CAMERA_STREAM = os.environ.get("CAMERA_STREAM", "1")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))

# Construct RTSP URL for Hikvision
RTSP_URL = (
    f"rtsp://{CAMERA_RTSP_USER}:{CAMERA_RTSP_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}"
    f"/Streaming/Channels/{CAMERA_CHANNEL}0{CAMERA_STREAM}"
)

BOUNDARY = "frameboundary"

async def stream_generator():
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        yield (
            b"--" + BOUNDARY.encode() + b"\r\n"
            b"Content-Type: text/plain\r\n\r\n"
            b"Could not connect to camera stream\r\n"
        )
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                await asyncio.sleep(0.1)
                continue
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode(".jpg", frame)
            if not ret:
                await asyncio.sleep(0.01)
                continue
            data = jpeg.tobytes()
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n" +
                data + b"\r\n"
            )
            await asyncio.sleep(0.04)  # ~25 FPS
    finally:
        cap.release()

async def stream_handler(request):
    headers = {
        "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "close",
    }
    resp = aiohttp.web.StreamResponse(status=200, reason='OK', headers=headers)
    await resp.prepare(request)
    async for chunk in stream_generator():
        await resp.write(chunk)
    return resp

app = aiohttp.web.Application()
app.router.add_get('/stream', stream_handler)

if __name__ == "__main__":
    aiohttp.web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)