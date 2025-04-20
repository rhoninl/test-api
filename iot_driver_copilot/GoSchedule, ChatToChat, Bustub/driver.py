import os
import json
import requests
from flask import Flask, request, Response, jsonify, stream_with_context

app = Flask(__name__)

# Environment Variables
DEVICE_IP = os.environ.get("DEVICE_IP", "127.0.0.1")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "80")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", 8080))
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_IP}:{DEVICE_PORT}"

# --- Utility Functions ---

def device_api_url(path):
    return f"{DEVICE_BASE_URL}{path}"

def forward_headers(exclude=None):
    exclude = exclude or []
    headers = {}
    for key, value in request.headers.items():
        lk = key.lower()
        if lk not in exclude:
            headers[key] = value
    return headers

def proxy_device_request(method, path, data=None, params=None, stream=False):
    url = device_api_url(path)
    headers = forward_headers(exclude=["host", "content-length"])
    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            data=data,
            json=None if not request.is_json else request.get_json(silent=True),
            stream=stream,
            timeout=30
        )
        return resp
    except requests.RequestException as ex:
        return None

# --- Authentication Endpoints ---

@app.route('/session/login', methods=['POST'])
def session_login():
    resp = proxy_device_request("POST", "/session/login", data=request.data)
    if resp is None:
        return jsonify({"error": "Device unreachable"}), 502
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/session/logout', methods=['POST'])
def session_logout():
    resp = proxy_device_request("POST", "/session/logout", data=request.data)
    if resp is None:
        return jsonify({"error": "Device unreachable"}), 502
    return (resp.content, resp.status_code, resp.headers.items())

# --- Schedules Endpoints ---

@app.route('/schedules', methods=['GET', 'POST'])
def schedules():
    if request.method == 'GET':
        resp = proxy_device_request("GET", "/schedules", params=request.args)
        if resp is None:
            return jsonify({"error": "Device unreachable"}), 502
        return (resp.content, resp.status_code, resp.headers.items())
    elif request.method == 'POST':
        resp = proxy_device_request("POST", "/schedules", data=request.data)
        if resp is None:
            return jsonify({"error": "Device unreachable"}), 502
        return (resp.content, resp.status_code, resp.headers.items())

# --- Posts Endpoints ---

@app.route('/posts', methods=['GET', 'POST'])
def posts():
    if request.method == 'GET':
        resp = proxy_device_request("GET", "/posts", params=request.args)
        if resp is None:
            return jsonify({"error": "Device unreachable"}), 502
        return (resp.content, resp.status_code, resp.headers.items())
    elif request.method == 'POST':
        resp = proxy_device_request("POST", "/posts", data=request.data)
        if resp is None:
            return jsonify({"error": "Device unreachable"}), 502
        return (resp.content, resp.status_code, resp.headers.items())

# --- Search Endpoint ---

@app.route('/search', methods=['GET'])
def search():
    resp = proxy_device_request("GET", "/search", params=request.args)
    if resp is None:
        return jsonify({"error": "Device unreachable"}), 502
    return (resp.content, resp.status_code, resp.headers.items())

# --- Chat Endpoints ---

@app.route('/chats/<chatId>/messages', methods=['GET', 'POST'])
def chat_messages(chatId):
    path = f"/chats/{chatId}/messages"
    if request.method == 'GET':
        resp = proxy_device_request("GET", path, params=request.args)
        if resp is None:
            return jsonify({"error": "Device unreachable"}), 502
        return (resp.content, resp.status_code, resp.headers.items())
    elif request.method == 'POST':
        resp = proxy_device_request("POST", path, data=request.data)
        if resp is None:
            return jsonify({"error": "Device unreachable"}), 502
        return (resp.content, resp.status_code, resp.headers.items())

# --- HTTP Proxy Streaming for Large Responses (Optional) ---

@app.route('/proxy/stream', methods=['GET'])
def proxy_stream():
    target_path = request.args.get("path")
    if not target_path:
        return jsonify({"error": "Missing 'path' query parameter"}), 400
    def generate():
        with requests.get(device_api_url(target_path), headers=forward_headers(), stream=True) as r:
            for chunk in r.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk
    return Response(stream_with_context(generate()), content_type='application/octet-stream')

# --- Main ---

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)