import os
import asyncio
import json
import base64
from aiohttp import web
import aiohttp
import websockets

# Environment Variables
ROSBRIDGE_HOST = os.environ.get("ROSBRIDGE_HOST", "localhost")
ROSBRIDGE_PORT = int(os.environ.get("ROSBRIDGE_PORT", "9090"))

HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

CAMERA_IMAGE_TOPIC = os.environ.get("CAMERA_IMAGE_TOPIC", "/camera/image/compressed")
CAMERA_IMAGE_TYPE = os.environ.get("CAMERA_IMAGE_TYPE", "sensor_msgs/CompressedImage")
CAMERA_IMAGE_FIELD = os.environ.get("CAMERA_IMAGE_FIELD", "data")
CAMERA_IMAGE_ENCODING = os.environ.get("CAMERA_IMAGE_ENCODING", "jpeg")  # just for content-type

class ROSBridgeCameraProxy:
    def __init__(self, host, port, topic, msg_type):
        self._uri = f"ws://{host}:{port}"
        self._topic = topic
        self._msg_type = msg_type
        self._ws = None
        self._msg_queue = asyncio.Queue()
        self._listener_task = None

    async def connect(self):
        self._ws = await websockets.connect(self._uri)
        subscribe_msg = {
            "op": "subscribe",
            "topic": self._topic,
            "type": self._msg_type,
            "queue_length": 1,
        }
        await self._ws.send(json.dumps(subscribe_msg))
        if self._listener_task is None:
            self._listener_task = asyncio.create_task(self._msg_listener())

    async def _msg_listener(self):
        try:
            async for message in self._ws:
                msg = json.loads(message)
                if "msg" in msg and CAMERA_IMAGE_FIELD in msg["msg"]:
                    await self._msg_queue.put(msg["msg"][CAMERA_IMAGE_FIELD])
        except Exception:
            pass
        finally:
            await self._cleanup()

    async def get_next_image(self):
        return await self._msg_queue.get()

    async def _cleanup(self):
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None

    async def disconnect(self):
        await self._cleanup()

camera_proxy = ROSBridgeCameraProxy(
    ROSBRIDGE_HOST,
    ROSBRIDGE_PORT,
    CAMERA_IMAGE_TOPIC,
    CAMERA_IMAGE_TYPE,
)

async def cam_mjpeg_stream(request):
    boundary = "frame"
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': f'multipart/x-mixed-replace; boundary=--{boundary}',
            'Cache-Control': 'no-cache',
            'Connection': 'close',
        }
    )
    await response.prepare(request)

    # Connect once for all clients
    if camera_proxy._ws is None:
        await camera_proxy.connect()

    try:
        while True:
            img_b64 = await camera_proxy.get_next_image()
            img_bytes = base64.b64decode(img_b64)
            await response.write(
                f"--{boundary}\r\nContent-Type: image/{CAMERA_IMAGE_ENCODING}\r\nContent-Length: {len(img_bytes)}\r\n\r\n".encode('utf-8') + img_bytes + b"\r\n"
            )
            await response.drain()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        return response

app = web.Application()
app.router.add_get('/cam', cam_mjpeg_stream)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(camera_proxy.connect())
    web.run_app(app, host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT)