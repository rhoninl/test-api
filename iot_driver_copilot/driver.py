import os
import uvicorn
import json
import requests
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Configuration from environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", 80))
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))
SERVER_USE_HTTPS = os.environ.get("SERVER_USE_HTTPS", "false").lower() == "true"

# Construct base URL for device
if DEVICE_PROTOCOL == "http":
    BASE_URL = f"http://{DEVICE_HOST}:{DEVICE_PORT}"
elif DEVICE_PROTOCOL == "https":
    BASE_URL = f"https://{DEVICE_HOST}:{DEVICE_PORT}"
else:
    raise RuntimeError("Unsupported DEVICE_PROTOCOL. Only http and https are supported.")

app = FastAPI(
    title="GoSchedule/ChatToChat/Bustub HTTP Proxy Driver",
    description="HTTP driver to proxy backend software service APIs for GoSchedule, ChatToChat, Bustub.",
    version="1.0.0",
)

# Enable CORS for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _api_headers(token: str = None, extra: dict = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers

def _handle_response(resp):
    try:
        content = resp.json()
    except Exception:
        content = resp.text
    return JSONResponse(status_code=resp.status_code, content=content)

@app.post("/session/login")
async def session_login(request: Request):
    data = await request.json()
    url = f"{BASE_URL}/session/login"
    try:
        resp = requests.post(url, json=data, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/session/logout")
async def session_logout(authorization: str = Header(None)):
    url = f"{BASE_URL}/session/logout"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/schedules")
async def get_schedules(authorization: str = Header(None)):
    url = f"{BASE_URL}/schedules"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/schedules")
async def create_schedule(request: Request, authorization: str = Header(None)):
    data = await request.json()
    url = f"{BASE_URL}/schedules"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/posts")
async def get_posts(request: Request, authorization: str = Header(None)):
    query_string = request.url.query
    url = f"{BASE_URL}/posts"
    if query_string:
        url += f"?{query_string}"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/posts")
async def create_post(request: Request, authorization: str = Header(None)):
    data = await request.json()
    url = f"{BASE_URL}/posts"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/search")
async def search(request: Request, authorization: str = Header(None)):
    query_string = request.url.query
    url = f"{BASE_URL}/search"
    if query_string:
        url += f"?{query_string}"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: str, authorization: str = Header(None)):
    url = f"{BASE_URL}/chats/{chat_id}/messages"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/chats/{chat_id}/messages")
async def post_chat_message(chat_id: str, request: Request, authorization: str = Header(None)):
    data = await request.json()
    url = f"{BASE_URL}/chats/{chat_id}/messages"
    headers = _api_headers(token=authorization.replace("Bearer ", "") if authorization else None)
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        return _handle_response(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        ssl_keyfile=os.environ.get("SSL_KEYFILE") if SERVER_USE_HTTPS else None,
        ssl_certfile=os.environ.get("SSL_CERTFILE") if SERVER_USE_HTTPS else None,
    )