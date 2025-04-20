import os
import json
from urllib.parse import urlencode
from functools import wraps

from flask import Flask, request, Response, jsonify, stream_with_context
import requests

# Environment variables for configuration
DEVICE_URL = os.environ.get('DEVICE_URL', 'http://localhost:8000')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# Flask HTTP server
app = Flask(__name__)

def get_token_from_headers():
    auth = request.headers.get('Authorization')
    if auth and auth.lower().startswith('bearer '):
        return auth[7:]
    return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token_from_headers()
        if not token:
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
        return f(token, *args, **kwargs)
    return decorated

# 1. POST /session/login
@app.route('/session/login', methods=['POST'])
def session_login():
    try:
        data = request.get_json(force=True)
        r = requests.post(f'{DEVICE_URL}/session/login', json=data)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 2. POST /session/logout
@app.route('/session/logout', methods=['POST'])
@require_auth
def session_logout(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        r = requests.post(f'{DEVICE_URL}/session/logout', headers=headers)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 3. GET /schedules
@app.route('/schedules', methods=['GET'])
@require_auth
def get_schedules(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        r = requests.get(f'{DEVICE_URL}/schedules', headers=headers)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 4. POST /schedules
@app.route('/schedules', methods=['POST'])
@require_auth
def post_schedules(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        data = request.get_json(force=True)
        r = requests.post(f'{DEVICE_URL}/schedules', headers=headers, json=data)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 5. GET /posts
@app.route('/posts', methods=['GET'])
@require_auth
def get_posts(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        query = request.query_string.decode()
        url = f'{DEVICE_URL}/posts'
        if query:
            url += '?' + query
        r = requests.get(url, headers=headers)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 6. POST /posts
@app.route('/posts', methods=['POST'])
@require_auth
def post_posts(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        data = request.get_json(force=True)
        r = requests.post(f'{DEVICE_URL}/posts', headers=headers, json=data)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 7. GET /search
@app.route('/search', methods=['GET'])
@require_auth
def get_search(token):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        query = request.query_string.decode()
        url = f'{DEVICE_URL}/search'
        if query:
            url += '?' + query
        r = requests.get(url, headers=headers)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 8. GET /chats/<chatId>/messages
@app.route('/chats/<chatId>/messages', methods=['GET'])
@require_auth
def get_chat_messages(token, chatId):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        url = f'{DEVICE_URL}/chats/{chatId}/messages'
        r = requests.get(url, headers=headers)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 9. POST /chats/<chatId>/messages
@app.route('/chats/<chatId>/messages', methods=['POST'])
@require_auth
def post_chat_message(token, chatId):
    try:
        headers = {'Authorization': f'Bearer {token}'}
        data = request.get_json(force=True)
        url = f'{DEVICE_URL}/chats/{chatId}/messages'
        r = requests.post(url, headers=headers, json=data)
        return (r.content, r.status_code, r.headers.items())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Health check endpoint (optional)
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT)