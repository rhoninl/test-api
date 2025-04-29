import os
import asyncio
import base64
import json
import aiohttp
from aiohttp import web
import websockets

# Environment variables
ROSBRIDGE_WS_URL = os.getenv("ROSBRIDGE_WS_URL", "ws://localhost:9090")
ROSBRIDGE_CAMERA_TOPIC = os.getenv("ROSBRIDGE_CAMERA_TOPIC", "/camera/image/compressed")
HTTP_SERVER_HOST = os.getenv("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.getenv("HTTP_SERVER_PORT", "8080"))

# Global: single camera stream for all clients
class CameraStreamManager:
    def __init__(self):
        self.clients = set()
        self.latest_image = None
        self.ros_task = None
        self.ros_ws = None
        self.lock = asyncio.Lock()
        self.running = False

    async def start_stream(self):
        async with self.lock:
            if self.running:
                return
            self.running = True
            self.ros_task = asyncio.create_task(self._ros_listener())

    async def stop_stream(self):
        async with self.lock:
            if self.running and not self.clients:
                if self.ros_task:
                    self.ros_task.cancel()
                    self.ros_task = None
                if self.ros_ws:
                    await self.ros_ws.close()
                    self.ros_ws = None
                self.running = False

    async def add_client(self, ws_response):
        self.clients.add(ws_response)
        await self.start_stream()

    async def remove_client(self, ws_response):
        self.clients.discard(ws_response)
        if not self.clients:
            await self.stop_stream()

    async def _ros_listener(self):
        while True:
            try:
                async with websockets.connect(ROSBRIDGE_WS_URL) as ws:
                    self.ros_ws = ws
                    subscribe_msg = {
                        "op": "subscribe",
                        "topic": ROSBRIDGE_CAMERA_TOPIC,
                        "type": "sensor_msgs/CompressedImage"
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("msg") and "data" in data["msg"]:
                            self.latest_image = data["msg"]["data"]
            except Exception:
                await asyncio.sleep(1)  # Retry on connection failure

camera_manager = CameraStreamManager()

async def camera_stream(request):
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
    await camera_manager.add_client(response)

    try:
        last_sent = None
        while True:
            await asyncio.sleep(0.05)
            img_data_b64 = camera_manager.latest_image
            if img_data_b64 and img_data_b64 != last_sent:
                last_sent = img_data_b64
                img_bytes = base64.b64decode(img_data_b64)
                part = (
                    f"\r\n--{boundary}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(img_bytes)}\r\n\r\n"
                ).encode('utf-8') + img_bytes
                await response.write(part)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        await camera_manager.remove_client(response)
        try:
            await response.write_eof()
        except Exception:
            pass
    return response

app = web.Application()
app.router.add_get('/cam', camera_stream)

if __name__ == '__main__':
    web.run_app(app, host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT)