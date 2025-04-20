import os
import json
from functools import wraps
from flask import Flask, request, Response, jsonify, stream_with_context
import requests

app = Flask(__name__)

# Environment variable configuration
DEVICE_API_HOST = os.getenv('DEVICE_API_HOST', 'localhost')
DEVICE_API_PORT = os.getenv('DEVICE_API_PORT', '8080')
SERVER_HOST = os.getenv('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.getenv('SERVER_PORT', '5000'))
DEVICE_API_PROTOCOL = os.getenv('DEVICE_API_PROTOCOL', 'http')
TIMEOUT = int(os.getenv('DEVICE_TIMEOUT', '10'))  # seconds

DEVICE_BASE_URL = f"{DEVICE_API_PROTOCOL}://{DEVICE_API_HOST}:{DEVICE_API_PORT}"

# Helper: Proxy JSON requests to device API
def device_api_request(method, path, headers=None, params=None, data=None, json_data=None, stream=False):
    url = DEVICE_BASE_URL + path
    req_headers = dict(headers) if headers else {}
    resp = requests.request(
        method=method,
        url=url,
        headers=req_headers,
        params=params,
        data=data,
        json=json_data,
        stream=stream,
        timeout=TIMEOUT
    )
    if stream:
        return resp
    else:
        return resp.status_code, resp.headers, resp.content

# Helper: Session token extraction and forwarding
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if token:
            request.device_auth_header = {'Authorization': token}
        else:
            request.device_auth_header = {}
        return f(*args, **kwargs)
    return decorated

@app.route('/session/login', methods=['POST'])
def session_login():
    status, headers, content = device_api_request(
        'POST',
        '/session/login',
        headers={'Content-Type': 'application/json'},
        json_data=request.get_json()
    )
    return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))

@app.route('/session/logout', methods=['POST'])
@require_auth
def session_logout():
    status, headers, content = device_api_request(
        'POST',
        '/session/logout',
        headers={**request.device_auth_header, 'Content-Type': 'application/json'}
    )
    return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))

@app.route('/schedules', methods=['GET', 'POST'])
@require_auth
def schedules():
    if request.method == 'GET':
        status, headers, content = device_api_request(
            'GET',
            '/schedules',
            headers=request.device_auth_header,
            params=request.args
        )
        return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))
    elif request.method == 'POST':
        status, headers, content = device_api_request(
            'POST',
            '/schedules',
            headers={**request.device_auth_header, 'Content-Type': 'application/json'},
            json_data=request.get_json()
        )
        return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))

@app.route('/posts', methods=['GET', 'POST'])
@require_auth
def posts():
    if request.method == 'GET':
        status, headers, content = device_api_request(
            'GET',
            '/posts',
            headers=request.device_auth_header,
            params=request.args
        )
        return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))
    elif request.method == 'POST':
        status, headers, content = device_api_request(
            'POST',
            '/posts',
            headers={**request.device_auth_header, 'Content-Type': 'application/json'},
            json_data=request.get_json()
        )
        return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))

@app.route('/search', methods=['GET'])
@require_auth
def search():
    status, headers, content = device_api_request(
        'GET',
        '/search',
        headers=request.device_auth_header,
        params=request.args
    )
    return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))

@app.route('/chats/<chat_id>/messages', methods=['GET', 'POST'])
@require_auth
def chat_messages(chat_id):
    path = f'/chats/{chat_id}/messages'
    if request.method == 'GET':
        status, headers, content = device_api_request(
            'GET',
            path,
            headers=request.device_auth_header,
            params=request.args
        )
        return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))
    elif request.method == 'POST':
        status, headers, content = device_api_request(
            'POST',
            path,
            headers={**request.device_auth_header, 'Content-Type': 'application/json'},
            json_data=request.get_json()
        )
        return Response(content, status=status, content_type=headers.get('Content-Type', 'application/json'))

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT)