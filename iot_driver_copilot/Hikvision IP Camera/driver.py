```python
import os
import asyncio
import aiohttp
import cv2
import av
import numpy as np
from aiohttp import web

# Environment Variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
RTSP_PORT = int(os.environ.get('RTSP_PORT', '554'))
RTSP_USER = os.environ.get('RTSP_USER', 'admin')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
RTSP_PATH = os.environ.get('RTSP_PATH', 'Streaming/Channels/101')

RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

# Async generator to yield multipart JPEG frames
async def mjpeg_stream():
    # OpenCV VideoCapture is blocking, so we use av.open in a thread
    container = None
    try:
        container = av.open(RTSP_URL)
        stream = next(s for s in container.streams if s.type == 'video')
        for frame in container.decode(stream):
            img = frame.to_ndarray(format='bgr24')
            ret, jpeg = cv2.imencode('.jpg', img)
            if not ret:
                continue
            data = jpeg.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + data + b'\r\n')
            await asyncio.sleep(0.04)  # ~25 fps
    except Exception:
        pass
    finally:
        if container:
            container.close()

async def stream_handler(request):
    headers = {
        'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    }
    response = web.StreamResponse(status=200, reason='OK', headers=headers)
    await response.prepare(request)
    async for frame in mjpeg_stream():
        await response.write(frame)
    return response

app = web.Application()
app.router.add_get('/stream', stream_handler)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)
```
