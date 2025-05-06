import os
import asyncio
import aiohttp
import aiohttp.web
import base64
import threading
import queue

from aiohttp import web

# Environment Variables
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", 554))
CAMERA_HTTP_PORT = int(os.environ.get("CAMERA_HTTP_PORT", 80))
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "")
CAMERA_CHANNEL = os.environ.get("CAMERA_CHANNEL", "101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8000))

RTSP_PATH = os.environ.get("CAMERA_RTSP_PATH", f"/Streaming/Channels/{CAMERA_CHANNEL}")
RTSP_URL = f"rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{RTSP_PATH}"
SNAPSHOT_URL = f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}/ISAPI/Streaming/channels/{CAMERA_CHANNEL}/picture"
SNAPSHOT_TRIGGER_URL = f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}/ISAPI/ContentMgmt/record/control/manualTrigger"

SESSION_ACTIVE = threading.Event()
STREAM_QUEUE = queue.Queue(maxsize=512)

def get_basic_auth():
    userpass = f"{CAMERA_USER}:{CAMERA_PASSWORD}"
    return base64.b64encode(userpass.encode()).decode()

async def handle_snap(request):
    # GET: retrieve current snapshot
    auth = aiohttp.BasicAuth(CAMERA_USER, CAMERA_PASSWORD)
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(SNAPSHOT_URL, timeout=10) as resp:
            if resp.status == 200:
                img = await resp.read()
                return web.Response(body=img, content_type='image/jpeg')
            else:
                return web.Response(status=resp.status, text="Snapshot failed.")

async def handle_snap_post(request):
    # POST: trigger snapshot and return the image
    auth = aiohttp.BasicAuth(CAMERA_USER, CAMERA_PASSWORD)
    headers = {"Content-Type": "application/xml"}
    xml_trigger = "<ManualRecord><channelID>1</channelID><cmd>start</cmd></ManualRecord>"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.put(SNAPSHOT_TRIGGER_URL, data=xml_trigger, headers=headers, timeout=10) as resp:
            if resp.status not in (200, 201, 204):
                return web.Response(status=resp.status, text="Snapshot trigger failed")
        # After trigger, fetch snapshot
        async with session.get(SNAPSHOT_URL, timeout=10) as resp:
            if resp.status == 200:
                img = await resp.read()
                return web.Response(body=img, content_type='image/jpeg')
            else:
                return web.Response(status=resp.status, text="Snapshot retrieval failed.")

async def handle_play(request):
    # POST: Start stream
    SESSION_ACTIVE.set()
    return web.json_response({"message": "Streaming session started", "stream_url": f"http://{SERVER_HOST}:{SERVER_PORT}/video"})

async def handle_halt(request):
    # POST: Stop stream
    SESSION_ACTIVE.clear()
    return web.json_response({"message": "Streaming session stopped"})

async def handle_video(request):
    # GET: MJPEG over HTTP
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'multipart/x-mixed-replace; boundary=frame'
        }
    )
    await response.prepare(request)

    # Start RTSP client in a separate thread if not running
    if not SESSION_ACTIVE.is_set():
        SESSION_ACTIVE.set()

    def start_rtsp_to_jpeg_thread():
        if hasattr(start_rtsp_to_jpeg_thread, "thread") and start_rtsp_to_jpeg_thread.thread.is_alive():
            return
        def rtsp_to_jpeg_worker():
            import cv2
            cap = cv2.VideoCapture(RTSP_URL)
            if not cap.isOpened():
                SESSION_ACTIVE.clear()
                return
            try:
                while SESSION_ACTIVE.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        continue
                    ret, jpeg = cv2.imencode('.jpg', frame)
                    if ret:
                        try:
                            STREAM_QUEUE.put(jpeg.tobytes(), timeout=0.1)
                        except queue.Full:
                            pass
            finally:
                cap.release()
        t = threading.Thread(target=rtsp_to_jpeg_worker, daemon=True)
        t.start()
        start_rtsp_to_jpeg_thread.thread = t

    start_rtsp_to_jpeg_thread()

    try:
        while SESSION_ACTIVE.is_set():
            try:
                frame = STREAM_QUEUE.get(timeout=2)
            except queue.Empty:
                continue
            await response.write(
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' +
                frame + b'\r\n'
            )
            await asyncio.sleep(0.04)  # About 25 FPS
    except asyncio.CancelledError:
        pass
    finally:
        SESSION_ACTIVE.clear()
        return response

app = web.Application()
app.router.add_post('/play', handle_play)
app.router.add_get('/snap', handle_snap)
app.router.add_post('/snap', handle_snap_post)
app.router.add_post('/halt', handle_halt)
app.router.add_get('/video', handle_video)

if __name__ == '__main__':
    import cv2  # Ensure OpenCV is imported for the thread
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)