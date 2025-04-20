import os
import json
import asyncio
from urllib.parse import urlencode
from aiohttp import web, ClientSession, WSMsgType

# Environment variables for configuration
DEVICE_IP = os.environ.get('DEVICE_IP', '127.0.0.1')
DEVICE_HTTP_PORT = int(os.environ.get('DEVICE_HTTP_PORT', '8080'))
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8000'))

DEVICE_BASE_URL = f"http://{DEVICE_IP}:{DEVICE_HTTP_PORT}"

routes = web.RouteTableDef()

def get_token_from_request(request):
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return None

@routes.post('/session/login')
async def session_login(request):
    payload = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/session/login", json=payload) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.post('/session/logout')
async def session_logout(request):
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/session/logout", headers=headers) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.get('/schedules')
async def get_schedules(request):
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    async with ClientSession() as session:
        async with session.get(f"{DEVICE_BASE_URL}/schedules", headers=headers) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.post('/schedules')
async def create_schedule(request):
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    payload = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/schedules", headers=headers, json=payload) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.get('/posts')
async def get_posts(request):
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    params = request.rel_url.query
    query = urlencode(params)
    url = f"{DEVICE_BASE_URL}/posts"
    if query:
        url += f"?{query}"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.post('/posts')
async def create_post(request):
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    payload = await request.json()
    async with ClientSession() as session:
        async with session.post(f"{DEVICE_BASE_URL}/posts", headers=headers, json=payload) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.get('/search')
async def search(request):
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    params = request.rel_url.query
    query = urlencode(params)
    url = f"{DEVICE_BASE_URL}/search"
    if query:
        url += f"?{query}"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.get('/chats/{chatId}/messages')
async def get_chat_messages(request):
    chatId = request.match_info['chatId']
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    url = f"{DEVICE_BASE_URL}/chats/{chatId}/messages"
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

@routes.post('/chats/{chatId}/messages')
async def post_chat_message(request):
    chatId = request.match_info['chatId']
    token = get_token_from_request(request)
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    payload = await request.json()
    url = f"{DEVICE_BASE_URL}/chats/{chatId}/messages"
    async with ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text()
            return web.Response(text=text, status=resp.status, content_type=resp.headers.get("Content-Type", "application/json"))

# Optional: Proxy WebSocket for chat, if needed.
@routes.get('/ws/chats/{chatId}')
async def websocket_chat_proxy(request):
    chatId = request.match_info['chatId']
    token = get_token_from_request(request)
    target_url = f"ws://{DEVICE_IP}:{DEVICE_HTTP_PORT}/ws/chats/{chatId}"
    headers = {}
    if token:
        headers['Authorization'] = f"Bearer {token}"
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    session = ClientSession()
    async with session.ws_connect(target_url, headers=headers) as ws_client:
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
    await session.close()
    return ws_server

app = web.Application()
app.add_routes(routes)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)