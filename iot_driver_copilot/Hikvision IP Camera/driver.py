import os
import asyncio
import threading
import base64
import aiohttp
import aiohttp.web
import cv2
import numpy as np

# Configuration from environment variables
DEVICE_IP = os.environ.get("HIKVISION_IP")
DEVICE_RTSP_PORT = int(os.environ.get("HIKVISION_RTSP_PORT", "554"))
DEVICE_RTSP_PATH = os.environ.get("HIKVISION_RTSP_PATH", "Streaming/Channels/101")
DEVICE_USER = os.environ.get("HIKVISION_USER")
DEVICE_PASS = os.environ.get("HIKVISION_PASS")
SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))
RTSP_TRANSPORT = os.environ.get("RTSP_TRANSPORT", "tcp")  # tcp or udp

# Global state for stream control
stream_active = False
frame_buffer = []
buffer_lock = threading.Lock()
stream_thread = None
stream_stop_event = threading.Event()

def get_rtsp_url():
    auth = ""
    if DEVICE_USER and DEVICE_PASS:
        auth = f"{DEVICE_USER}:{DEVICE_PASS}@"
    return f"rtsp://{auth}{DEVICE_IP}:{DEVICE_RTSP_PORT}/{DEVICE_RTSP_PATH}"

def camera_stream_worker():
    global frame_buffer, stream_active
    rtsp_url = get_rtsp_url()

    # OpenCV VideoCapture
    cap = cv2.VideoCapture()
    # Set protocol
    if RTSP_TRANSPORT == "udp":
        cap.open(rtsp_url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_RTSP_TRANSPORT, cv2.VideoWriter_fourcc(*'U','D','P')])
    else:
        cap.open(rtsp_url)
    if not cap.isOpened():
        stream_active = False
        return

    stream_active = True
    while not stream_stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            continue
        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        with buffer_lock:
            frame_buffer = [jpeg.tobytes()]
    cap.release()
    stream_active = False

async def start_video_stream():
    global stream_thread, stream_stop_event, stream_active
    if stream_active:
        return
    stream_stop_event.clear()
    stream_thread = threading.Thread(target=camera_stream_worker, daemon=True)
    stream_thread.start()
    # Wait until stream starts (with timeout)
    for _ in range(20):
        if stream_active:
            break
        await asyncio.sleep(0.1)

async def stop_video_stream():
    global stream_thread, stream_stop_event, stream_active
    stream_stop_event.set()
    if stream_thread:
        stream_thread.join(timeout=2)
    stream_active = False

# HTTP API
routes = aiohttp.web.RouteTableDef()

@routes.post('/start')
@routes.post('/video/start')
async def api_start(request):
    await start_video_stream()
    return aiohttp.web.json_response({"status": "started" if stream_active else "failed"})

@routes.post('/stop')
@routes.post('/video/stop')
async def api_stop(request):
    await stop_video_stream()
    return aiohttp.web.json_response({"status": "stopped"})

@routes.get('/stream')
async def api_stream(request):
    # Start stream if not already started
    if not stream_active:
        await start_video_stream()
        if not stream_active:
            return aiohttp.web.Response(text="Unable to start stream", status=500)
    # Stream MJPEG
    response = aiohttp.web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
    )
    await response.prepare(request)
    try:
        while stream_active:
            with buffer_lock:
                if frame_buffer:
                    frame = frame_buffer[0]
                else:
                    frame = None
            if frame is not None:
                await response.write(b'--frame\r\n')
                await response.write(b'Content-Type: image/jpeg\r\n\r\n')
                await response.write(frame)
                await response.write(b'\r\n')
            await asyncio.sleep(0.033)  # ~30fps
    finally:
        await response.write_eof()
    return response

app = aiohttp.web.Application()
app.add_routes(routes)

if __name__ == '__main__':
    aiohttp.web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)