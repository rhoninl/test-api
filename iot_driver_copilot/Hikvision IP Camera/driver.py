import os
import asyncio
import aiohttp
import aiohttp.web
import base64

import cv2
import numpy as np

# ----- Configuration from Environment Variables -----

CAMERA_IP = os.environ.get("HIKVISION_CAMERA_IP", "192.168.1.64")
CAMERA_RTSP_PORT = int(os.environ.get("HIKVISION_CAMERA_RTSP_PORT", "554"))
CAMERA_USERNAME = os.environ.get("HIKVISION_CAMERA_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("HIKVISION_CAMERA_PASSWORD", "12345")
CAMERA_STREAM_PATH = os.environ.get("HIKVISION_CAMERA_STREAM_PATH", "/Streaming/Channels/101")
SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

# ----- RTSP URL Construction -----

def get_rtsp_url():
    user_pass = f"{CAMERA_USERNAME}:{CAMERA_PASSWORD}@"
    return f"rtsp://{user_pass}{CAMERA_IP}:{CAMERA_RTSP_PORT}{CAMERA_STREAM_PATH}"

# ----- Streaming State Management -----

class StreamSession:
    """Manages the video capture and streaming lifecycle."""
    def __init__(self):
        self.active = False
        self.cap = None
        self.lock = asyncio.Lock()
        self.latest_frame = None
        self.read_task = None

    async def start(self):
        async with self.lock:
            if self.active:
                return
            self.cap = cv2.VideoCapture(get_rtsp_url())
            if not self.cap.isOpened():
                raise RuntimeError("Failed to open RTSP stream.")
            self.active = True
            self.read_task = asyncio.create_task(self._frame_reader())

    async def stop(self):
        async with self.lock:
            self.active = False
            if self.read_task:
                self.read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.read_task
                self.read_task = None
            if self.cap:
                self.cap.release()
                self.cap = None
            self.latest_frame = None

    async def _frame_reader(self):
        try:
            while self.active and self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if not ret:
                    await asyncio.sleep(0.05)
                    continue
                # Encode as JPEG for browser compatibility
                ret, buf = cv2.imencode('.jpg', frame)
                if ret:
                    self.latest_frame = buf.tobytes()
                await asyncio.sleep(0.01)  # ~100fps max
        except asyncio.CancelledError:
            pass

    async def get_frame(self):
        return self.latest_frame

stream_session = StreamSession()

# ----- HTTP API -----

routes = aiohttp.web.RouteTableDef()

@routes.post("/start")
@routes.post("/video/start")
async def start_stream(request):
    try:
        await stream_session.start()
        return aiohttp.web.json_response({"status": "started"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "msg": str(e)}, status=500)

@routes.post("/stop")
@routes.post("/video/stop")
async def stop_stream(request):
    try:
        await stream_session.stop()
        return aiohttp.web.json_response({"status": "stopped"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "msg": str(e)}, status=500)

@routes.get("/stream")
async def video_stream(request):
    # Start the stream if not already running
    if not stream_session.active:
        try:
            await stream_session.start()
        except Exception as e:
            return aiohttp.web.Response(status=500, text=f"Failed to start: {str(e)}")

    async def mjpeg_response():
        boundary = "frame"
        while True:
            frame = await stream_session.get_frame()
            if frame is not None:
                yield (
                    f"--{boundary}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n"
                    "\r\n"
                ).encode('utf-8')
                yield frame
                yield b"\r\n"
            await asyncio.sleep(0.03)  # ~30fps

    headers = {
        "Content-Type": 'multipart/x-mixed-replace; boundary=frame',
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    return aiohttp.web.Response(body=mjpeg_response(), headers=headers)

# ----- App Factory -----

async def on_shutdown(app):
    await stream_session.stop()

def main():
    app = aiohttp.web.Application()
    app.add_routes(routes)
    app.on_shutdown.append(on_shutdown)
    aiohttp.web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)

if __name__ == "__main__":
    import contextlib
    main()