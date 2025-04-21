import os
import io
import uuid
import threading
import queue
from typing import Dict, Optional
from http import HTTPStatus

from fastapi import FastAPI, Request, Response, Query, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import cv2
import uvicorn

# ================= Environment Variables =================
CAMERA_IP = os.environ.get("CAMERA_IP")
CAMERA_RTSP_PORT = int(os.environ.get("CAMERA_RTSP_PORT", "554"))
CAMERA_USERNAME = os.environ.get("CAMERA_USERNAME", "admin")
CAMERA_PASSWORD = os.environ.get("CAMERA_PASSWORD", "12345")
CAMERA_STREAM_PATH = os.environ.get("CAMERA_STREAM_PATH", "Streaming/Channels/101")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

# =================== Session State =======================
class StreamSession:
    def __init__(self, session_id: str, rtsp_url: str):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.frame_queue = queue.Queue(maxsize=100)
        self.running = threading.Event()
        self.running.set()
        self.capture_thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.capture_thread.start()

    def _capture_frames(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        if not cap.isOpened():
            self.running.clear()
            return
        try:
            while self.running.is_set():
                ret, frame = cap.read()
                if not ret:
                    break
                # Convert to JPEG
                ret, jpeg = cv2.imencode('.jpg', frame)
                if not ret:
                    continue
                try:
                    self.frame_queue.put(jpeg.tobytes(), timeout=1)
                except queue.Full:
                    continue
        finally:
            cap.release()

    def read_frame(self) -> Optional[bytes]:
        try:
            return self.frame_queue.get(timeout=2)
        except queue.Empty:
            return None

    def stop(self):
        self.running.clear()
        while not self.frame_queue.empty():
            self.frame_queue.get_nowait()
        self.capture_thread.join(timeout=2)

sessions: Dict[str, StreamSession] = {}
sessions_lock = threading.Lock()

# ================= FastAPI App ============================
app = FastAPI()

class InitStreamResponse(BaseModel):
    session_id: str
    stream_url: str

class TerminateRequest(BaseModel):
    session_id: str

@app.post("/camera/stream/init", response_model=InitStreamResponse)
async def init_stream():
    session_id = str(uuid.uuid4())
    rtsp_url = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_STREAM_PATH}"
    session = StreamSession(session_id, rtsp_url)
    with sessions_lock:
        sessions[session_id] = session
    stream_url = f"/camera/stream/live?session_id={session_id}"
    return InitStreamResponse(session_id=session_id, stream_url=stream_url)

def gen_mjpeg(session: StreamSession):
    boundary = "frame"
    while session.running.is_set():
        frame = session.read_frame()
        if frame is None:
            break
        yield (
            b"--%b\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: %d\r\n\r\n" % (boundary.encode(), len(frame))
        )
        yield frame
        yield b"\r\n"
    yield b"--%b--\r\n" % boundary.encode()

@app.get("/camera/stream/live")
async def get_live_stream(session_id: str = Query(..., description="Session identifier from /camera/stream/init")):
    with sessions_lock:
        session = sessions.get(session_id)
    if session is None or not session.running.is_set():
        return JSONResponse({"error": "Invalid or expired session."}, status_code=HTTPStatus.NOT_FOUND)
    return StreamingResponse(
        gen_mjpeg(session),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )

@app.post("/camera/stream/terminate")
async def terminate_stream(request: Request):
    # Accept session_id from body or query param
    data = await request.json() if request.headers.get('content-type') == 'application/json' else {}
    session_id = (
        data.get("session_id") 
        or request.query_params.get("session_id")
    )
    if not session_id:
        return JSONResponse({"error": "session_id required."}, status_code=HTTPStatus.BAD_REQUEST)
    with sessions_lock:
        session = sessions.pop(session_id, None)
    if session:
        session.stop()
        return JSONResponse({"result": "Session terminated."}, status_code=HTTPStatus.OK)
    return JSONResponse({"error": "Session not found."}, status_code=HTTPStatus.NOT_FOUND)

if __name__ == "__main__":
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)