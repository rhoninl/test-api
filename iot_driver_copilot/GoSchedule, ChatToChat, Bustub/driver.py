import os
import json
import requests
from flask import Flask, request, Response, jsonify, stream_with_context

app = Flask(__name__)

# Load configuration from environment variables
DEVICE_BASE_URL = os.environ.get('DEVICE_BASE_URL')  # e.g. "http://192.168.1.100:8080"
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8081))

# Helper to build device endpoint URL
def device_url(path):
    return f"{DEVICE_BASE_URL.rstrip('/')}{path}"

# 1. POST /session/login
@app.route('/session/login', methods=['POST'])
def session_login():
    resp = requests.post(
        device_url('/session/login'),
        json=request.get_json(),
        headers={'Content-Type': 'application/json'}
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 2. POST /session/logout
@app.route('/session/logout', methods=['POST'])
def session_logout():
    auth_header = request.headers.get('Authorization')
    headers = {'Authorization': auth_header} if auth_header else {}
    resp = requests.post(
        device_url('/session/logout'),
        headers=headers
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 3. GET /schedules
@app.route('/schedules', methods=['GET'])
def get_schedules():
    auth_header = request.headers.get('Authorization')
    headers = {'Authorization': auth_header} if auth_header else {}
    resp = requests.get(
        device_url('/schedules'),
        headers=headers,
        params=request.args
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 4. POST /schedules
@app.route('/schedules', methods=['POST'])
def post_schedules():
    auth_header = request.headers.get('Authorization')
    headers = {
        'Authorization': auth_header,
        'Content-Type': 'application/json'
    } if auth_header else {'Content-Type': 'application/json'}
    resp = requests.post(
        device_url('/schedules'),
        headers=headers,
        json=request.get_json()
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 5. GET /posts
@app.route('/posts', methods=['GET'])
def get_posts():
    auth_header = request.headers.get('Authorization')
    headers = {'Authorization': auth_header} if auth_header else {}
    resp = requests.get(
        device_url('/posts'),
        headers=headers,
        params=request.args
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 6. POST /posts
@app.route('/posts', methods=['POST'])
def post_posts():
    auth_header = request.headers.get('Authorization')
    headers = {
        'Authorization': auth_header,
        'Content-Type': 'application/json'
    } if auth_header else {'Content-Type': 'application/json'}
    resp = requests.post(
        device_url('/posts'),
        headers=headers,
        json=request.get_json()
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 7. GET /search
@app.route('/search', methods=['GET'])
def search():
    auth_header = request.headers.get('Authorization')
    headers = {'Authorization': auth_header} if auth_header else {}
    resp = requests.get(
        device_url('/search'),
        headers=headers,
        params=request.args
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 8. GET /chats/<chatId>/messages
@app.route('/chats/<chatId>/messages', methods=['GET'])
def get_chat_messages(chatId):
    auth_header = request.headers.get('Authorization')
    headers = {'Authorization': auth_header} if auth_header else {}
    resp = requests.get(
        device_url(f'/chats/{chatId}/messages'),
        headers=headers,
        params=request.args
    )
    return (resp.content, resp.status_code, resp.headers.items())

# 9. POST /chats/<chatId>/messages
@app.route('/chats/<chatId>/messages', methods=['POST'])
def post_chat_messages(chatId):
    auth_header = request.headers.get('Authorization')
    headers = {
        'Authorization': auth_header,
        'Content-Type': 'application/json'
    } if auth_header else {'Content-Type': 'application/json'}
    resp = requests.post(
        device_url(f'/chats/{chatId}/messages'),
        headers=headers,
        json=request.get_json()
    )
    return (resp.content, resp.status_code, resp.headers.items())

# Health check endpoint
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT)