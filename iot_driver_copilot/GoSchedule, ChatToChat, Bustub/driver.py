import os
import json
from urllib.parse import urlencode
from functools import wraps

from flask import Flask, request, Response, jsonify, stream_with_context
import requests

app = Flask(__name__)

# Environment Variable Configuration
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8000")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")  # http or https

DEVICE_BASE = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# Helper: Proxy request to device, stream response if wanted
def proxy_request(method, path, stream=False, token=None, data=None, params=None, headers=None):
    url = DEVICE_BASE + path
    req_headers = dict(headers) if headers else {}
    if token:
        req_headers['Authorization'] = f"Bearer {token}"
    payload = None
    if data:
        payload = json.dumps(data)
        req_headers['Content-Type'] = 'application/json'
    resp = requests.request(method, url, headers=req_headers, params=params, data=payload, stream=stream)
    if stream:
        def generate():
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
        return Response(stream_with_context(generate()), status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    else:
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))

# Decorator to extract token from Authorization header
def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = None
        if auth.startswith('Bearer '):
            token = auth.split(' ', 1)[1]
        return fn(token, *args, **kwargs)
    return wrapper

# 1. POST /session/login
@app.route('/session/login', methods=['POST'])
def session_login():
    data = request.get_json(force=True)
    return proxy_request('POST', '/session/login', data=data)

# 2. POST /session/logout
@app.route('/session/logout', methods=['POST'])
@require_token
def session_logout(token):
    return proxy_request('POST', '/session/logout', token=token)

# 3. GET /schedules
@app.route('/schedules', methods=['GET'])
@require_token
def get_schedules(token):
    return proxy_request('GET', '/schedules', token=token, params=request.args)

# 4. POST /schedules
@app.route('/schedules', methods=['POST'])
@require_token
def post_schedules(token):
    data = request.get_json(force=True)
    return proxy_request('POST', '/schedules', token=token, data=data)

# 5. GET /posts
@app.route('/posts', methods=['GET'])
@require_token
def get_posts(token):
    return proxy_request('GET', '/posts', token=token, params=request.args)

# 6. POST /posts
@app.route('/posts', methods=['POST'])
@require_token
def post_posts(token):
    data = request.get_json(force=True)
    return proxy_request('POST', '/posts', token=token, data=data)

# 7. GET /search
@app.route('/search', methods=['GET'])
@require_token
def get_search(token):
    return proxy_request('GET', '/search', token=token, params=request.args)

# 8. GET /chats/<chatId>/messages
@app.route('/chats/<chatId>/messages', methods=['GET'])
@require_token
def get_chat_messages(token, chatId):
    path = f"/chats/{chatId}/messages"
    return proxy_request('GET', path, token=token, params=request.args)

# 9. POST /chats/<chatId>/messages
@app.route('/chats/<chatId>/messages', methods=['POST'])
@require_token
def post_chat_messages(token, chatId):
    data = request.get_json(force=True)
    path = f"/chats/{chatId}/messages"
    return proxy_request('POST', path, token=token, data=data)

# Health check
@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "GoSchedule driver running"})

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT)