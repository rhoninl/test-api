import os
import json
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from functools import wraps

# Environment variable configuration
DEVICE_HOST = os.environ.get("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", "8080"))
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

app = Flask(__name__)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization")
        if not auth:
            return jsonify({"error": "Missing Authorization header"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/session/login", methods=["POST"])
def session_login():
    data = request.get_json(force=True)
    url = f"{DEVICE_BASE_URL}/session/login"
    resp = requests.post(url, json=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/session/logout", methods=["POST"])
@require_auth
def session_logout():
    url = f"{DEVICE_BASE_URL}/session/logout"
    headers = {"Authorization": request.headers["Authorization"]}
    resp = requests.post(url, headers=headers)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/schedules", methods=["GET", "POST"])
@require_auth
def schedules():
    url = f"{DEVICE_BASE_URL}/schedules"
    headers = {"Authorization": request.headers["Authorization"]}
    if request.method == "GET":
        resp = requests.get(url, headers=headers)
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, headers=headers, json=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/posts", methods=["GET", "POST"])
@require_auth
def posts():
    url = f"{DEVICE_BASE_URL}/posts"
    headers = {"Authorization": request.headers["Authorization"]}
    if request.method == "GET":
        resp = requests.get(url, headers=headers, params=request.args)
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, headers=headers, json=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/search", methods=["GET"])
@require_auth
def search():
    url = f"{DEVICE_BASE_URL}/search"
    headers = {"Authorization": request.headers["Authorization"]}
    resp = requests.get(url, headers=headers, params=request.args)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/chats/<chatId>/messages", methods=["GET", "POST"])
@require_auth
def chat_messages(chatId):
    url = f"{DEVICE_BASE_URL}/chats/{chatId}/messages"
    headers = {"Authorization": request.headers["Authorization"]}
    if request.method == "GET":
        resp = requests.get(url, headers=headers)
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, headers=headers, json=data)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)