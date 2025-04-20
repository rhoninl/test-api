import os
import json
import asyncio
from typing import Dict, Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Header, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '127.0.0.1')
DEVICE_PORT = int(os.environ.get('DEVICE_PORT', '8000'))
DEVICE_SCHEME = os.environ.get('DEVICE_SCHEME', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
HTTP_TIMEOUT = int(os.environ.get('HTTP_TIMEOUT', '10'))

DEVICE_BASE_URL = f"{DEVICE_SCHEME}://{DEVICE_IP}:{DEVICE_PORT}"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- HTTP Client Helper ---

async def device_request(
    method: str,
    path: str,
    *,
    params: Dict[str, Any] = None,
    data: Any = None,
    headers: Dict[str, str] = None,
    token: str = None,
):
    url = f"{DEVICE_BASE_URL}{path}"
    req_headers = headers.copy() if headers else {}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.request(
            method, url, params=params, json=data, headers=req_headers
        )
        return response

# --- API Endpoints ---

@app.post("/session/login")
async def login(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    resp = await device_request("POST", "/session/login", data=body)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/session/logout")
async def logout(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    resp = await device_request("POST", "/session/logout", token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/schedules")
async def get_schedules(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    resp = await device_request("GET", "/schedules", token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/schedules")
async def create_schedule(request: Request, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    resp = await device_request("POST", "/schedules", data=body, token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/posts")
async def get_posts(request: Request, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    params = dict(request.query_params)
    resp = await device_request("GET", "/posts", params=params, token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/posts")
async def create_post(request: Request, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    resp = await device_request("POST", "/posts", data=body, token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/search")
async def search(request: Request, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    params = dict(request.query_params)
    resp = await device_request("GET", "/search", params=params, token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    path = f"/chats/{chat_id}/messages"
    resp = await device_request("GET", path, token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

@app.post("/chats/{chat_id}/messages")
async def send_chat_message(chat_id: str, request: Request, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.split(" ")[-1]
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    path = f"/chats/{chat_id}/messages"
    resp = await device_request("POST", path, data=body, token=token)
    return JSONResponse(status_code=resp.status_code, content=resp.json())

# --- WebSocket Proxy (if backend supports WebSocket for chat) ---

@app.websocket("/ws/chats/{chat_id}")
async def websocket_proxy(websocket: WebSocket, chat_id: str):
    await websocket.accept()
    token = None
    try:
        query = dict(websocket.query_params)
        token = query.get("token")
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        backend_url = f"{DEVICE_SCHEME}://{DEVICE_IP}:{DEVICE_PORT}/ws/chats/{chat_id}"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", backend_url, headers={"Authorization": f"Bearer {token}"}
            ) as backend_ws:
                async def backend_to_client():
                    async for message in backend_ws.aiter_text():
                        await websocket.send_text(message)
                async def client_to_backend():
                    while True:
                        data = await websocket.receive_text()
                        await backend_ws.send_bytes(data.encode("utf-8"))
                await asyncio.gather(backend_to_client(), client_to_backend())
    except WebSocketDisconnect:
        pass
    except Exception:
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("driver:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)