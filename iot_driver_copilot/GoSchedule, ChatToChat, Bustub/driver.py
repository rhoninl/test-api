import os
import json
import asyncio
from functools import wraps
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType

# === Configuration from environment ===
DEVICE_BASE_URL = os.environ.get("DEVICE_BASE_URL", "http://localhost:8000")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# === Utility: Pass-through headers ===
def extract_auth_header(request):
    auth = request.headers.get("Authorization")
    return {"Authorization": auth} if auth else {}

def require_auth(f):
    @wraps(f)
    async def wrapper(request):
        if "Authorization" not in request.headers:
            return web.json_response({"error": "Authorization header required"}, status=401)
        return await f(request)
    return wrapper

# === Session token manager (for browser use) ===
async def set_session_cookie(resp, token):
    resp.set_cookie("session_token", token, httponly=True, path="/")

def get_session_token(request):
    token = request.headers.get("Authorization")
    if not token:
        token = request.cookies.get("session_token")
        if token:
            token = f"Bearer {token}"
    return token

# === API Proxy Handlers ===

async def login(request):
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/session/login", json=data) as resp:
            raw = await resp.json()
            if resp.status == 200 and "token" in raw:
                response = web.json_response(raw)
                await set_session_cookie(response, raw["token"])
                return response
            return web.json_response(raw, status=resp.status)

@require_auth
async def logout(request):
    token = get_session_token(request)
    headers = {"Authorization": token}
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/session/logout", headers=headers) as resp:
            raw = await resp.json()
            response = web.json_response(raw, status=resp.status)
            response.del_cookie("session_token")
            return response

@require_auth
async def get_schedules(request):
    token = get_session_token(request)
    headers = {"Authorization": token}
    async with ClientSession() as session:
        async with session.get(f"{DEVICE_BASE_URL}/schedules", headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

@require_auth
async def post_schedules(request):
    token = get_session_token(request)
    data = await request.json()
    headers = {"Authorization": token}
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/schedules", json=data, headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

@require_auth
async def get_posts(request):
    token = get_session_token(request)
    params = request.rel_url.query
    headers = {"Authorization": token}
    url = f"{DEVICE_BASE_URL}/posts"
    if params:
        url += "?" + urlencode(params)
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

@require_auth
async def post_posts(request):
    token = get_session_token(request)
    data = await request.json()
    headers = {"Authorization": token}
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/posts", json=data, headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

@require_auth
async def search(request):
    token = get_session_token(request)
    params = request.rel_url.query
    headers = {"Authorization": token}
    url = f"{DEVICE_BASE_URL}/search"
    if params:
        url += "?" + urlencode(params)
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

@require_auth
async def get_chat_messages(request):
    token = get_session_token(request)
    chat_id = request.match_info['chatId']
    headers = {"Authorization": token}
    async with ClientSession() as session:
        async with session.get(f"{DEVICE_BASE_URL}/chats/{chat_id}/messages", headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

@require_auth
async def post_chat_messages(request):
    token = get_session_token(request)
    chat_id = request.match_info['chatId']
    data = await request.json()
    headers = {"Authorization": token}
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/chats/{chat_id}/messages", json=data, headers=headers) as resp:
            raw = await resp.json()
            return web.json_response(raw, status=resp.status)

# === WebSocket Proxy Handler ===

async def websocket_handler(request):
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    token = get_session_token(request)
    ws_url = f"{DEVICE_BASE_URL.replace('http', 'ws')}/ws"
    headers = {}
    if token:
        headers["Authorization"] = token

    async with ClientSession() as session:
        async with session.ws_connect(ws_url, headers=headers) as ws_client:
            async def ws_forward(ws_from, ws_to):
                async for msg in ws_from:
                    if msg.type == WSMsgType.TEXT:
                        await ws_to.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_to.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_to.close()
                        break

            await asyncio.gather(
                ws_forward(ws_server, ws_client),
                ws_forward(ws_client, ws_server)
            )
    return ws_server

# === App setup ===

app = web.Application()
app.add_routes([
    web.post('/session/login', login),
    web.post('/session/logout', logout),
    web.get('/schedules', get_schedules),
    web.post('/schedules', post_schedules),
    web.get('/posts', get_posts),
    web.post('/posts', post_posts),
    web.get('/search', search),
    web.get('/chats/{chatId}/messages', get_chat_messages),
    web.post('/chats/{chatId}/messages', post_chat_messages),
    web.get('/ws', websocket_handler)
])

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)