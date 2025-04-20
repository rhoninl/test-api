import os
import json
import asyncio
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType

# Config from environment variables
DEVICE_HOST = os.environ.get('DEVICE_HOST', 'localhost')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8000')
DEVICE_SCHEME = os.environ.get('DEVICE_SCHEME', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# Compose base device URL
DEVICE_BASE = f"{DEVICE_SCHEME}://{DEVICE_HOST}:{DEVICE_PORT}"

routes = web.RouteTableDef()

# ---- Session Management ----
@routes.post('/session/login')
async def login(request):
    data = await request.json()
    async with ClientSession() as sess:
        async with sess.post(f"{DEVICE_BASE}/session/login", json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

@routes.post('/session/logout')
async def logout(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    async with ClientSession() as sess:
        async with sess.post(f"{DEVICE_BASE}/session/logout", headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

# ---- Schedules ----
@routes.get('/schedules')
async def get_schedules(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    async with ClientSession() as sess:
        async with sess.get(f"{DEVICE_BASE}/schedules", headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

@routes.post('/schedules')
async def create_schedule(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    data = await request.json()
    async with ClientSession() as sess:
        async with sess.post(f"{DEVICE_BASE}/schedules", headers=headers, json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

# ---- Posts ----
@routes.get('/posts')
async def get_posts(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    params = request.rel_url.query
    url = f"{DEVICE_BASE}/posts"
    if params:
        url += f"?{urlencode(params)}"
    async with ClientSession() as sess:
        async with sess.get(url, headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

@routes.post('/posts')
async def create_post(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    data = await request.json()
    async with ClientSession() as sess:
        async with sess.post(f"{DEVICE_BASE}/posts", headers=headers, json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

# ---- Search ----
@routes.get('/search')
async def search(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    params = request.rel_url.query
    url = f"{DEVICE_BASE}/search"
    if params:
        url += f"?{urlencode(params)}"
    async with ClientSession() as sess:
        async with sess.get(url, headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

# ---- Chats: Proxy chat messages and send message ----
@routes.get('/chats/{chatId}/messages')
async def get_chat_messages(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    chat_id = request.match_info['chatId']
    async with ClientSession() as sess:
        async with sess.get(f"{DEVICE_BASE}/chats/{chat_id}/messages", headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

@routes.post('/chats/{chatId}/messages')
async def post_chat_message(request):
    headers = {}
    if 'Authorization' in request.headers:
        headers['Authorization'] = request.headers['Authorization']
    chat_id = request.match_info['chatId']
    data = await request.json()
    async with ClientSession() as sess:
        async with sess.post(f"{DEVICE_BASE}/chats/{chat_id}/messages", headers=headers, json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers={'Content-Type': resp.headers.get('Content-Type', 'application/json')})

# ---- WebSocket Proxying (for browser and cmd use) ----
@routes.get('/wsproxy/{ws_path:.*}')
async def ws_proxy(request):
    # Extract WebSocket path to proxy, e.g. /wsproxy/chat
    ws_path = request.match_info['ws_path']
    # Proxy to the device's WebSocket endpoint
    device_ws_url = f"{'wss' if DEVICE_SCHEME == 'https' else 'ws'}://{DEVICE_HOST}:{DEVICE_PORT}/{ws_path}"
    client_ws = web.WebSocketResponse()
    await client_ws.prepare(request)

    async with ClientSession() as sess:
        async with sess.ws_connect(device_ws_url) as device_ws:
            async def ws_to_device():
                async for msg in client_ws:
                    if msg.type == WSMsgType.TEXT:
                        await device_ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await device_ws.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await device_ws.close()
            async def device_to_ws():
                async for msg in device_ws:
                    if msg.type == WSMsgType.TEXT:
                        await client_ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await client_ws.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await client_ws.close()
            await asyncio.gather(ws_to_device(), device_to_ws())
    return client_ws

app = web.Application()
app.add_routes(routes)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)