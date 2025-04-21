```python
import os
import asyncio
import uuid
from typing import Dict, Optional
from fastapi import FastAPI, Request, HTTPException, Response, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import av

DEVICE_IP = os.environ.get("DEVICE_IP")
DEVICE_RTSP_PORT = int(os.environ.get("DEVICE_RTSP_PORT", "554"))
DEVICE_RTSP_USER = os.environ.get("DEVICE_RTSP_USER", "admin")
DEVICE_RTSP_PASS = os.environ.get("DEVICE_RTSP_PASS", "12345")
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

RTSP_URL_TEMPLATE = "rtsp://{user}:{pwd}@{ip}:{port}/{path}"

app = FastAPI()

class SessionInfo:
    def __init__(self, rtsp_url: str, fmt: str):
        self.rtsp_url = rtsp_url
        self.format = fmt
        self.container: Optional[av.container.InputContainer] = None
        self.active = True
        self.lock = asyncio.Lock()

_sessions: Dict[str, SessionInfo] = {}

class InitStreamRequest(BaseModel):
    format: Optional[str] = "mjpeg"  # "mjpeg" or "h264"

class TerminateRequest(BaseModel):
    session_id: str

def get_rtsp_url(fmt: str):
    return RTSP_URL_TEMPLATE.format(
        user=DEVICE_RTSP_USER,
        pwd=DEVICE_RTSP_PASS,
        ip=DEVICE_IP,
        port=DEVICE_RTSP_PORT,
        path=RTSP_PATH
    )

def get_stream_mime(fmt: str):
    if fmt.lower() == "mjpeg":
        return "multipart/x-mixed-replace; boundary=frame"
    elif fmt.lower() == "h264":
        return "video/mp4"
    else:
        return "application/octet-stream"

@app.post("/camera/stream/init")
async def camera_stream_init(req: InitStreamRequest):
    fmt = req.format.lower() if req.format else "mjpeg"
    if fmt not in ("mjpeg", "h264"):
        raise HTTPException(status_code=400, detail="Unsupported format")
    session_id = str(uuid.uuid4())
    rtsp_url = get_rtsp_url(fmt)
    _sessions[session_id] = SessionInfo(rtsp_url, fmt)
    stream_url = f"/camera/stream/live?session_id={session_id}"
    return JSONResponse({
        "session_id": session_id,
        "stream_url": stream_url,
        "format": fmt
    })

async def mjpeg_streamer(session: SessionInfo):
    """Proxy RTSP H.264 or MJPEG to HTTP multipart/x-mixed-replace MJPEG."""
    try:
        async with session.lock:
            if not session.container:
                # Open the RTSP stream via PyAV
                session.container = av.open(session.rtsp_url, timeout=5)
        video_stream = next(s for s in session.container.streams if s.type == "video")
        for frame in session.container.decode(video_stream):
            if not session.active:
                break
            # Convert frame to JPEG
            jpg = frame.to_image()
            import io
            buf = io.BytesIO()
            jpg.save(buf, format='JPEG')
            part = (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' +
                buf.getvalue() + b'\r\n'
            )
            yield part
            await asyncio.sleep(0.001)
    except Exception:
        pass
    finally:
        if session.container:
            session.container.close()
            session.container = None

async def h264_streamer(session: SessionInfo):
    """Proxy RTSP H.264 to HTTP as fragmented MP4 stream."""
    # We encode a minimal MP4 stream for browser
    try:
        async with session.lock:
            if not session.container:
                session.container = av.open(session.rtsp_url, timeout=5)
        output = av.open(
            None, 'w', format='mp4',
            options={'movflags': 'frag_keyframe+empty_moov'}
        )
        video_stream_in = next(s for s in session.container.streams if s.type == 'video')
        video_stream_out = output.add_stream(template=video_stream_in)
        for frame in session.container.decode(video_stream_in):
            if not session.active:
                break
            for packet in video_stream_out.encode(frame):
                yield packet.to_bytes()
            await asyncio.sleep(0.001)
        # Flush encoder
        for packet in video_stream_out.encode():
            yield packet.to_bytes()
    except Exception:
        pass
    finally:
        if session.container:
            session.container.close()
            session.container = None

@app.get("/camera/stream/live")
async def camera_stream_live(request: Request, session_id: str):
    session = _sessions.get(session_id)
    if not session or not session.active:
        raise HTTPException(status_code=404, detail="Session not found or inactive")
    fmt = session.format
    mime = get_stream_mime(fmt)
    if fmt == "mjpeg":
        streamer = mjpeg_streamer
    elif fmt == "h264":
        streamer = h264_streamer
    else:
        raise HTTPException(status_code=400, detail="Unsupported format")
    return StreamingResponse(
        streamer(session),
        media_type=mime
    )

@app.post("/camera/stream/terminate")
async def camera_stream_terminate(req: TerminateRequest):
    session_id = req.session_id
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.active = False
    async with session.lock:
        if session.container:
            session.container.close()
            session.container = None
    del _sessions[session_id]
    return JSONResponse({"status": "terminated"})

@app.post("/stream/stop")
async def stream_stop(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.active = False
    async with session.lock:
        if session.container:
            session.container.close()
            session.container = None
    del _sessions[session_id]
    return JSONResponse({"status": "stopped"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
```
