import os
import uuid
import threading
import queue
import time
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import cv2
import numpy as np

# Configuration via environment variables
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_USERNAME = os.environ.get("CAMERA_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_STREAM_PATH = os.environ.get("RTSP_STREAM_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))
HTTP_STREAM_FPS = int(os.environ.get("HTTP_STREAM_FPS", "10"))  # Frames per second for MJPEG HTTP stream

# Session management
sessions = {}
sessions_lock = threading.Lock()

app = FastAPI()

def make_rtsp_url():
    return f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{RTSP_PORT}/{RTSP_STREAM_PATH}"

class StreamSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.active = True
        self.buffer = queue.Queue(maxsize=10)
        self.thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.lock = threading.Lock()
        self.last_access = time.time()
        self.thread.start()

    def _capture_frames(self):
        rtsp_url = make_rtsp_url()
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            self.active = False
            return
        try:
            while self.active:
                ret, frame = cap.read()
                if not ret:
                    break
                # Encode frame as JPEG
                ret, jpeg = cv2.imencode('.jpg', frame)
                if not ret:
                    continue
                # Put into buffer
                try:
                    self.buffer.put(jpeg.tobytes(), timeout=1)
                except queue.Full:
                    pass  # Drop frame if buffer is full
                self.last_access = time.time()
                time.sleep(1.0 / HTTP_STREAM_FPS)
        finally:
            cap.release()
            self.active = False

    def read_frame(self, timeout=5):
        try:
            return self.buffer.get(timeout=timeout)
        except queue.Empty:
            return None

    def terminate(self):
        self.active = False

    def is_active(self):
        return self.active

    def update_access(self):
        self.last_access = time.time()

class InitStreamRequest(BaseModel):
    format: Optional[str] = "mjpeg"

class TerminateStreamRequest(BaseModel):
    session_id: Optional[str] = None

@app.post("/camera/stream/init")
async def init_stream(req: InitStreamRequest):
    # Only support MJPEG over HTTP for browser
    session_id = str(uuid.uuid4())
    with sessions_lock:
        session = StreamSession(session_id)
        # Wait briefly to check if the stream is valid
        time.sleep(1)
        if not session.is_active():
            return JSONResponse(status_code=500, content={"error": "Unable to connect to camera RTSP stream."})
        sessions[session_id] = session
    url = f"/camera/stream/live?session_id={session_id}"
    return {
        "session_id": session_id,
        "http_stream_url": url
    }

def mjpeg_stream_generator(session: StreamSession):
    boundary = "--frame"
    try:
        while session.is_active():
            frame = session.read_frame(timeout=5)
            if frame is None:
                break
            yield (
                f"{boundary}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode("utf-8") + frame + b"\r\n"
    finally:
        session.terminate()

@app.get("/camera/stream/live")
async def live_stream(session_id: str):
    with sessions_lock:
        session = sessions.get(session_id)
        if not session or not session.is_active():
            raise HTTPException(status_code=404, detail="Session not found or inactive.")
    session.update_access()
    return StreamingResponse(
        mjpeg_stream_generator(session),
        media_type="multipart/x-mixed-replace; boundary=--frame"
    )

@app.post("/camera/stream/terminate")
async def terminate_stream(req: TerminateStreamRequest, request: Request):
    session_id = req.session_id or request.query_params.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    with sessions_lock:
        session = sessions.get(session_id)
        if session:
            session.terminate()
            del sessions[session_id]
            return {"status": "terminated"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")

def session_cleanup_worker(timeout_sec=30):
    while True:
        now = time.time()
        with sessions_lock:
            stale = [sid for sid, s in sessions.items() if now - s.last_access > timeout_sec or not s.is_active()]
            for sid in stale:
                sessions[sid].terminate()
                del sessions[sid]
        time.sleep(10)

cleanup_thread = threading.Thread(target=session_cleanup_worker, daemon=True)
cleanup_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)