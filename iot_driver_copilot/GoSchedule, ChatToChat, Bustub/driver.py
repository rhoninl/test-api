import os
import json
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, request, Response, jsonify, stream_with_context
import requests

app = Flask(__name__)

# Configuration from environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

def get_auth_token():
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(' ', 1)[1]
    return None

def proxy_headers():
    headers = {}
    for k, v in request.headers.items():
        if k.lower() == "host":
            continue
        headers[k] = v
    return headers

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_auth_token()
        if not token:
            return jsonify({"error": "Missing or invalid Authorization token"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/session/login", methods=["POST"])
def session_login():
    url = f"{BASE_URL}/session/login"
    headers = proxy_headers()
    data = request.get_json(force=True)
    resp = requests.post(url, headers=headers, json=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/session/logout", methods=["POST"])
@require_token
def session_logout():
    url = f"{BASE_URL}/session/logout"
    headers = proxy_headers()
    resp = requests.post(url, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/schedules", methods=["GET", "POST"])
@require_token
def schedules():
    url = f"{BASE_URL}/schedules"
    headers = proxy_headers()
    if request.method == "GET":
        resp = requests.get(url, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, headers=headers, json=data)
        return (resp.content, resp.status_code, resp.headers.items())

@app.route("/posts", methods=["GET", "POST"])
@require_token
def posts():
    url = f"{BASE_URL}/posts"
    headers = proxy_headers()
    if request.method == "GET":
        params = request.args.to_dict()
        resp = requests.get(url, headers=headers, params=params)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, headers=headers, json=data)
        return (resp.content, resp.status_code, resp.headers.items())

@app.route("/search", methods=["GET"])
@require_token
def search():
    url = f"{BASE_URL}/search"
    headers = proxy_headers()
    params = request.args.to_dict()
    resp = requests.get(url, headers=headers, params=params)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/chats/<chat_id>/messages", methods=["GET", "POST"])
@require_token
def chat_messages(chat_id):
    url = f"{BASE_URL}/chats/{chat_id}/messages"
    headers = proxy_headers()
    if request.method == "GET":
        resp = requests.get(url, headers=headers)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, headers=headers, json=data)
        return (resp.content, resp.status_code, resp.headers.items())

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT)