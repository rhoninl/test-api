import os
import json
import asyncio
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType

# Environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
DEVICE_USE_HTTPS = os.environ.get("DEVICE_USE_HTTPS", "false").lower() in ("1", "true", "yes")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))
WEBSOCKET_PORT = int(os.environ.get("WEBSOCKET_PORT", SERVER_PORT))  # same port unless overridden

PROTOCOL = "https" if DEVICE_USE_HTTPS else "http"
DEVICE_BASE_URL = f"{PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# Helper to forward REST requests
async def forward_request(request, path, method=None, require_auth=False, stream=False):
    url = DEVICE_BASE_URL + path
    method = method or request.method
    headers = dict(request.headers)
    if "host" in headers:
        del headers["host"]
    data = None
    if method in ("POST", "PUT", "PATCH"):
        try:
            data = await request.json()
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        except Exception:
            data = await request.read()
    else:
        data = None
    # Auth token forwarding
    if require_auth:
        token = request.headers.get("Authorization")
        if token:
            headers["Authorization"] = token
    params = request.rel_url.query
    async with ClientSession() as session:
        async with session.request(method, url, headers=headers, params=params, data=data) as resp:
            if stream:
                response = web.StreamResponse(status=resp.status, reason=resp.reason, headers=resp.headers)
                await response.prepare(request)
                async for chunk in resp.content.iter_chunked(4096):
                    await response.write(chunk)
                await response.write_eof()
                return response
            else:
                body = await resp.read()
                try:
                    json_body = json.loads(body)
                    return web.json_response(json_body, status=resp.status)
                except Exception:
                    return web.Response(body=body, status=resp.status, content_type=resp.headers.get("Content-Type"))

# RESTful HTTP handlers
routes = web.RouteTableDef()

@routes.post("/session/login")
async def login(request):
    return await forward_request(request, "/session/login", method="POST")

@routes.post("/session/logout")
async def logout(request):
    return await forward_request(request, "/session/logout", method="POST", require_auth=True)

@routes.get("/schedules")
async def get_schedules(request):
    return await forward_request(request, "/schedules", method="GET", require_auth=True)

@routes.post("/schedules")
async def create_schedule(request):
    return await forward_request(request, "/schedules", method="POST", require_auth=True)

@routes.get("/posts")
async def get_posts(request):
    return await forward_request(request, "/posts", method="GET", require_auth=True)

@routes.post("/posts")
async def create_post(request):
    return await forward_request(request, "/posts", method="POST", require_auth=True)

@routes.get("/search")
async def search(request):
    return await forward_request(request, "/search", method="GET", require_auth=True)

@routes.get("/chats/{chatId}/messages")
async def get_chat_messages(request):
    chatId = request.match_info["chatId"]
    return await forward_request(request, f"/chats/{chatId}/messages", method="GET", require_auth=True)

@routes.post("/chats/{chatId}/messages")
async def post_chat_message(request):
    chatId = request.match_info["chatId"]
    return await forward_request(request, f"/chats/{chatId}/messages", method="POST", require_auth=True)

# WebSocket Proxy Handler
async def websocket_handler(request):
    # Accept query param: path=/ws/...
    device_ws_path = request.rel_url.query.get("path", "/ws")
    device_ws_url = f"{'wss' if DEVICE_USE_HTTPS else 'ws'}://{DEVICE_HOST}:{DEVICE_PORT}{device_ws_path}"
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    async with ClientSession() as session:
        try:
            async with session.ws_connect(device_ws_url) as ws_client:
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
        except Exception as e:
            await ws_server.close(message=str(e).encode())
    return ws_server

# Attach WebSocket endpoint, only if WEBSOCKET_PORT is set and nonzero
app = web.Application()
app.add_routes(routes)
app.router.add_get("/wsproxy", websocket_handler)

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)