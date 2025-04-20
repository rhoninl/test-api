import os
import json
import asyncio
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException, status, Header, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# Environment variables for configuration
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http").lower()
DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# FastAPI app setup
app = FastAPI(title="GoSchedule ChatToChat Bustub Driver")

# Allow CORS for browser and CLI access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- AUTH HANDLING ---

async def get_auth_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header missing")
    return authorization

# --- SESSION LOGIN ---

@app.post("/session/login")
async def session_login(request: Request):
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{DEVICE_BASE_URL}/session/login", json=body)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

# --- SESSION LOGOUT ---

@app.post("/session/logout")
async def session_logout(authorization: str = Depends(get_auth_token)):
    headers = {"Authorization": authorization}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{DEVICE_BASE_URL}/session/logout", headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

# --- FETCH SCHEDULES ---

@app.get("/schedules")
async def get_schedules(authorization: str = Depends(get_auth_token)):
    headers = {"Authorization": authorization}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DEVICE_BASE_URL}/schedules", headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

@app.post("/schedules")
async def create_schedule(request: Request, authorization: str = Depends(get_auth_token)):
    body = await request.json()
    headers = {"Authorization": authorization}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{DEVICE_BASE_URL}/schedules", json=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

# --- POSTS ---

@app.get("/posts")
async def get_posts(request: Request, authorization: str = Depends(get_auth_token)):
    headers = {"Authorization": authorization}
    params = dict(request.query_params)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DEVICE_BASE_URL}/posts", headers=headers, params=params)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

@app.post("/posts")
async def create_post(request: Request, authorization: str = Depends(get_auth_token)):
    body = await request.json()
    headers = {"Authorization": authorization}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{DEVICE_BASE_URL}/posts", json=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

# --- SEARCH ---

@app.get("/search")
async def search(request: Request, authorization: str = Depends(get_auth_token)):
    headers = {"Authorization": authorization}
    params = dict(request.query_params)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DEVICE_BASE_URL}/search", headers=headers, params=params)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

# --- CHATS ---

@app.get("/chats/{chatId}/messages")
async def get_chat_messages(chatId: str, authorization: str = Depends(get_auth_token)):
    headers = {"Authorization": authorization}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DEVICE_BASE_URL}/chats/{chatId}/messages", headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

@app.post("/chats/{chatId}/messages")
async def send_chat_message(chatId: str, request: Request, authorization: str = Depends(get_auth_token)):
    body = await request.json()
    headers = {"Authorization": authorization}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{DEVICE_BASE_URL}/chats/{chatId}/messages", json=body, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get('content-type', 'application/json'))

# --- WEBSOCKET PROXY SUPPORT ---

# Proxy WebSocket connections to backend WebSocket if requested
@app.websocket("/ws/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    await websocket.accept()
    backend_ws_url = f"ws://{DEVICE_HOST}:{DEVICE_PORT}/ws/{path}"
    async with httpx.AsyncClient() as client:
        try:
            async with client.ws_connect(backend_ws_url) as backend_ws:
                async def to_backend():
                    try:
                        while True:
                            data = await websocket.receive_text()
                            await backend_ws.send_text(data)
                    except WebSocketDisconnect:
                        await backend_ws.close()
                    except Exception:
                        await backend_ws.close()
                async def from_backend():
                    try:
                        async for msg in backend_ws.iter_text():
                            await websocket.send_text(msg)
                    except Exception:
                        await websocket.close()
                await asyncio.gather(to_backend(), from_backend())
        except Exception:
            await websocket.close()

# --- MAIN ENTRY POINT ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("driver:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)