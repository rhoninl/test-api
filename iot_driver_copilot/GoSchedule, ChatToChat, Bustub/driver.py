import os
import json
import requests
from flask import Flask, request, Response, jsonify, stream_with_context
from functools import wraps

# Configuration from environment variables
DEVICE_HOST = os.environ.get('DEVICE_HOST', 'localhost')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '80')
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# Construct device API base URL
DEVICE_API_BASE = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Helper to forward headers (for auth)
def get_auth_header():
    auth = request.headers.get('Authorization')
    if auth:
        return {"Authorization": auth}
    return {}

# Decorator to proxy authentication
def require_auth_proxy(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Optionally add more sophisticated session/token management here
        return f(*args, **kwargs)
    return decorated

@app.route('/session/login', methods=['POST'])
def session_login():
    # Proxy login to device
    data = request.get_json(force=True)
    resp = requests.post(f"{DEVICE_API_BASE}/session/login", json=data)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/session/logout', methods=['POST'])
@require_auth_proxy
def session_logout():
    headers = get_auth_header()
    resp = requests.post(f"{DEVICE_API_BASE}/session/logout", headers=headers)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/schedules', methods=['GET', 'POST'])
@require_auth_proxy
def schedules():
    headers = get_auth_header()
    if request.method == 'GET':
        resp = requests.get(f"{DEVICE_API_BASE}/schedules", headers=headers, params=request.args)
    else:
        resp = requests.post(f"{DEVICE_API_BASE}/schedules", headers=headers, json=request.get_json(force=True))
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/posts', methods=['GET', 'POST'])
@require_auth_proxy
def posts():
    headers = get_auth_header()
    if request.method == 'GET':
        resp = requests.get(f"{DEVICE_API_BASE}/posts", headers=headers, params=request.args)
    else:
        resp = requests.post(f"{DEVICE_API_BASE}/posts", headers=headers, json=request.get_json(force=True))
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/search', methods=['GET'])
@require_auth_proxy
def search():
    headers = get_auth_header()
    resp = requests.get(f"{DEVICE_API_BASE}/search", headers=headers, params=request.args)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/chats/<chatId>/messages', methods=['GET', 'POST'])
@require_auth_proxy
def chat_messages(chatId):
    headers = get_auth_header()
    if request.method == 'GET':
        resp = requests.get(f"{DEVICE_API_BASE}/chats/{chatId}/messages", headers=headers, params=request.args)
    else:
        resp = requests.post(f"{DEVICE_API_BASE}/chats/{chatId}/messages", headers=headers, json=request.get_json(force=True))
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

# Provide a root endpoint with minimal info
@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "name": "GoSchedule/ChatToChat/Bustub HTTP Proxy Driver",
        "endpoints": [
            {"method": "POST", "path": "/session/login"},
            {"method": "POST", "path": "/session/logout"},
            {"method": "GET/POST", "path": "/schedules"},
            {"method": "GET/POST", "path": "/posts"},
            {"method": "GET", "path": "/search"},
            {"method": "GET/POST", "path": "/chats/<chatId>/messages"}
        ]
    })

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT)