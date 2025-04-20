import os
import json
import requests
from flask import Flask, request, Response, jsonify, stream_with_context

app = Flask(__name__)

# Device backend REST API configuration from environment variables
DEVICE_HOST = os.environ.get('DEVICE_HOST', 'localhost')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8080')
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '5000'))

BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

# --- SESSION MANAGEMENT ---

@app.route('/session/login', methods=['POST'])
def session_login():
    data = request.get_json(force=True)
    resp = requests.post(f"{BASE_URL}/session/login", json=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/session/logout', methods=['POST'])
def session_logout():
    headers = {}
    auth_header = request.headers.get('Authorization')
    if auth_header:
        headers['Authorization'] = auth_header
    resp = requests.post(f"{BASE_URL}/session/logout", headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

# --- SCHEDULES ---

@app.route('/schedules', methods=['GET', 'POST'])
def schedules():
    headers = {}
    auth_header = request.headers.get('Authorization')
    if auth_header:
        headers['Authorization'] = auth_header

    if request.method == 'GET':
        resp = requests.get(f"{BASE_URL}/schedules", headers=headers, params=request.args)
        return (resp.content, resp.status_code, resp.headers.items())
    elif request.method == 'POST':
        data = request.get_json(force=True)
        resp = requests.post(f"{BASE_URL}/schedules", headers=headers, json=data)
        return (resp.content, resp.status_code, resp.headers.items())

# --- POSTS ---

@app.route('/posts', methods=['GET', 'POST'])
def posts():
    headers = {}
    auth_header = request.headers.get('Authorization')
    if auth_header:
        headers['Authorization'] = auth_header

    if request.method == 'GET':
        resp = requests.get(f"{BASE_URL}/posts", headers=headers, params=request.args)
        return (resp.content, resp.status_code, resp.headers.items())
    elif request.method == 'POST':
        data = request.get_json(force=True)
        resp = requests.post(f"{BASE_URL}/posts", headers=headers, json=data)
        return (resp.content, resp.status_code, resp.headers.items())

# --- SEARCH ---

@app.route('/search', methods=['GET'])
def search():
    headers = {}
    auth_header = request.headers.get('Authorization')
    if auth_header:
        headers['Authorization'] = auth_header
    resp = requests.get(f"{BASE_URL}/search", headers=headers, params=request.args)
    return (resp.content, resp.status_code, resp.headers.items())

# --- CHATS ---

@app.route('/chats/<chatId>/messages', methods=['GET', 'POST'])
def chat_messages(chatId):
    headers = {}
    auth_header = request.headers.get('Authorization')
    if auth_header:
        headers['Authorization'] = auth_header

    if request.method == 'GET':
        resp = requests.get(f"{BASE_URL}/chats/{chatId}/messages", headers=headers, params=request.args)
        return (resp.content, resp.status_code, resp.headers.items())
    elif request.method == 'POST':
        data = request.get_json(force=True)
        resp = requests.post(f"{BASE_URL}/chats/{chatId}/messages", headers=headers, json=data)
        return (resp.content, resp.status_code, resp.headers.items())

# --- MAIN ---

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT)