import os
import json
import asyncio
import websockets
from typing import Optional
from urllib.parse import urlencode, urljoin

from fastapi import FastAPI, Request, Response, status, HTTPException, Header
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

DEVICE_IP = os.getenv("DEVICE_IP", "127.0.0.1")
DEVICE_REST_PORT = int(os.getenv("DEVICE_REST_PORT", "8080"))
DEVICE_WS_PORT = int(os.getenv("DEVICE_WS_PORT", "8081"))
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

REST_BASE = f"http://{DEVICE_IP}:{DEVICE_REST_PORT}"
WS_BASE = f"ws://{DEVICE_IP}:{DEVICE_WS_PORT}"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Session Management Helper ---
def extract_auth_token(request: Request):
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return auth_header[7:]

# --- REST Proxy Endpoints ---

@app.post("/session/login")
async def session_login(request: Request):
    body = await request.json()
    async with httpx.AsyncClient() as client:
        r = await client.post(urljoin(REST_BASE, "/session/login"), json=body)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.post("/session/logout")
async def session_logout(request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        r = await client.post(urljoin(REST_BASE, "/session/logout"), headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.get("/schedules")
async def get_schedules(request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(urljoin(REST_BASE, "/schedules"), headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.post("/schedules")
async def create_schedule(request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    body = await request.json()
    async with httpx.AsyncClient() as client:
        r = await client.post(urljoin(REST_BASE, "/schedules"), json=body, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.get("/posts")
async def get_posts(request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    query = request.query_params
    url = urljoin(REST_BASE, "/posts")
    if query:
        url = f"{url}?{urlencode(query)}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.post("/posts")
async def create_post(request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    body = await request.json()
    async with httpx.AsyncClient() as client:
        r = await client.post(urljoin(REST_BASE, "/posts"), json=body, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.get("/search")
async def search(request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    query = request.query_params
    url = urljoin(REST_BASE, "/search")
    if query:
        url = f"{url}?{urlencode(query)}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.get("/chats/{chatId}/messages")
async def get_chat_messages(chatId: str, request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    url = urljoin(REST_BASE, f"/chats/{chatId}/messages")
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))

@app.post("/chats/{chatId}/messages")
async def send_chat_message(chatId: str, request: Request):
    token = extract_auth_token(request)
    headers = {"Authorization": f"Bearer {token}"}
    body = await request.json()
    url = urljoin(REST_BASE, f"/chats/{chatId}/messages")
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=body, headers=headers)
        return Response(r.content, status_code=r.status_code, headers=dict(r.headers))


# --- WebSocket Proxy (convert WS to HTTP stream for browser/commandline) ---

@app.get("/chats/{chatId}/ws-messages")
async def proxy_chat_ws_messages(chatId: str, request: Request):
    """
    HTTP stream (Server-Sent Events) that proxies chat messages from the device's WebSocket.
    Client must provide Authorization header.
    """
    token = extract_auth_token(request)
    ws_url = f"{WS_BASE}/chats/{chatId}/ws-messages"

    async def event_stream():
        try:
            async with websockets.connect(
                ws_url, extra_headers={"Authorization": f"Bearer {token}"}
            ) as websocket:
                while True:
                    msg = await websocket.recv()
                    yield f"data: {msg}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)