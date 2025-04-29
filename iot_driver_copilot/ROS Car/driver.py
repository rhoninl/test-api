import os
import asyncio
import json
import base64
import aiohttp
from aiohttp import web
import websockets

ROSBRIDGE_WS_HOST = os.environ.get("ROSBRIDGE_WS_HOST", "localhost")
ROSBRIDGE_WS_PORT = int(os.environ.get("ROSBRIDGE_WS_PORT", "9090"))
ROS_CAMERA_TOPIC = os.environ.get("ROS_CAMERA_TOPIC", "/camera/image/compressed")
HTTP_SERVER_HOST = os.environ.get("HTTP_SERVER_HOST", "0.0.0.0")
HTTP_SERVER_PORT = int(os.environ.get("HTTP_SERVER_PORT", "8080"))

class RosCameraStream:
    def __init__(self, ws_host, ws_port, camera_topic):
        self.ws_uri = f"ws://{ws_host}:{ws_port}"
        self.camera_topic = camera_topic
        self.latest_image = None
        self._clients = set()
        self._ws = None
        self._listen_task = None

    async def connect(self):
        self._ws = await websockets.connect(self.ws_uri)
        subscribe_msg = {
            "op": "subscribe",
            "topic": self.camera_topic,
            "type": "sensor_msgs/CompressedImage",
            "queue_length": 1
        }
        await self._ws.send(json.dumps(subscribe_msg))
        self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self):
        try:
            async for msg in self._ws:
                msg_data = json.loads(msg)
                if msg_data.get("op") == "publish" and "msg" in msg_data:
                    img_data_b64 = msg_data["msg"].get("data")
                    if img_data_b64:
                        self.latest_image = base64.b64decode(img_data_b64)
        except Exception:
            pass

    async def get_latest_image(self):
        if self.latest_image is not None:
            return self.latest_image
        return None

    async def start(self):
        await self.connect()

async def camera_stream(request):
    # MJPEG HTTP stream
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
            'Cache-Control': 'no-cache',
            'Connection': 'close',
            'Pragma': 'no-cache'
        }
    )
    await response.prepare(request)
    last_image = None
    try:
        while True:
            image = await request.app["ros_camera"].get_latest_image()
            if image is not None and image != last_image:
                await response.write(b"--frame\r\n")
                await response.write(b"Content-Type: image/jpeg\r\n\r\n")
                await response.write(image)
                await response.write(b"\r\n")
                last_image = image
            await asyncio.sleep(0.05)  # ~20 FPS
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    return response

async def on_startup(app):
    ros_camera = RosCameraStream(ROSBRIDGE_WS_HOST, ROSBRIDGE_WS_PORT, ROS_CAMERA_TOPIC)
    await ros_camera.start()
    app["ros_camera"] = ros_camera

def main():
    app = web.Application()
    app.router.add_get('/cam', camera_stream)
    app.on_startup.append(on_startup)
    web.run_app(app, host=HTTP_SERVER_HOST, port=HTTP_SERVER_PORT)

if __name__ == "__main__":
    main()