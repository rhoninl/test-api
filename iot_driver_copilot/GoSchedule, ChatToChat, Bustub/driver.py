import os
import json
import asyncio
from functools import wraps
from urllib.parse import urljoin, urlencode

from aiohttp import web, ClientSession, WSMsgType

# Environment-based configuration
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", "8080"))
DEVICE_PROTO = os.environ.get("DEVICE_PROTO", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

# Compose backend base URL
BACKEND_BASE = f"{DEVICE_PROTO}://{DEVICE_HOST}:{DEVICE_PORT}"

# Helper: token storage (in-memory, per server instance)
sessions = {}

def require_token(handler):
    @wraps(handler)
    async def wrapper(request):
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            return web.json_response({"error": "Missing or invalid Authorization header"}, status=401)
        token = auth.split(" ", 1)[1]
        if token not in sessions.values():
            return web.json_response({"error": "Invalid or expired token"}, status=401)
        request["token"] = token
        return await handler(request)
    return wrapper

async def proxy_request(request, method, path, with_token=False, json_body=True, query_params=None):
    url = urljoin(BACKEND_BASE, path)
    if query_params:
        url += '?' + urlencode(query_params)
    headers = {}
    if with_token:
        token = request.headers.get("Authorization", "")
        if token:
            headers["Authorization"] = token
    data = await request.json() if json_body and request.can_read_body else None
    async with ClientSession() as session:
        async with session.request(method, url, json=data, headers=headers, params=None if not query_params else query_params) as resp:
            try:
                resp_data = await resp.json()
            except Exception:
                resp_data = await resp.text()
            return web.Response(
                status=resp.status,
                content_type=resp.content_type,
                text=json.dumps(resp_data) if resp.content_type == "application/json" else resp_data
            )

# HTTP API routes
routes = web.RouteTableDef()

@routes.post("/session/login")
async def login(request):
    async with ClientSession() as session:
        url = urljoin(BACKEND_BASE, "/session/login")
        body = await request.json()
        async with session.post(url, json=body) as resp:
            data = await resp.json()
            if resp.status == 200 and "token" in data:
                sessions[body.get("username", "")] = data["token"]
            return web.json_response(data, status=resp.status)

@routes.post("/session/logout")
@require_token
async def logout(request):
    token = request["token"]
    async with ClientSession() as session:
        url = urljoin(BACKEND_BASE, "/session/logout")
        headers = {"Authorization": f"Bearer {token}"}
        async with session.post(url, headers=headers) as resp:
            data = await resp.json()
            # Remove the token from local session storage
            for k,v in list(sessions.items()):
                if v == token:
                    del sessions[k]
            return web.json_response(data, status=resp.status)

@routes.get("/schedules")
@require_token
async def get_schedules(request):
    return await proxy_request(request, "GET", "/schedules", with_token=True, json_body=False, query_params=request.rel_url.query)

@routes.post("/schedules")
@require_token
async def create_schedule(request):
    return await proxy_request(request, "POST", "/schedules", with_token=True)

@routes.get("/posts")
@require_token
async def get_posts(request):
    return await proxy_request(request, "GET", "/posts", with_token=True, json_body=False, query_params=request.rel_url.query)

@routes.post("/posts")
@require_token
async def create_post(request):
    return await proxy_request(request, "POST", "/posts", with_token=True)

@routes.get("/search")
@require_token
async def search(request):
    return await proxy_request(request, "GET", "/search", with_token=True, json_body=False, query_params=request.rel_url.query)

@routes.get("/chats/{chatId}/messages")
@require_token
async def get_chat_messages(request):
    chat_id = request.match_info["chatId"]
    path = f"/chats/{chat_id}/messages"
    return await proxy_request(request, "GET", path, with_token=True, json_body=False, query_params=request.rel_url.query)

@routes.post("/chats/{chatId}/messages")
@require_token
async def send_chat_message(request):
    chat_id = request.match_info["chatId"]
    path = f"/chats/{chat_id}/messages"
    return await proxy_request(request, "POST", path, with_token=True)

# WebSocket proxy endpoint (browser and CLI compatible)
@routes.get("/ws/{tail:.*}")
async def websocket_proxy(request):
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    ws_path = request.match_info.get('tail', '')
    ws_url = f"{DEVICE_PROTO.replace('http', 'ws')}://{DEVICE_HOST}:{DEVICE_PORT}/{ws_path}"
    # Forward Authorization header if present
    headers = {}
    if "Authorization" in request.headers:
        headers["Authorization"] = request.headers["Authorization"]

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
                ws_forward(ws_client, ws_server),
            )
    return ws_server

app = web.Application()
app.add_routes(routes)

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)