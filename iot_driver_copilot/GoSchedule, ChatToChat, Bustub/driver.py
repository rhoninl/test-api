import os
import json
from functools import wraps
from urllib.parse import urlencode
from flask import Flask, request, jsonify, Response, stream_with_context
import requests

# --- Environment Configuration ---
DEVICE_HOST = os.environ.get('DEVICE_HOST', 'localhost')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8000')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
DEVICE_API_SCHEME = os.environ.get('DEVICE_API_SCHEME', 'http')  # http or https

DEVICE_BASE_URL = f"{DEVICE_API_SCHEME}://{DEVICE_HOST}:{DEVICE_PORT}"

app = Flask(__name__)

# ----------- Session Management -----------
# Simple in-memory session storage (token per browser session)
# In production, replace with persistent storage or JWT tokens.
active_sessions = {}

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', None)
        if not token or token not in active_sessions.values():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ----------- API Proxy Endpoints -----------

@app.route('/session/login', methods=['POST'])
def proxy_login():
    # Forward login credentials to device backend, store token
    credentials = request.get_json(force=True)
    url = f"{DEVICE_BASE_URL}/session/login"
    resp = requests.post(url, json=credentials)
    if resp.status_code == 200:
        data = resp.json()
        token = data.get('token') or data.get('session_token') or data.get('access_token')
        if token:
            # Store session (here, simplistic, keyed by remote addr)
            active_sessions[request.remote_addr] = token
            # Return token to client
            return jsonify({"token": token})
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/session/logout', methods=['POST'])
@require_auth
def proxy_logout():
    token = request.headers.get('Authorization')
    url = f"{DEVICE_BASE_URL}/session/logout"
    headers = {'Authorization': token}
    resp = requests.post(url, headers=headers)
    # Remove session from local store
    active_sessions.pop(request.remote_addr, None)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/schedules', methods=['GET', 'POST'])
@require_auth
def proxy_schedules():
    token = request.headers.get('Authorization')
    url = f"{DEVICE_BASE_URL}/schedules"
    headers = {'Authorization': token}
    if request.method == 'GET':
        resp = requests.get(url, headers=headers, params=request.args)
    else:
        resp = requests.post(url, headers=headers, json=request.get_json(force=True))
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/posts', methods=['GET', 'POST'])
@require_auth
def proxy_posts():
    token = request.headers.get('Authorization')
    url = f"{DEVICE_BASE_URL}/posts"
    headers = {'Authorization': token}
    if request.method == 'GET':
        resp = requests.get(url, headers=headers, params=request.args)
    else:
        resp = requests.post(url, headers=headers, json=request.get_json(force=True))
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/search', methods=['GET'])
@require_auth
def proxy_search():
    token = request.headers.get('Authorization')
    url = f"{DEVICE_BASE_URL}/search"
    headers = {'Authorization': token}
    resp = requests.get(url, headers=headers, params=request.args)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

@app.route('/chats/<chat_id>/messages', methods=['GET', 'POST'])
@require_auth
def proxy_chat_messages(chat_id):
    token = request.headers.get('Authorization')
    url = f"{DEVICE_BASE_URL}/chats/{chat_id}/messages"
    headers = {'Authorization': token}
    if request.method == 'GET':
        # Stream messages if available
        def generate():
            with requests.get(url, headers=headers, params=request.args, stream=True) as resp:
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk
        return Response(stream_with_context(generate()), content_type='application/json')
    else:
        resp = requests.post(url, headers=headers, json=request.get_json(force=True))
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/json'))

# ----------- Health Check -----------
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ----------- Main Entrypoint -----------
if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)