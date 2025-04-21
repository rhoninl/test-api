import os
import json
import asyncio
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType

# Load configuration from environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "80")
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http").lower()
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Compose base URL for backend device
if DEVICE_PROTOCOL == "https":
    BASE_URL = f"https://{DEVICE_HOST}:{DEVICE_PORT}"
else:
    BASE_URL = f"http://{DEVICE_HOST}:{DEVICE_PORT}"

routes = web.RouteTableDef()

# Session store for tokens (in-memory, for demo purposes)
SESSION_TOKENS = {}

def get_auth_header(request):
    auth = request.headers.get("Authorization")
    if not auth:
        return {}
    return {"Authorization": auth}

# --- REST API Proxy Endpoints ---

@routes.post("/session/login")
async def session_login(request):
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{BASE_URL}/session/login", json=data) as resp:
            res_data = await resp.json()
            token = res_data.get("token")
            if token:
                SESSION_TOKENS[token] = True
            return web.json_response(res_data, status=resp.status)

@routes.post("/session/logout")
async def session_logout(request):
    headers = get_auth_header(request)
    async with ClientSession() as session:
        async with session.post(f"{BASE_URL}/session/logout", headers=headers) as resp:
            res_data = await resp.json()
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token in SESSION_TOKENS:
                del SESSION_TOKENS[token]
            return web.json_response(res_data, status=resp.status)

@routes.get("/schedules")
async def get_schedules(request):
    headers = get_auth_header(request)
    async with ClientSession() as session:
        async with session.get(f"{BASE_URL}/schedules", headers=headers) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

@routes.post("/schedules")
async def post_schedules(request):
    headers = get_auth_header(request)
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{BASE_URL}/schedules", headers=headers, json=data) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

@routes.get("/posts")
async def get_posts(request):
    headers = get_auth_header(request)
    params = request.rel_url.query
    url = f"{BASE_URL}/posts"
    if params:
        url += f"?{urlencode(params)}"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

@routes.post("/posts")
async def post_posts(request):
    headers = get_auth_header(request)
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{BASE_URL}/posts", headers=headers, json=data) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

@routes.get("/search")
async def get_search(request):
    headers = get_auth_header(request)
    params = request.rel_url.query
    url = f"{BASE_URL}/search"
    if params:
        url += f"?{urlencode(params)}"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

@routes.get("/chats/{chatId}/messages")
async def get_chat_messages(request):
    headers = get_auth_header(request)
    chat_id = request.match_info["chatId"]
    url = f"{BASE_URL}/chats/{chat_id}/messages"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

@routes.post("/chats/{chatId}/messages")
async def post_chat_messages(request):
    headers = get_auth_header(request)
    chat_id = request.match_info["chatId"]
    data = await request.json()
    url = f"{BASE_URL}/chats/{chat_id}/messages"
    async with ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            res_data = await resp.json()
            return web.json_response(res_data, status=resp.status)

# --- WebSocket Proxy Example (if device supports WS at /ws) ---
@routes.get("/wsproxy")
async def ws_proxy(request):
    # Proxies between client and device WebSocket endpoint
    ws_from_client = web.WebSocketResponse()
    await ws_from_client.prepare(request)

    device_ws_url = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}/ws"
    async with ClientSession() as session:
        async with session.ws_connect(device_ws_url) as ws_to_device:

            async def client_to_device():
                async for msg in ws_from_client:
                    if msg.type == WSMsgType.TEXT:
                        await ws_to_device.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_to_device.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_to_device.close()

            async def device_to_client():
                async for msg in ws_to_device:
                    if msg.type == WSMsgType.TEXT:
                        await ws_from_client.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_from_client.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_from_client.close()

            await asyncio.gather(client_to_device(), device_to_client())
    return ws_from_client

# --- App Setup ---

app = web.Application()
app.add_routes(routes)

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)