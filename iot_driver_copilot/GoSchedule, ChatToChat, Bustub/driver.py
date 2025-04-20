import os
import json
from functools import wraps
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

from flask import Flask, request, Response, jsonify, stream_with_context
import requests

app = Flask(__name__)

# Load configuration from environment variables
DEVICE_BASE_URL = os.environ.get("DEVICE_BASE_URL", "http://localhost:8000")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Helper for constructing device URLs, handling path and query params
def device_url(path, query=None):
    base = DEVICE_BASE_URL.rstrip("/")
    full_path = urljoin(base + "/", path.lstrip("/"))
    if query:
        return "{}?{}".format(full_path, urlencode(query, doseq=True))
    return full_path

# Proxy decorator to handle session token and errors
def proxy_request(method, device_path, path_params=None, query_params=None, auth_required=True):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Path parameters
            path_vars = {}
            if path_params:
                for p in path_params:
                    path_vars[p] = kwargs.get(p)
            # Query parameters
            query = dict(request.args)
            if query_params:
                for qp in query_params:
                    if qp in request.args:
                        query[qp] = request.args[qp]
            # Construct path with path variables
            target_path = device_path
            for k, v in path_vars.items():
                target_path = target_path.replace("{" + k + "}", str(v))
            url = device_url(target_path, query=query if query else None)
            # Headers
            headers = {"Content-Type": request.headers.get("Content-Type", "application/json")}
            # Forward Authorization if present
            if auth_required and "Authorization" in request.headers:
                headers["Authorization"] = request.headers["Authorization"]
            # Data
            data = request.get_data() if method in ["POST", "PUT", "PATCH"] else None
            # Proxy the request
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    data=data,
                    stream=True if fn.__name__ in ["get_messages_stream"] else False,
                    timeout=30
                )
                # Stream if necessary
                if fn.__name__ in ["get_messages_stream"]:
                    def generate():
                        for chunk in resp.iter_content(chunk_size=4096):
                            if chunk:
                                yield chunk
                    return Response(stream_with_context(generate()), status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
                else:
                    return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
            except Exception as e:
                return jsonify({"error": str(e)}), 502
        return wrapper
    return decorator

# -------- Authentication APIs --------

@app.route('/session/login', methods=['POST'])
@proxy_request('POST', '/session/login', auth_required=False)
def session_login():
    pass

@app.route('/session/logout', methods=['POST'])
@proxy_request('POST', '/session/logout')
def session_logout():
    pass

# -------- Schedules APIs --------

@app.route('/schedules', methods=['GET'])
@proxy_request('GET', '/schedules')
def get_schedules():
    pass

@app.route('/schedules', methods=['POST'])
@proxy_request('POST', '/schedules')
def create_schedule():
    pass

# -------- Posts APIs --------

@app.route('/posts', methods=['GET'])
@proxy_request('GET', '/posts')
def get_posts():
    pass

@app.route('/posts', methods=['POST'])
@proxy_request('POST', '/posts')
def create_post():
    pass

# -------- Search API --------

@app.route('/search', methods=['GET'])
@proxy_request('GET', '/search')
def search():
    pass

# -------- Chat APIs --------

@app.route('/chats/<chatId>/messages', methods=['GET'])
@proxy_request('GET', '/chats/{chatId}/messages', path_params=['chatId'])
def get_messages_stream(chatId):
    pass

@app.route('/chats/<chatId>/messages', methods=['POST'])
@proxy_request('POST', '/chats/{chatId}/messages', path_params=['chatId'])
def post_message(chatId):
    pass

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)