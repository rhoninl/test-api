import os
import json
import requests
from flask import Flask, request, Response, jsonify, stream_with_context
from functools import wraps

# Configuration from environment variables
DEVICE_HOST = os.environ.get('DEVICE_HOST', 'localhost')
DEVICE_PORT = int(os.environ.get('DEVICE_PORT', '8080'))
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '5000'))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

app = Flask(__name__)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization')
        if not auth:
            return jsonify({'error': 'Authorization header required'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/session/login', methods=['POST'])
def login():
    data = request.get_json(force=True)
    backend_url = f"{DEVICE_BASE_URL}/session/login"
    resp = requests.post(backend_url, json=data)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

@app.route('/session/logout', methods=['POST'])
@require_auth
def logout():
    backend_url = f"{DEVICE_BASE_URL}/session/logout"
    headers = {'Authorization': request.headers.get('Authorization')}
    resp = requests.post(backend_url, headers=headers)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

@app.route('/schedules', methods=['GET', 'POST'])
@require_auth
def schedules():
    backend_url = f"{DEVICE_BASE_URL}/schedules"
    headers = {'Authorization': request.headers.get('Authorization')}
    if request.method == 'GET':
        resp = requests.get(backend_url, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))
    elif request.method == 'POST':
        data = request.get_json(force=True)
        resp = requests.post(backend_url, json=data, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

@app.route('/posts', methods=['GET', 'POST'])
@require_auth
def posts():
    backend_url = f"{DEVICE_BASE_URL}/posts"
    headers = {'Authorization': request.headers.get('Authorization')}
    if request.method == 'GET':
        resp = requests.get(backend_url, headers=headers, params=request.args)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))
    elif request.method == 'POST':
        data = request.get_json(force=True)
        resp = requests.post(backend_url, json=data, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

@app.route('/search', methods=['GET'])
@require_auth
def search():
    backend_url = f"{DEVICE_BASE_URL}/search"
    headers = {'Authorization': request.headers.get('Authorization')}
    resp = requests.get(backend_url, headers=headers, params=request.args)
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

@app.route('/chats/<chat_id>/messages', methods=['GET', 'POST'])
@require_auth
def chat_messages(chat_id):
    backend_url = f"{DEVICE_BASE_URL}/chats/{chat_id}/messages"
    headers = {'Authorization': request.headers.get('Authorization')}
    if request.method == 'GET':
        resp = requests.get(backend_url, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))
    elif request.method == 'POST':
        data = request.get_json(force=True)
        resp = requests.post(backend_url, json=data, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('content-type'))

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)