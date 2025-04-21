import os
import json
import asyncio
from urllib.parse import urlencode
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Response, Header, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# Environment Variables
DEVICE_HOST = os.getenv('DEVICE_HOST', 'localhost')
DEVICE_PORT = int(os.getenv('DEVICE_PORT', 8000))
DEVICE_PROTOCOL = os.getenv('DEVICE_PROTOCOL', 'http').lower()
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', 8080))
SERVER_HTTP_PORT = int(os.getenv('SERVER_HTTP_PORT', SERVER_PORT))  # Only HTTP is implemented

# Compose device base URL
DEVICE_BASE = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def proxy_request(
    method: str,
    path: str,
    headers: Dict[str, str],
    query: Optional[Dict[str, Any]] = None,
    body: Any = None,
    stream: bool = False
):
    url = f"{DEVICE_BASE}{path}"
    if query:
        url += '?' + urlencode(query, doseq=True)
    async with httpx.AsyncClient() as client:
        req_headers = {k: v for k, v in headers.items() if k.lower() != 'host'}
        if stream:
            resp = await client.request(method, url, headers=req_headers, content=body, stream=True)
            return resp
        else:
            resp = await client.request(method, url, headers=req_headers, content=body)
            return resp

# --- API Endpoints ---

@app.post("/session/login")
async def session_login(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    resp = await proxy_request("POST", "/session/login", headers, body=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.post("/session/logout")
async def session_logout(request: Request, authorization: Optional[str] = Header(None)):
    body = await request.body()
    headers = dict(request.headers)
    # Ensure Authorization header is passed
    if authorization:
        headers['Authorization'] = authorization
    resp = await proxy_request("POST", "/session/logout", headers, body=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.get("/schedules")
async def get_schedules(request: Request):
    headers = dict(request.headers)
    query = dict(request.query_params)
    resp = await proxy_request("GET", "/schedules", headers, query=query)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.post("/schedules")
async def post_schedules(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    resp = await proxy_request("POST", "/schedules", headers, body=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.get("/posts")
async def get_posts(request: Request):
    headers = dict(request.headers)
    query = dict(request.query_params)
    resp = await proxy_request("GET", "/posts", headers, query=query)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.post("/posts")
async def post_posts(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    resp = await proxy_request("POST", "/posts", headers, body=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.get("/search")
async def get_search(request: Request):
    headers = dict(request.headers)
    query = dict(request.query_params)
    resp = await proxy_request("GET", "/search", headers, query=query)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.get("/chats/{chatId}/messages")
async def get_chat_messages(request: Request, chatId: str):
    headers = dict(request.headers)
    query = dict(request.query_params)
    resp = await proxy_request("GET", f"/chats/{chatId}/messages", headers, query=query)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

@app.post("/chats/{chatId}/messages")
async def post_chat_message(request: Request, chatId: str):
    body = await request.body()
    headers = dict(request.headers)
    resp = await proxy_request("POST", f"/chats/{chatId}/messages", headers, body=body)
    return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))

# WebSocket Proxy (for chat or real-time features)
@app.websocket("/ws/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    await websocket.accept()
    # Proxy to device WebSocket
    device_ws_url = f"{DEVICE_PROTOCOL.replace('http', 'ws')}://{DEVICE_HOST}:{DEVICE_PORT}/{path}"
    async with httpx.AsyncClient() as client:
        try:
            async with client.ws_connect(device_ws_url) as device_ws:
                async def to_device():
                    try:
                        while True:
                            msg = await websocket.receive_text()
                            await device_ws.send_text(msg)
                    except WebSocketDisconnect:
                        await device_ws.close()
                    except Exception:
                        await device_ws.close()

                async def to_client():
                    try:
                        while True:
                            msg = await device_ws.receive_text()
                            await websocket.send_text(msg)
                    except Exception:
                        await websocket.close()

                await asyncio.gather(to_device(), to_client())
        except Exception:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

# For browser/command-line access: root endpoint
@app.get("/")
async def root():
    return JSONResponse({
        "message": "GoSchedule, ChatToChat, Bustub Device HTTP Driver",
        "endpoints": [
            "/session/login [POST]",
            "/session/logout [POST]",
            "/schedules [GET, POST]",
            "/posts [GET, POST]",
            "/search [GET]",
            "/chats/{chatId}/messages [GET, POST]",
            "/ws/{path} [WebSocket Proxy]"
        ],
        "device_base": DEVICE_BASE
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_HTTP_PORT)