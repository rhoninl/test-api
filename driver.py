import os
import json
import asyncio
from urllib.parse import urlencode, urljoin
from aiohttp import ClientSession, web, WSMsgType

DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# --- HTTP REST Proxy Handlers ---

async def proxy_request(request, path, method="GET", json_body=False, extra_path_params=None):
    url = urljoin(BASE_URL, path)
    if extra_path_params:
        url = url.format(**extra_path_params)
    headers = dict(request.headers)
    if "Host" in headers:
        del headers["Host"]

    params = dict(request.query)
    data = None
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            data = await request.json()
        except Exception:
            data = await request.text()
    async with ClientSession() as session:
        req_args = {
            "headers": headers,
            "params": params,
        }
        if data:
            if isinstance(data, dict):
                req_args["json"] = data
            else:
                req_args["data"] = data
        async with session.request(method, url, **req_args) as resp:
            body = await resp.read()
            return web.Response(
                status=resp.status,
                headers={k: v for k, v in resp.headers.items() if k.lower() != "transfer-encoding"},
                body=body,
            )

# Login
async def login(request):
    return await proxy_request(request, "/session/login", method="POST")

# Logout
async def logout(request):
    return await proxy_request(request, "/session/logout", method="POST")

# Schedules (GET/POST)
async def get_schedules(request):
    return await proxy_request(request, "/schedules", method="GET")

async def post_schedules(request):
    return await proxy_request(request, "/schedules", method="POST")

# Posts (GET/POST)
async def get_posts(request):
    return await proxy_request(request, "/posts", method="GET")

async def post_posts(request):
    return await proxy_request(request, "/posts", method="POST")

# Search
async def get_search(request):
    return await proxy_request(request, "/search", method="GET")

# Chats (GET/POST)
async def get_chat_messages(request):
    chat_id = request.match_info['chatId']
    return await proxy_request(request, f"/chats/{chat_id}/messages", method="GET", extra_path_params={"chatId": chat_id})

async def post_chat_messages(request):
    chat_id = request.match_info['chatId']
    return await proxy_request(request, f"/chats/{chat_id}/messages", method="POST", extra_path_params={"chatId": chat_id})

# --- WebSocket Proxy Handler ---
async def ws_handler(request):
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    ws_url = f"{DEVICE_PROTOCOL.replace('http', 'ws')}://{DEVICE_HOST}:{DEVICE_PORT}/ws"
    async with ClientSession() as session:
        async with session.ws_connect(ws_url) as ws_client:
            async def ws_to_client():
                async for msg in ws_client:
                    if msg.type == WSMsgType.TEXT:
                        await ws_server.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_server.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_server.close()
                        break

            async def client_to_ws():
                async for msg in ws_server:
                    if msg.type == WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_client.close()
                        break

            await asyncio.gather(ws_to_client(), client_to_ws())

    return ws_server

# --- App Routing and Startup ---

app = web.Application()
app.router.add_post('/session/login', login)
app.router.add_post('/session/logout', logout)
app.router.add_get('/schedules', get_schedules)
app.router.add_post('/schedules', post_schedules)
app.router.add_get('/posts', get_posts)
app.router.add_post('/posts', post_posts)
app.router.add_get('/search', get_search)
app.router.add_get('/chats/{chatId}/messages', get_chat_messages)
app.router.add_post('/chats/{chatId}/messages', post_chat_messages)
app.router.add_get('/ws', ws_handler)  # WebSocket endpoint proxy

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)