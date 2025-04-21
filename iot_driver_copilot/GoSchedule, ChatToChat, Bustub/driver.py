import os
import json
import asyncio
from urllib.parse import urlencode

from aiohttp import web, ClientSession, WSMsgType

# Configuration from environment variables
DEVICE_HOST = os.environ.get('DEVICE_HOST', '127.0.0.1')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8000')
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')  # http or https
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
TIMEOUT = int(os.environ.get('PROXY_TIMEOUT', '15'))

BACKEND_BASE = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# Helper for auth token
def get_auth_header(request):
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer ') or auth.startswith('Token '):
        return auth
    # Optionally, extract from cookie/session if needed
    return None

# Proxy HTTP requests to backend
async def proxy_request(request, method, path, with_auth=True, stream=False, replace_path_vars=None):
    url = BACKEND_BASE + path
    if replace_path_vars:
        for k, v in replace_path_vars.items():
            url = url.replace('{' + k + '}', str(v))
    headers = {}
    if with_auth:
        auth = get_auth_header(request)
        if auth:
            headers['Authorization'] = auth

    # Forward query params if present
    if request.query_string:
        url = f"{url}?{request.query_string}"

    data = None
    if method in ('POST', 'PUT', 'PATCH'):
        try:
            data = await request.json()
        except Exception:
            data = await request.read()  # fallback for non-JSON

    async with ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
        req = getattr(session, method.lower())
        if stream:
            async with req(url, headers=headers, json=data if isinstance(data, dict) else None, data=None if isinstance(data, dict) else data) as resp:
                # Stream response
                response = web.StreamResponse(status=resp.status, headers=resp.headers)
                await response.prepare(request)
                async for chunk in resp.content.iter_chunked(4096):
                    await response.write(chunk)
                await response.write_eof()
                return response
        else:
            async with req(url, headers=headers, json=data if isinstance(data, dict) else None, data=None if isinstance(data, dict) else data) as resp:
                content = await resp.read()
                return web.Response(body=content, status=resp.status, headers=resp.headers)

# API Endpoints

# 1. Login
async def login(request):
    return await proxy_request(request, 'POST', '/session/login', with_auth=False)

# 2. Logout
async def logout(request):
    return await proxy_request(request, 'POST', '/session/logout')

# 3. List schedules
async def get_schedules(request):
    return await proxy_request(request, 'GET', '/schedules')

# 4. Create schedule
async def create_schedule(request):
    return await proxy_request(request, 'POST', '/schedules')

# 5. Get posts
async def get_posts(request):
    return await proxy_request(request, 'GET', '/posts')

# 6. Submit post
async def create_post(request):
    return await proxy_request(request, 'POST', '/posts')

# 7. Search
async def search(request):
    return await proxy_request(request, 'GET', '/search')

# 8. Get chat messages
async def get_chat_messages(request):
    chat_id = request.match_info['chatId']
    return await proxy_request(request, 'GET', f'/chats/{chat_id}/messages', replace_path_vars={'chatId': chat_id})

# 9. Send chat message
async def send_chat_message(request):
    chat_id = request.match_info['chatId']
    return await proxy_request(request, 'POST', f'/chats/{chat_id}/messages', replace_path_vars={'chatId': chat_id})

# WebSocket proxy endpoint (browser/CLI can connect via ws:// to this proxy)
async def ws_proxy(request):
    chat_id = request.match_info['chatId']
    # Connect to backend WebSocket
    backend_ws_url = f"{DEVICE_PROTOCOL.replace('http', 'ws')}://{DEVICE_HOST}:{DEVICE_PORT}/chats/{chat_id}/ws"
    headers = {}
    auth = get_auth_header(request)
    if auth:
        headers['Authorization'] = auth

    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)

    async with ClientSession() as session:
        async with session.ws_connect(backend_ws_url, headers=headers) as ws_client:

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

# App and routes
app = web.Application()

# REST API routes
app.router.add_post('/session/login', login)
app.router.add_post('/session/logout', logout)
app.router.add_get('/schedules', get_schedules)
app.router.add_post('/schedules', create_schedule)
app.router.add_get('/posts', get_posts)
app.router.add_post('/posts', create_post)
app.router.add_get('/search', search)
app.router.add_get('/chats/{chatId}/messages', get_chat_messages)
app.router.add_post('/chats/{chatId}/messages', send_chat_message)

# WebSocket proxy route
app.router.add_get('/chats/{chatId}/ws', ws_proxy)

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)