import os
import json
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, request, jsonify, Response, stream_with_context
import requests

# Environment variables for configuration
DEVICE_API_HOST = os.environ.get("DEVICE_API_HOST", "localhost")
DEVICE_API_PORT = os.environ.get("DEVICE_API_PORT", "8080")
DEVICE_API_PROTOCOL = os.environ.get("DEVICE_API_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

API_BASE = f"{DEVICE_API_PROTOCOL}://{DEVICE_API_HOST}:{DEVICE_API_PORT}"

app = Flask(__name__)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def proxy_request(method, target_path, auth_forward=False, stream=False, **kwargs):
    url = f"{API_BASE}{target_path}"
    headers = {}
    if auth_forward:
        auth_header = request.headers.get("Authorization")
        if auth_header:
            headers["Authorization"] = auth_header
    if "headers" in kwargs:
        headers.update(kwargs["headers"])
        del kwargs["headers"]
    resp = requests.request(
        method,
        url,
        headers=headers,
        params=request.args,
        data=request.data if request.data else None,
        json=request.get_json(silent=True),
        stream=stream
    )
    excluded_headers = ["content-encoding", "content-length", "transfer-encoding", "connection"]
    response_headers = [(name, value) for (name, value) in resp.raw.headers.items()
                        if name.lower() not in excluded_headers]
    if stream:
        return Response(stream_with_context(resp.iter_content(chunk_size=4096)), status=resp.status_code, headers=response_headers)
    else:
        return (resp.content, resp.status_code, response_headers)

@app.route("/session/login", methods=["POST"])
def session_login():
    # Forward JSON body to device backend
    return proxy_request("POST", "/session/login")

@app.route("/session/logout", methods=["POST"])
@require_auth
def session_logout():
    return proxy_request("POST", "/session/logout", auth_forward=True)

@app.route("/schedules", methods=["GET", "POST"])
@require_auth
def schedules():
    if request.method == "GET":
        return proxy_request("GET", "/schedules", auth_forward=True)
    elif request.method == "POST":
        return proxy_request("POST", "/schedules", auth_forward=True)

@app.route("/posts", methods=["GET", "POST"])
@require_auth
def posts():
    if request.method == "GET":
        return proxy_request("GET", "/posts", auth_forward=True)
    elif request.method == "POST":
        return proxy_request("POST", "/posts", auth_forward=True)

@app.route("/search", methods=["GET"])
@require_auth
def search():
    return proxy_request("GET", "/search", auth_forward=True)

@app.route("/chats/<chat_id>/messages", methods=["GET", "POST"])
@require_auth
def chat_messages(chat_id):
    path = f"/chats/{chat_id}/messages"
    if request.method == "GET":
        return proxy_request("GET", path, auth_forward=True)
    elif request.method == "POST":
        return proxy_request("POST", path, auth_forward=True)

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT)