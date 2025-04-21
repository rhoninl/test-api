import os
import json
from functools import wraps
from flask import Flask, request, jsonify, Response, stream_with_context
import requests

app = Flask(__name__)

# Environment variables for configuration
DEVICE_HOST = os.environ.get("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8000")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Compose base URL to backend service
BACKEND_BASE_URL = f"http://{DEVICE_HOST}:{DEVICE_PORT}"

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Authorization token required'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/session/login", methods=["POST"])
def login():
    try:
        payload = request.get_json(force=True)
        resp = requests.post(f"{BACKEND_BASE_URL}/session/login", json=payload)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/session/logout", methods=["POST"])
@require_auth
def logout():
    try:
        headers = {'Authorization': request.headers.get('Authorization')}
        resp = requests.post(f"{BACKEND_BASE_URL}/session/logout", headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/schedules", methods=["GET", "POST"])
@require_auth
def schedules():
    try:
        headers = {'Authorization': request.headers.get('Authorization')}
        if request.method == "GET":
            resp = requests.get(f"{BACKEND_BASE_URL}/schedules", headers=headers)
        else:
            payload = request.get_json(force=True)
            resp = requests.post(f"{BACKEND_BASE_URL}/schedules", headers=headers, json=payload)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/posts", methods=["GET", "POST"])
@require_auth
def posts():
    try:
        headers = {'Authorization': request.headers.get('Authorization')}
        if request.method == "GET":
            resp = requests.get(f"{BACKEND_BASE_URL}/posts", headers=headers, params=request.args)
        else:
            payload = request.get_json(force=True)
            resp = requests.post(f"{BACKEND_BASE_URL}/posts", headers=headers, json=payload)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/search", methods=["GET"])
@require_auth
def search():
    try:
        headers = {'Authorization': request.headers.get('Authorization')}
        resp = requests.get(f"{BACKEND_BASE_URL}/search", headers=headers, params=request.args)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/chats/<chat_id>/messages", methods=["GET", "POST"])
@require_auth
def chat_messages(chat_id):
    try:
        headers = {'Authorization': request.headers.get('Authorization')}
        if request.method == "GET":
            resp = requests.get(f"{BACKEND_BASE_URL}/chats/{chat_id}/messages", headers=headers)
        else:
            payload = request.get_json(force=True)
            resp = requests.post(f"{BACKEND_BASE_URL}/chats/{chat_id}/messages", headers=headers, json=payload)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT)