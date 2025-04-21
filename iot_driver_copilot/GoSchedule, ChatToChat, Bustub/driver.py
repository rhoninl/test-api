import os
import json
from functools import wraps
from urllib.parse import urlencode
from flask import Flask, request, jsonify, Response, stream_with_context
import requests

# Configuration from environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))
DEVICE_USE_HTTPS = os.environ.get("DEVICE_USE_HTTPS", "false").lower() == "true"

DEVICE_BASE_URL = f'{"https" if DEVICE_USE_HTTPS else "http"}://{DEVICE_HOST}:{DEVICE_PORT}'

app = Flask(__name__)

def proxy_headers():
    headers = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    headers["Content-Type"] = request.headers.get("Content-Type", "application/json")
    return headers

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # All endpoints except login require an Authorization header
        if request.path != "/session/login":
            if not request.headers.get("Authorization"):
                return jsonify({"error": "Authorization header required"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/session/login", methods=["POST"])
def session_login():
    url = f"{DEVICE_BASE_URL}/session/login"
    data = request.get_json(force=True)
    resp = requests.post(url, json=data, headers=proxy_headers())
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))

@app.route("/session/logout", methods=["POST"])
@require_auth
def session_logout():
    url = f"{DEVICE_BASE_URL}/session/logout"
    resp = requests.post(url, headers=proxy_headers())
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))

@app.route("/schedules", methods=["GET", "POST"])
@require_auth
def schedules():
    url = f"{DEVICE_BASE_URL}/schedules"
    if request.method == "GET":
        resp = requests.get(url, headers=proxy_headers())
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    elif request.method == "POST":
        data = request.get_json(force=True)
        resp = requests.post(url, json=data, headers=proxy_headers())
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))

@app.route("/posts", methods=["GET", "POST"])
@require_auth
def posts():
    url = f"{DEVICE_BASE_URL}/posts"
    if request.method == "GET":
        # Forward query string if present
        qs = request.query_string.decode()
        full_url = f"{url}?{qs}" if qs else url
        resp = requests.get(full_url, headers=proxy_headers())
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    elif request.method == "POST":
        data = request.get_json(force=True)
        resp = requests.post(url, json=data, headers=proxy_headers())
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))

@app.route("/search", methods=["GET"])
@require_auth
def search():
    url = f"{DEVICE_BASE_URL}/search"
    qs = request.query_string.decode()
    full_url = f"{url}?{qs}" if qs else url
    resp = requests.get(full_url, headers=proxy_headers())
    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))

@app.route("/chats/<chatId>/messages", methods=["GET", "POST"])
@require_auth
def chat_messages(chatId):
    url = f"{DEVICE_BASE_URL}/chats/{chatId}/messages"
    if request.method == "GET":
        resp = requests.get(url, headers=proxy_headers())
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    elif request.method == "POST":
        data = request.get_json(force=True)
        resp = requests.post(url, json=data, headers=proxy_headers())
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT)