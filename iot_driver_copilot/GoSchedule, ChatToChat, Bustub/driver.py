import os
import json
import requests
from urllib.parse import urlencode
from flask import Flask, request, Response, jsonify, stream_with_context

app = Flask(__name__)

# Configuration from environment variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
DEVICE_SCHEME = os.environ.get("DEVICE_SCHEME", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

BASE_URL = f"{DEVICE_SCHEME}://{DEVICE_HOST}:{DEVICE_PORT}"

def relay_headers(in_request, extra_headers=None):
    """Build headers for relaying requests to device, including Authorization."""
    headers = {}
    if in_request.headers.get("Authorization"):
        headers["Authorization"] = in_request.headers["Authorization"]
    headers["Content-Type"] = in_request.headers.get("Content-Type", "application/json")
    if extra_headers:
        headers.update(extra_headers)
    return headers

# ---- API Endpoints ----

@app.route('/session/login', methods=['POST'])
def session_login():
    data = request.get_json(force=True)
    url = f"{BASE_URL}/session/login"
    resp = requests.post(url, json=data, headers=relay_headers(request))
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/session/logout', methods=['POST'])
def session_logout():
    url = f"{BASE_URL}/session/logout"
    resp = requests.post(url, headers=relay_headers(request))
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/schedules', methods=['GET', 'POST'])
def schedules():
    url = f"{BASE_URL}/schedules"
    if request.method == 'GET':
        resp = requests.get(url, headers=relay_headers(request), params=request.args)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, json=data, headers=relay_headers(request))
        return (resp.content, resp.status_code, resp.headers.items())

@app.route('/posts', methods=['GET', 'POST'])
def posts():
    url = f"{BASE_URL}/posts"
    if request.method == 'GET':
        resp = requests.get(url, headers=relay_headers(request), params=request.args)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, json=data, headers=relay_headers(request))
        return (resp.content, resp.status_code, resp.headers.items())

@app.route('/search', methods=['GET'])
def search():
    url = f"{BASE_URL}/search"
    resp = requests.get(url, headers=relay_headers(request), params=request.args)
    return (resp.content, resp.status_code, resp.headers.items())

@app.route('/chats/<chat_id>/messages', methods=['GET', 'POST'])
def chat_messages(chat_id):
    url = f"{BASE_URL}/chats/{chat_id}/messages"
    if request.method == 'GET':
        resp = requests.get(url, headers=relay_headers(request), params=request.args)
        return (resp.content, resp.status_code, resp.headers.items())
    else:
        data = request.get_json(force=True)
        resp = requests.post(url, json=data, headers=relay_headers(request))
        return (resp.content, resp.status_code, resp.headers.items())

# ---- WebSocket Proxy (HTTP Streaming for Browser/CLI) ----

import threading
import queue
import time
import websocket

@app.route('/ws/<path:ws_path>')
def ws_proxy(ws_path):
    token = request.headers.get("Authorization")
    ws_url = f"{DEVICE_SCHEME.replace('http','ws')}://{DEVICE_HOST}:{DEVICE_PORT}/{ws_path}"
    if request.query_string:
        ws_url += "?" + request.query_string.decode()
    
    def generate():
        q = queue.Queue()
        stop_flag = threading.Event()

        def on_message(ws, message):
            q.put(message)

        def on_error(ws, error):
            q.put(json.dumps({"error": str(error)}))
            stop_flag.set()

        def on_close(ws, close_status_code, close_msg):
            stop_flag.set()

        def on_open(ws):
            pass

        ws_headers = []
        if token:
            ws_headers.append(f"Authorization: {token}")

        ws_app = websocket.WebSocketApp(
            ws_url,
            header=ws_headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        wst = threading.Thread(target=ws_app.run_forever)
        wst.daemon = True
        wst.start()

        try:
            while not stop_flag.is_set():
                try:
                    msg = q.get(timeout=0.5)
                    yield msg + "\n"
                except queue.Empty:
                    continue
        finally:
            ws_app.close()
            stop_flag.set()

    return Response(stream_with_context(generate()), mimetype='text/plain')

# ---- Main Entrypoint ----

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)