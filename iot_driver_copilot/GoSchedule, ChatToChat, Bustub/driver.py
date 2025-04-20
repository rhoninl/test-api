import os
import json
import asyncio
from typing import Dict, Any, Optional
from aiohttp import web, ClientSession, WSMsgType

DEVICE_HOST = os.environ.get('DEVICE_HOST', '127.0.0.1')
DEVICE_PORT = int(os.environ.get('DEVICE_PORT', '8080'))
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8000'))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

def device_url(path: str) -> str:
    return DEVICE_BASE_URL + path

async def proxy_request(request: web.Request, path: str, method: str, with_body: bool = False, json_subst: Optional[Dict[str, Any]] = None):
    headers = dict(request.headers)
    headers.pop('Host', None)
    url = device_url(path)
    params = request.query.copy()
    data = None
    if with_body:
        try:
            data = await request.json()
        except Exception:
            data = await request.text()
    if json_subst:
        if isinstance(data, dict):
            data.update(json_subst)
        else:
            data = json_subst
    async with ClientSession() as session:
        async with session.request(
            method, url, params=params, headers=headers, json=data if isinstance(data, dict) else None, data=data if isinstance(data, str) else None
        ) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, headers=resp.headers, body=body)

# API handlers

async def login(request):
    return await proxy_request(request, '/session/login', 'POST', with_body=True)

async def logout(request):
    return await proxy_request(request, '/session/logout', 'POST', with_body=True)

async def get_schedules(request):
    return await proxy_request(request, '/schedules', 'GET')

async def create_schedule(request):
    return await proxy_request(request, '/schedules', 'POST', with_body=True)

async def get_posts(request):
    return await proxy_request(request, '/posts', 'GET')

async def create_post(request):
    return await proxy_request(request, '/posts', 'POST', with_body=True)

async def search(request):
    return await proxy_request(request, '/search', 'GET')

async def get_chat_messages(request):
    chat_id = request.match_info['chatId']
    return await proxy_request(request, f'/chats/{chat_id}/messages', 'GET')

async def post_chat_message(request):
    chat_id = request.match_info['chatId']
    return await proxy_request(request, f'/chats/{chat_id}/messages', 'POST', with_body=True)

# WebSocket proxying (if needed)
async def websocket_handler(request):
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    ws_url = f"ws{'s' if DEVICE_PROTOCOL == 'https' else ''}://{DEVICE_HOST}:{DEVICE_PORT}/ws"
    async with ClientSession() as session:
        async with session.ws_connect(ws_url) as ws_client:
            async def client_to_server():
                async for msg in ws_server:
                    if msg.type == WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_client.close()
            async def server_to_client():
                async for msg in ws_client:
                    if msg.type == WSMsgType.TEXT:
                        await ws_server.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws_server.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await ws_server.close()
            await asyncio.gather(client_to_server(), server_to_client())
    return ws_server

# Main app setup
app = web.Application()

app.add_routes([
    web.post('/session/login', login),
    web.post('/session/logout', logout),
    web.get('/schedules', get_schedules),
    web.post('/schedules', create_schedule),
    web.get('/posts', get_posts),
    web.post('/posts', create_post),
    web.get('/search', search),
    web.get('/chats/{chatId}/messages', get_chat_messages),
    web.post('/chats/{chatId}/messages', post_chat_message),
    web.get('/ws', websocket_handler),
])

if __name__ == '__main__':
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)