import os
import asyncio
import aiohttp
import threading
import cv2
import numpy as np
from aiohttp import web

# Environment variables
CAMERA_IP = os.environ.get('CAMERA_IP', '192.168.1.64')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_USERNAME = os.environ.get('CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('CAMERA_PASSWORD', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_PATH = os.environ.get('CAMERA_RTSP_PATH', 'Streaming/Channels/101')
RTSP_URL = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{RTSP_PATH}"

# Shared frame buffer for MJPEG streaming
frame_lock = threading.Lock()
latest_frame = None
stop_event = threading.Event()

def capture_frames():
    global latest_frame
    cap = cv2.VideoCapture(RTSP_URL)
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            continue
        with frame_lock:
            latest_frame = frame
    cap.release()

async def mjpeg_stream(request):
    boundary = 'frame'
    headers = {
        'Content-Type': f'multipart/x-mixed-replace; boundary={boundary}'
    }
    response = web.StreamResponse(status=200, reason='OK', headers=headers)
    await response.prepare(request)
    while True:
        await asyncio.sleep(0.03)  # ~30fps
        with frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None
        if frame is None:
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        data = jpeg.tobytes()
        await response.write(
            b"--" + boundary.encode() + b"\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + f"{len(data)}".encode() + b"\r\n"
            b"\r\n" + data + b"\r\n"
        )
    return response

async def on_startup(app):
    t = threading.Thread(target=capture_frames, daemon=True)
    t.start()
    app['capture_thread'] = t

async def on_cleanup(app):
    stop_event.set()
    app['capture_thread'].join()

app = web.Application()
app.router.add_get('/stream', mjpeg_stream)
app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)