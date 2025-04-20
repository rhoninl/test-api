import os
import json
import asyncio
import uvicorn
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# Environment variables for configuration
DEVICE_HOST = os.environ.get("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", "8080"))
DEVICE_SCHEME = os.environ.get("DEVICE_SCHEME", "http")  # "http" or "https"
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

# Compose base URL for backend device
DEVICE_BASE_URL = f"{DEVICE_SCHEME}://{DEVICE_HOST}:{DEVICE_PORT}"

app = FastAPI(title="GoSchedule ChatToChat Bustub HTTP Proxy Driver")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store for demo (not for production use)
session_tokens: Dict[str, str] = {}

def get_authorization_header(request: Request) -> Optional[str]:
    return request.headers.get('authorization') or request.headers.get('Authorization')

def get_token_from_header(request: Request) -> Optional[str]:
    auth = get_authorization_header(request)
    if auth and auth.lower().startswith("bearer "):
        return auth[7:]
    return auth

async def proxy_request(
    method: str,
    path: str,
    request: Request,
    path_params: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, str]] = None,
    body: Optional[Any] = None,
    add_auth: bool = True,
) -> JSONResponse:
    url = DEVICE_BASE_URL + path.format(**(path_params or {}))
    headers = dict(request.headers)
    # Remove host and content-length, httpx will set them
    headers.pop('host', None)
    headers.pop('content-length', None)
    token = get_token_from_header(request)
    if add_auth and token:
        headers['Authorization'] = f"Bearer {token}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=query_params,
                json=body
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return JSONResponse(
            status_code=resp.status_code,
            content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        )

@app.post("/session/login")
async def session_login(request: Request):
    body = await request.json()
    resp = await proxy_request("POST", "/session/login", request, body=body, add_auth=False)
    # Optionally keep a local session if needed
    try:
        token = resp.body.decode()
        token = json.loads(token).get("token")
        if token:
            session_tokens[token] = "active"
    except Exception:
        pass
    return resp

@app.post("/session/logout")
async def session_logout(request: Request):
    resp = await proxy_request("POST", "/session/logout", request)
    token = get_token_from_header(request)
    if token and token in session_tokens:
        del session_tokens[token]
    return resp

@app.get("/schedules")
async def get_schedules(request: Request):
    return await proxy_request("GET", "/schedules", request)

@app.post("/schedules")
async def create_schedule(request: Request):
    body = await request.json()
    return await proxy_request("POST", "/schedules", request, body=body)

@app.get("/posts")
async def get_posts(request: Request):
    query_params = dict(request.query_params)
    return await proxy_request("GET", "/posts", request, query_params=query_params)

@app.post("/posts")
async def create_post(request: Request):
    body = await request.json()
    return await proxy_request("POST", "/posts", request, body=body)

@app.get("/search")
async def search(request: Request):
    query_params = dict(request.query_params)
    return await proxy_request("GET", "/search", request, query_params=query_params)

@app.get("/chats/{chatId}/messages")
async def get_chat_messages(chatId: str, request: Request):
    return await proxy_request("GET", f"/chats/{chatId}/messages", request, path_params={"chatId": chatId})

@app.post("/chats/{chatId}/messages")
async def send_chat_message(chatId: str, request: Request):
    body = await request.json()
    return await proxy_request("POST", f"/chats/{chatId}/messages", request, path_params={"chatId": chatId}, body=body)

# WebSocket proxy example for chat (if backend supports WebSocket natively)
@app.websocket("/ws/chats/{chatId}")
async def websocket_chat_proxy(websocket: WebSocket, chatId: str):
    await websocket.accept()
    backend_url = f"{DEVICE_SCHEME}://{DEVICE_HOST}:{DEVICE_PORT}/ws/chats/{chatId}"
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("GET", backend_url) as backend_stream:
                async for chunk in backend_stream.aiter_bytes():
                    await websocket.send_bytes(chunk)
        except Exception as exc:
            await websocket.close(code=1011)

if __name__ == "__main__":
    uvicorn.run("main:app", host=SERVER_HOST, port=SERVER_PORT)