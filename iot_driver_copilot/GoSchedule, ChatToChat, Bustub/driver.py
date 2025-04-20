import os
import json
from functools import wraps
from urllib.parse import urlencode
from flask import Flask, request, Response, jsonify, stream_with_context
import requests

# Environment variable configuration
DEVICE_HOST = os.environ.get('DEVICE_HOST', '127.0.0.1')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8000')
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')  # http or https

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '5000'))

# Device base URL construction
DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# Flask app initialization
app = Flask(__name__)

def require_auth(f):
    """Decorator to require Authorization header with Bearer token for certain endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# Helper function to extract token from Authorization header
def get_token():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        return auth_header.split(' ', 1)[1]
    return None

# Session: Login
@app.route('/session/login', methods=['POST'])
def session_login():
    payload = request.get_json(force=True)
    url = f"{DEVICE_BASE_URL}/session/login"
    resp = requests.post(url, json=payload)
    return (resp.content, resp.status_code, resp.headers.items())

# Session: Logout
@app.route('/session/logout', methods=['POST'])
@require_auth
def session_logout():
    url = f"{DEVICE_BASE_URL}/session/logout"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.post(url, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

# Schedules: List tasks
@app.route('/schedules', methods=['GET'])
@require_auth
def get_schedules():
    url = f"{DEVICE_BASE_URL}/schedules"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.get(url, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

# Schedules: Create task
@app.route('/schedules', methods=['POST'])
@require_auth
def post_schedule():
    payload = request.get_json(force=True)
    url = f"{DEVICE_BASE_URL}/schedules"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.post(url, json=payload, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

# Posts: List posts (with query params)
@app.route('/posts', methods=['GET'])
@require_auth
def get_posts():
    url = f"{DEVICE_BASE_URL}/posts"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    # Forward all query params
    resp = requests.get(url, headers=headers, params=request.args)
    return (resp.content, resp.status_code, resp.headers.items())

# Posts: Create post
@app.route('/posts', methods=['POST'])
@require_auth
def post_post():
    payload = request.get_json(force=True)
    url = f"{DEVICE_BASE_URL}/posts"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.post(url, json=payload, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

# Search endpoint (with query param)
@app.route('/search', methods=['GET'])
@require_auth
def search():
    url = f"{DEVICE_BASE_URL}/search"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.get(url, headers=headers, params=request.args)
    return (resp.content, resp.status_code, resp.headers.items())

# Chat: Get messages for chatId
@app.route('/chats/<chatId>/messages', methods=['GET'])
@require_auth
def get_chat_messages(chatId):
    url = f"{DEVICE_BASE_URL}/chats/{chatId}/messages"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.get(url, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

# Chat: Post message to chatId
@app.route('/chats/<chatId>/messages', methods=['POST'])
@require_auth
def post_chat_message(chatId):
    payload = request.get_json(force=True)
    url = f"{DEVICE_BASE_URL}/chats/{chatId}/messages"
    token = get_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = requests.post(url, json=payload, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)