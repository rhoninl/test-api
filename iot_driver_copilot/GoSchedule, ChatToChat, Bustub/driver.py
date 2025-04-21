import os
import json
import asyncio
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType, ClientWebSocketResponse

# Environment variables
DEVICE_API_HOST = os.environ.get("DEVICE_API_HOST", "127.0.0.1")
DEVICE_API_PORT = os.environ.get("DEVICE_API_PORT", "8080")
DEVICE_API_PROTO = os.environ.get("DEVICE_API_PROTO", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

DEVICE_BASE_URL = f"{DEVICE_API_PROTO}://{DEVICE_API_HOST}:{DEVICE_API_PORT}"

routes = web.RouteTableDef()

# ========== RESTful API Proxy ==========

async def get_auth_header(request):
    auth = request.headers.get("Authorization")
    if auth:
        return {"Authorization": auth}
    return {}

@routes.post("/session/login")
async def session_login(request):
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/session/login", json=data) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.post("/session/logout")
async def session_logout(request):
    headers = await get_auth_header(request)
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/session/logout", headers=headers) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.get("/schedules")
async def get_schedules(request):
    headers = await get_auth_header(request)
    async with ClientSession() as session:
        async with session.get(f"{DEVICE_BASE_URL}/schedules", headers=headers) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.post("/schedules")
async def post_schedules(request):
    headers = await get_auth_header(request)
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/schedules", headers=headers, json=data) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.get("/posts")
async def get_posts(request):
    headers = await get_auth_header(request)
    params = request.rel_url.query
    url = f"{DEVICE_BASE_URL}/posts"
    if params:
        url += "?" + urlencode(params)
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.post("/posts")
async def post_posts(request):
    headers = await get_auth_header(request)
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/posts", headers=headers, json=data) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.get("/search")
async def search(request):
    headers = await get_auth_header(request)
    params = request.rel_url.query
    url = f"{DEVICE_BASE_URL}/search"
    if params:
        url += "?" + urlencode(params)
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.get("/chats/{chatId}/messages")
async def get_chat_messages(request):
    headers = await get_auth_header(request)
    chat_id = request.match_info["chatId"]
    url = f"{DEVICE_BASE_URL}/chats/{chat_id}/messages"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

@routes.post("/chats/{chatId}/messages")
async def post_chat_messages(request):
    headers = await get_auth_header(request)
    chat_id = request.match_info["chatId"]
    data = await request.json()
    url = f"{DEVICE_BASE_URL}/chats/{chat_id}/messages"
    async with ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.read()
            return web.Response(body=result, status=resp.status, content_type=resp.headers.get("Content-Type"))

# ========== WebSocket Proxy ==========

@routes.get("/wsproxy/{tail:.*}")
async def websocket_proxy(request):
    # ws://localhost:8000/wsproxy/ws/chat/123
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    tail = request.match_info["tail"]
    params = request.rel_url.query
    remote_path = f"/{tail}"
    if params:
        remote_url = f"{DEVICE_API_PROTO}://{DEVICE_API_HOST}:{DEVICE_API_PORT}{remote_path}?{urlencode(params)}"
    else:
        remote_url = f"{DEVICE_API_PROTO}://{DEVICE_API_HOST}:{DEVICE_API_PORT}{remote_path}"

    headers = await get_auth_header(request)
    async with ClientSession() as session:
        try:
            ws_client: ClientWebSocketResponse = await session.ws_connect(remote_url, headers=headers)
        except Exception as e:
            await ws_server.send_str(json.dumps({"error": str(e)}))
            await ws_server.close()
            return ws_server

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

# ========== App Setup ==========

app = web.Application()
app.add_routes(routes)

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)