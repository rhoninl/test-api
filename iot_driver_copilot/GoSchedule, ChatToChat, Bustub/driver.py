import os
import json
import asyncio
from aiohttp import web, ClientSession, WSMsgType

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '127.0.0.1')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8000')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')  # http or https
WS_ENABLED = os.environ.get('WS_ENABLED', 'false').lower() == 'true'
WS_PORT = int(os.environ.get('WS_PORT', '8765')) if WS_ENABLED else None

DEVICE_BASE = f"{DEVICE_PROTOCOL}://{DEVICE_IP}:{DEVICE_PORT}"

routes = web.RouteTableDef()

def get_auth_header(request):
    token = request.headers.get('Authorization')
    if token:
        return {'Authorization': token}
    return {}

@routes.post('/session/login')
async def login(request):
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f'{DEVICE_BASE}/session/login', json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.post('/session/logout')
async def logout(request):
    headers = get_auth_header(request)
    async with ClientSession() as session:
        async with session.post(f'{DEVICE_BASE}/session/logout', headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.get('/schedules')
async def get_schedules(request):
    headers = get_auth_header(request)
    async with ClientSession() as session:
        async with session.get(f'{DEVICE_BASE}/schedules', headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.post('/schedules')
async def post_schedules(request):
    headers = get_auth_header(request)
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f'{DEVICE_BASE}/schedules', headers=headers, json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.get('/posts')
async def get_posts(request):
    headers = get_auth_header(request)
    params = request.query_string
    url = f'{DEVICE_BASE}/posts'
    if params:
        url += f'?{params}'
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.post('/posts')
async def post_posts(request):
    headers = get_auth_header(request)
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f'{DEVICE_BASE}/posts', headers=headers, json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.get('/search')
async def get_search(request):
    headers = get_auth_header(request)
    params = request.query_string
    url = f'{DEVICE_BASE}/search'
    if params:
        url += f'?{params}'
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.get('/chats/{chatId}/messages')
async def get_chat_messages(request):
    headers = get_auth_header(request)
    chat_id = request.match_info['chatId']
    async with ClientSession() as session:
        async with session.get(f'{DEVICE_BASE}/chats/{chat_id}/messages', headers=headers) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

@routes.post('/chats/{chatId}/messages')
async def post_chat_message(request):
    headers = get_auth_header(request)
    chat_id = request.match_info['chatId']
    data = await request.json()
    async with ClientSession() as session:
        async with session.post(f'{DEVICE_BASE}/chats/{chat_id}/messages', headers=headers, json=data) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body, content_type=resp.content_type)

# Optional WebSocket Proxy if enabled
async def websocket_proxy(request):
    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)

    ws_headers = {}
    token = request.headers.get('Authorization')
    if token:
        ws_headers['Authorization'] = token

    device_ws_url = f"{'wss' if DEVICE_PROTOCOL=='https' else 'ws'}://{DEVICE_IP}:{DEVICE_PORT}/ws"
    async with ClientSession() as session:
        async with session.ws_connect(device_ws_url, headers=ws_headers) as ws_server:
            async def from_client():
                async for msg in ws_client:
                    if msg.type == WSMsgType.TEXT:
                        await ws_server.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_server.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_server.close()
                        break

            async def from_server():
                async for msg in ws_server:
                    if msg.type == WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_client.close()
                        break

            await asyncio.gather(from_client(), from_server())
    return ws_client

app = web.Application()
app.add_routes(routes)

if WS_ENABLED:
    app.router.add_route('GET', '/ws', websocket_proxy)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)