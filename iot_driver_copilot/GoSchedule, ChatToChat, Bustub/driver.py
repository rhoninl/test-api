import os
import json
import asyncio
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# Environment variables for configuration
DEVICE_HTTP_HOST = os.getenv('DEVICE_HTTP_HOST', '127.0.0.1')
DEVICE_HTTP_PORT = int(os.getenv('DEVICE_HTTP_PORT', '8000'))
DEVICE_API_BASE = os.getenv('DEVICE_API_BASE', 'http://127.0.0.1:9000')
DEVICE_WS_BASE = os.getenv('DEVICE_WS_BASE', 'ws://127.0.0.1:9001')
DEVICE_API_TIMEOUT = int(os.getenv('DEVICE_API_TIMEOUT', '10'))

# FastAPI app
app = FastAPI()

# Enable CORS if needed for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Utility: Forward REST API request to backend
async def forward_api_request(method, path, headers=None, params=None, data=None, json_body=None):
    url = DEVICE_API_BASE + path
    async with httpx.AsyncClient(timeout=DEVICE_API_TIMEOUT) as client:
        req_headers = dict(headers or {})
        if 'host' in req_headers:
            req_headers.pop('host')
        response = await client.request(
            method=method,
            url=url,
            headers=req_headers,
            params=params,
            data=data,
            json=json_body
        )
        return JSONResponse(
            status_code=response.status_code,
            content=response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
        )

# 1. Authenticate a user (login)
@app.post("/session/login")
async def api_session_login(request: Request):
    body = await request.json()
    return await forward_api_request('POST', '/session/login', headers=request.headers, json_body=body)

# 2. Invalidate session token (logout)
@app.post("/session/logout")
async def api_session_logout(request: Request):
    auth = request.headers.get("Authorization")
    if not auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    return await forward_api_request('POST', '/session/logout', headers=request.headers)

# 3. Fetch scheduled tasks
@app.get("/schedules")
async def api_get_schedules(request: Request):
    return await forward_api_request('GET', '/schedules', headers=request.headers, params=dict(request.query_params))

# 4. Create new task
@app.post("/schedules")
async def api_create_schedule(request: Request):
    body = await request.json()
    return await forward_api_request('POST', '/schedules', headers=request.headers, json_body=body)

# 5. Retrieve recent posts
@app.get("/posts")
async def api_get_posts(request: Request):
    return await forward_api_request('GET', '/posts', headers=request.headers, params=dict(request.query_params))

# 6. Submit new post
@app.post("/posts")
async def api_create_post(request: Request):
    body = await request.json()
    return await forward_api_request('POST', '/posts', headers=request.headers, json_body=body)

# 7. Global search
@app.get("/search")
async def api_search(request: Request):
    return await forward_api_request('GET', '/search', headers=request.headers, params=dict(request.query_params))

# 8. Load message history for a chat
@app.get("/chats/{chatId}/messages")
async def api_get_chat_messages(chatId: str, request: Request):
    path = f"/chats/{chatId}/messages"
    return await forward_api_request('GET', path, headers=request.headers, params=dict(request.query_params))

# 9. Send a new chat message
@app.post("/chats/{chatId}/messages")
async def api_post_chat_message(chatId: str, request: Request):
    path = f"/chats/{chatId}/messages"
    body = await request.json()
    return await forward_api_request('POST', path, headers=request.headers, json_body=body)

# WebSocket proxy: Proxy client <-> backend WebSocket stream
@app.websocket("/ws/proxy/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str):
    # Accept client WebSocket connection
    await websocket.accept()
    backend_ws_url = DEVICE_WS_BASE.rstrip('/') + '/' + path
    try:
        async with httpx.AsyncClient() as client:
            async with client.websocket_connect(backend_ws_url) as backend_ws:
                async def client_to_backend():
                    try:
                        while True:
                            msg = await websocket.receive_text()
                            await backend_ws.send_text(msg)
                    except WebSocketDisconnect:
                        await backend_ws.aclose()
                    except Exception:
                        pass

                async def backend_to_client():
                    try:
                        while True:
                            msg = await backend_ws.receive_text()
                            await websocket.send_text(msg)
                    except Exception:
                        await websocket.close()

                await asyncio.gather(client_to_backend(), backend_to_client())
    except Exception:
        await websocket.close(code=1001)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=DEVICE_HTTP_HOST,
        port=DEVICE_HTTP_PORT,
        reload=False,
        access_log=True
    )