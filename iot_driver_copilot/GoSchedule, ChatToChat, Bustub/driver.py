import os
import json
import requests
from flask import Flask, request, Response, jsonify, stream_with_context
from functools import wraps

# Environment Variable Configuration
DEVICE_BASE_URL = os.environ.get('DEVICE_BASE_URL')  # e.g., http://192.168.1.10:8080
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8088))
HTTP_PORT = int(os.environ.get('HTTP_PORT', SERVER_PORT))  # Only HTTP used in this driver

if not DEVICE_BASE_URL:
    raise EnvironmentError("DEVICE_BASE_URL environment variable must be set.")

app = Flask(__name__)

def get_auth_header():
    auth_header = request.headers.get('Authorization')
    if auth_header:
        return {'Authorization': auth_header}
    return {}

def proxy_request(method, path, data=None, params=None, headers=None, stream=False):
    url = DEVICE_BASE_URL.rstrip('/') + path
    headers = headers or {}
    resp = requests.request(
        method=method,
        url=url,
        params=params,
        json=data,
        headers=headers,
        stream=stream,
        timeout=30
    )
    return resp

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not request.headers.get('Authorization'):
            return jsonify({'error': 'Missing Authorization header'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/session/login', methods=['POST'])
def session_login():
    data = request.get_json(force=True)
    resp = proxy_request('POST', '/session/login', data=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/session/logout', methods=['POST'])
@require_auth
def session_logout():
    headers = get_auth_header()
    resp = proxy_request('POST', '/session/logout', headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/schedules', methods=['GET', 'POST'])
@require_auth
def schedules():
    headers = get_auth_header()
    if request.method == 'GET':
        resp = proxy_request('GET', '/schedules', headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = proxy_request('POST', '/schedules', data=data, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())

@app.route('/posts', methods=['GET', 'POST'])
@require_auth
def posts():
    headers = get_auth_header()
    if request.method == 'GET':
        params = request.args.to_dict(flat=True)
        resp = proxy_request('GET', '/posts', params=params, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = proxy_request('POST', '/posts', data=data, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())

@app.route('/search', methods=['GET'])
@require_auth
def search():
    headers = get_auth_header()
    params = request.args.to_dict(flat=True)
    resp = proxy_request('GET', '/search', params=params, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/chats/<chat_id>/messages', methods=['GET', 'POST'])
@require_auth
def chat_messages(chat_id):
    headers = get_auth_header()
    device_path = f'/chats/{chat_id}/messages'
    if request.method == 'GET':
        resp = proxy_request('GET', device_path, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = proxy_request('POST', device_path, data=data, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())

@app.route('/')
def index():
    return jsonify({
        'service': 'GoSchedule, ChatToChat, Bustub HTTP Proxy Driver',
        'endpoints': [
            {'method': 'POST', 'path': '/session/login'},
            {'method': 'POST', 'path': '/session/logout'},
            {'method': 'GET/POST', 'path': '/schedules'},
            {'method': 'GET/POST', 'path': '/posts'},
            {'method': 'GET', 'path': '/search'},
            {'method': 'GET/POST', 'path': '/chats/<chatId>/messages'}
        ]
    })

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=HTTP_PORT)