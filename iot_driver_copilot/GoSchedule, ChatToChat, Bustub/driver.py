import os
import uvicorn
from fastapi import FastAPI, Request, Response, Header, status, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from typing import Optional

# Read configuration from environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", "8000"))
DEVICE_SCHEME = os.environ.get("DEVICE_SCHEME", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8888"))
WS_PORT = int(os.environ.get("WS_PORT", "8765"))

DEVICE_BASE = f"{DEVICE_SCHEME}://{DEVICE_HOST}:{DEVICE_PORT}"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = httpx.AsyncClient(timeout=30)

def _auth_header(authorization: Optional[str]):
    if authorization:
        return {"Authorization": authorization}
    return {}

@app.post("/session/login")
async def session_login(request: Request):
    body = await request.json()
    url = f"{DEVICE_BASE}/session/login"
    async with httpx.AsyncClient() as ac:
        resp = await ac.post(url, json=body)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.post("/session/logout")
async def session_logout(Authorization: Optional[str] = Header(None)):
    url = f"{DEVICE_BASE}/session/logout"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.post(url, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.get("/schedules")
async def get_schedules(Authorization: Optional[str] = Header(None)):
    url = f"{DEVICE_BASE}/schedules"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.get(url, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.post("/schedules")
async def create_schedule(request: Request, Authorization: Optional[str] = Header(None)):
    body = await request.json()
    url = f"{DEVICE_BASE}/schedules"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.post(url, json=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.get("/posts")
async def get_posts(request: Request, Authorization: Optional[str] = Header(None)):
    params = dict(request.query_params)
    url = f"{DEVICE_BASE}/posts"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.get(url, params=params, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.post("/posts")
async def create_post(request: Request, Authorization: Optional[str] = Header(None)):
    body = await request.json()
    url = f"{DEVICE_BASE}/posts"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.post(url, json=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.get("/search")
async def search(request: Request, Authorization: Optional[str] = Header(None)):
    params = dict(request.query_params)
    url = f"{DEVICE_BASE}/search"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.get(url, params=params, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.get("/chats/{chatId}/messages")
async def get_chat_messages(chatId: str, Authorization: Optional[str] = Header(None)):
    url = f"{DEVICE_BASE}/chats/{chatId}/messages"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.get(url, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.post("/chats/{chatId}/messages")
async def send_chat_message(chatId: str, request: Request, Authorization: Optional[str] = Header(None)):
    body = await request.json()
    url = f"{DEVICE_BASE}/chats/{chatId}/messages"
    headers = _auth_header(Authorization)
    async with httpx.AsyncClient() as ac:
        resp = await ac.post(url, json=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=resp.headers, media_type="application/json")

@app.websocket("/ws/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    await websocket.accept()
    target_url = f"ws://{DEVICE_HOST}:{WS_PORT}/{path}"
    try:
        async with httpx.AsyncClient() as ac:
            async with ac.stream("GET", target_url) as ws_stream:
                while True:
                    data = await websocket.receive_text()
                    await ws_stream.send_bytes(data.encode())
                    async for chunk in ws_stream.aiter_bytes():
                        await websocket.send_text(chunk.decode())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

if __name__ == "__main__":
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT)