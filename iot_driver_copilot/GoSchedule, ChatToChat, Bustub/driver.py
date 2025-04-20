import os
import json
import asyncio
import aiohttp
from aiohttp import web
import websockets
from urllib.parse import urlencode, urljoin

# Configuration from environment variables
DEVICE_HOST = os.getenv("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", "8080"))
DEVICE_PROTOCOL = os.getenv("DEVICE_PROTOCOL", "http").lower()  # Only http or https supported
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
WEBSOCKET_PORT = int(os.getenv("WEBSOCKET_PORT", "8765"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# Helper for HTTP requests to device backend
async def device_request(method, path, headers=None, params=None, data=None, json_data=None):
    url = urljoin(DEVICE_BASE_URL, path)
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        req_params = {}
        if headers:
            req_params['headers'] = headers
        if params:
            req_params['params'] = params
        if data:
            req_params['data'] = data
        if json_data:
            req_params['json'] = json_data
        async with session.request(method, url, **req_params) as resp:
            resp_data = await resp.read()
            try:
                return resp.status, resp.headers, json.loads(resp_data)
            except Exception:
                return resp.status, resp.headers, resp_data

# HTTP Server with aiohttp
routes = web.RouteTableDef()

@routes.post('/session/login')
async def session_login(request):
    body = await request.json()
    status, headers, data = await device_request(
        "POST", "/session/login", 
        headers={"Content-Type": "application/json"},
        json_data=body
    )
    return web.json_response(data, status=status)

@routes.post('/session/logout')
async def session_logout(request):
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else {}
    status, _, data = await device_request(
        "POST", "/session/logout",
        headers=headers
    )
    return web.json_response(data, status=status)

@routes.get('/schedules')
async def get_schedules(request):
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else {}
    status, _, data = await device_request(
        "GET", "/schedules",
        headers=headers
    )
    return web.json_response(data, status=status)

@routes.post('/schedules')
async def post_schedules(request):
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth, "Content-Type": "application/json"} if auth else {"Content-Type": "application/json"}
    body = await request.json()
    status, _, data = await device_request(
        "POST", "/schedules",
        headers=headers,
        json_data=body
    )
    return web.json_response(data, status=status)

@routes.get('/posts')
async def get_posts(request):
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else {}
    params = request.query
    status, _, data = await device_request(
        "GET", "/posts",
        headers=headers,
        params=params
    )
    return web.json_response(data, status=status)

@routes.post('/posts')
async def post_posts(request):
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth, "Content-Type": "application/json"} if auth else {"Content-Type": "application/json"}
    body = await request.json()
    status, _, data = await device_request(
        "POST", "/posts",
        headers=headers,
        json_data=body
    )
    return web.json_response(data, status=status)

@routes.get('/search')
async def get_search(request):
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else {}
    params = request.query
    status, _, data = await device_request(
        "GET", "/search",
        headers=headers,
        params=params
    )
    return web.json_response(data, status=status)

@routes.get('/chats/{chatId}/messages')
async def get_chat_messages(request):
    chat_id = request.match_info['chatId']
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth} if auth else {}
    path = f"/chats/{chat_id}/messages"
    status, _, data = await device_request(
        "GET", path,
        headers=headers
    )
    return web.json_response(data, status=status)

@routes.post('/chats/{chatId}/messages')
async def post_chat_messages(request):
    chat_id = request.match_info['chatId']
    auth = request.headers.get("Authorization")
    headers = {"Authorization": auth, "Content-Type": "application/json"} if auth else {"Content-Type": "application/json"}
    path = f"/chats/{chat_id}/messages"
    body = await request.json()
    status, _, data = await device_request(
        "POST", path,
        headers=headers,
        json_data=body
    )
    return web.json_response(data, status=status)

# WebSocket proxy implementation for /ws/{chatId}
async def ws_proxy_handler(websocket, path):
    # path: e.g. "/ws/123"
    chat_id = path.split('/')[-1]
    client_protocol = DEVICE_PROTOCOL
    device_ws_url = f"{'wss' if client_protocol == 'https' else 'ws'}://{DEVICE_HOST}:{DEVICE_PORT}/ws/{chat_id}"
    # Forward headers if any
    # Not all devices support custom headers in websocket, but try if possible
    async with websockets.connect(device_ws_url) as device_ws:
        async def client_to_device():
            async for message in websocket:
                await device_ws.send(message)
        async def device_to_client():
            async for message in device_ws:
                await websocket.send(message)
        await asyncio.gather(client_to_device(), device_to_client())

# aiohttp endpoint to discover WebSocket proxy
@routes.get('/ws/{chatId}')
async def ws_upgrade_redirect(request):
    chat_id = request.match_info['chatId']
    ws_host = request.host.split(':')[0]
    ws_url = f"ws://{ws_host}:{WEBSOCKET_PORT}/ws/{chat_id}"
    return web.json_response({"websocket_url": ws_url})

# Serve OpenAPI-style API index
@routes.get('/')
async def index(request):
    return web.json_response({
        "endpoints": [
            {"method": "POST", "path": "/session/login"},
            {"method": "POST", "path": "/session/logout"},
            {"method": "GET", "path": "/schedules"},
            {"method": "POST", "path": "/schedules"},
            {"method": "GET", "path": "/posts"},
            {"method": "POST", "path": "/posts"},
            {"method": "GET", "path": "/search"},
            {"method": "GET", "path": "/chats/{chatId}/messages"},
            {"method": "POST", "path": "/chats/{chatId}/messages"},
            {"method": "GET", "path": "/ws/{chatId} (returns websocket URL)"},
        ]
    })

# Main runner for both HTTP and WebSocket servers
def main():
    app = web.Application()
    app.add_routes(routes)
    loop = asyncio.get_event_loop()

    # HTTP API Server
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, SERVER_HOST, SERVER_PORT)
    loop.run_until_complete(site.start())

    # WebSocket Proxy Server
    async def ws_server():
        async def ws_handler(websocket, path):
            await ws_proxy_handler(websocket, path)
        await websockets.serve(ws_handler, SERVER_HOST, WEBSOCKET_PORT)
    loop.create_task(ws_server())

    print(f"HTTP server running on {SERVER_HOST}:{SERVER_PORT}")
    print(f"WebSocket proxy running on {SERVER_HOST}:{WEBSOCKET_PORT}")
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()