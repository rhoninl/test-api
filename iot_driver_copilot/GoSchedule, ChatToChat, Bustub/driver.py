import os
import json
import asyncio
from functools import wraps
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType

# Load configuration from environment variables
DEVICE_HOST = os.environ.get('DEVICE_HOST', '127.0.0.1')
DEVICE_PORT = int(os.environ.get('DEVICE_PORT', 8080))
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 5000))
USE_HTTPS = os.environ.get('USE_HTTPS', 'false').lower() == 'true'
HTTP_TIMEOUT = int(os.environ.get('HTTP_TIMEOUT', 30))

BASE_URL = f"{'https' if USE_HTTPS else 'http'}://{DEVICE_HOST}:{DEVICE_PORT}"

async def proxy_request(request, method, remote_path, path_params=None, query_params=None, json_body=True):
    headers = dict(request.headers)
    # Remove hop-by-hop headers
    headers.pop('Host', None)
    # Compose target URL
    if path_params:
        remote_path = remote_path.format(**path_params)
    url = f"{BASE_URL}{remote_path}"
    if query_params:
        url += '?' + urlencode(query_params)
    # Prepare body
    data = None
    if json_body and request.can_read_body:
        try:
            data = await request.json()
        except Exception:
            data = None
    elif request.can_read_body:
        data = await request.read()
    # Proxy to device REST API
    async with ClientSession(timeout=web.ClientTimeout(total=HTTP_TIMEOUT)) as session:
        async with session.request(method, url, headers=headers, json=data if json_body else None, data=None if json_body else data) as resp:
            body = await resp.read()
            response = web.Response(
                status=resp.status,
                headers={k: v for k, v in resp.headers.items() if k.lower() != 'transfer-encoding'},
                body=body
            )
            return response

def require_auth(handler):
    @wraps(handler)
    async def wrapper(request):
        auth = request.headers.get('Authorization')
        if not auth and request.path != '/session/login':
            return web.json_response({'error': 'Missing Authorization header'}, status=401)
        return await handler(request)
    return wrapper

app = web.Application()

# Session endpoints
@app.route('POST', '/session/login')
async def login(request):
    return await proxy_request(request, 'POST', '/session/login')

@app.route('POST', '/session/logout')
@require_auth
async def logout(request):
    return await proxy_request(request, 'POST', '/session/logout')

# Schedules
@app.route('GET', '/schedules')
@require_auth
async def get_schedules(request):
    return await proxy_request(request, 'GET', '/schedules')

@app.route('POST', '/schedules')
@require_auth
async def create_schedule(request):
    return await proxy_request(request, 'POST', '/schedules')

# Posts
@app.route('GET', '/posts')
@require_auth
async def get_posts(request):
    return await proxy_request(request, 'GET', '/posts', query_params=request.rel_url.query)

@app.route('POST', '/posts')
@require_auth
async def create_post(request):
    return await proxy_request(request, 'POST', '/posts')

# Search
@app.route('GET', '/search')
@require_auth
async def search(request):
    return await proxy_request(request, 'GET', '/search', query_params=request.rel_url.query)

# Chat messages
@app.route('GET', '/chats/{chatId}/messages')
@require_auth
async def get_chat_messages(request):
    return await proxy_request(request, 'GET', '/chats/{chatId}/messages', path_params=request.match_info)

@app.route('POST', '/chats/{chatId}/messages')
@require_auth
async def post_chat_message(request):
    return await proxy_request(request, 'POST', '/chats/{chatId}/messages', path_params=request.match_info)

# WebSocket proxy
@app.route('GET', '/ws/{tail:.*}')
async def ws_proxy(request):
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    tail = request.match_info['tail']
    target_url = f"{'ws' if not USE_HTTPS else 'wss'}://{DEVICE_HOST}:{DEVICE_PORT}/ws/{tail}"
    headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
    async with ClientSession() as session:
        async with session.ws_connect(target_url, headers=headers) as ws_client:
            async def ws_to_client():
                async for msg in ws_client:
                    if msg.type == WSMsgType.TEXT:
                        await ws_server.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_server.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_server.close()
            async def ws_to_server():
                async for msg in ws_server:
                    if msg.type == WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_client.close()
            await asyncio.gather(ws_to_client(), ws_to_server())
    return ws_server

if __name__ == "__main__":
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)