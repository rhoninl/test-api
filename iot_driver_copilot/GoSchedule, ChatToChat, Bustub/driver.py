import os
import json
import sys
from urllib.parse import urlencode
from functools import wraps

from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import threading
import requests

DEVICE_HOST = os.environ.get('DEVICE_HOST', '127.0.0.1')
DEVICE_PORT = os.environ.get('DEVICE_PORT', '8080')
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8000'))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

API_PATHS = {
    "login":         {"method": "POST", "path": "/session/login"},
    "logout":        {"method": "POST", "path": "/session/logout"},
    "get_schedules": {"method": "GET",  "path": "/schedules"},
    "post_schedule": {"method": "POST", "path": "/schedules"},
    "get_posts":     {"method": "GET",  "path": "/posts"},
    "post_post":     {"method": "POST", "path": "/posts"},
    "search":        {"method": "GET",  "path": "/search"},
    "get_chat":      {"method": "GET",  "path": "/chats/{chatId}/messages"},
    "post_chat":     {"method": "POST", "path": "/chats/{chatId}/messages"},
}

def get_auth_token(headers):
    auth = headers.get('Authorization')
    if auth and auth.lower().startswith('bearer '):
        return auth[7:]
    return auth

def require_auth(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        token = self.headers.get('Authorization')
        if not token:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing Authorization header"}).encode())
            return
        return func(self, *args, **kwargs)
    return wrapper

class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _set_headers(self, status=200, extra_headers=None):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def _forward_request(self, api_key, path_vars=None, query_vars=None, require_auth_token=False):
        api = API_PATHS[api_key]
        method = api['method']
        path = api['path']
        if path_vars:
            path = path.format(**path_vars)
        url = DEVICE_BASE_URL + path
        if query_vars:
            url = f"{url}?{urlencode(query_vars)}"
        headers = {}
        if require_auth_token:
            token = self.headers.get('Authorization')
            if token:
                headers['Authorization'] = token
        if method in ['POST', 'PUT', 'PATCH']:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b''
            try:
                json_data = json.loads(post_data.decode()) if post_data else {}
            except Exception:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
                return
            resp = requests.request(method, url, headers=headers, json=json_data)
        else:
            resp = requests.request(method, url, headers=headers)
        self._set_headers(resp.status_code)
        self.wfile.write(resp.content)

    def do_POST(self):
        if self.path == "/session/login":
            self._forward_request("login")
        elif self.path == "/session/logout":
            self._forward_request("logout", require_auth_token=True)
        elif self.path == "/schedules":
            self._forward_request("post_schedule", require_auth_token=True)
        elif self.path == "/posts":
            self._forward_request("post_post", require_auth_token=True)
        elif self.path.startswith("/chats/") and self.path.endswith("/messages"):
            chat_id = self.path.split('/')[2]
            self._forward_request("post_chat", path_vars={"chatId": chat_id}, require_auth_token=True)
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def do_GET(self):
        if self.path == "/schedules":
            self._forward_request("get_schedules", require_auth_token=True)
        elif self.path.startswith("/posts"):
            # Support query ?page=2
            if '?' in self.path:
                base, query = self.path.split('?', 1)
                q_dict = dict(item.split('=') for item in query.split('&') if '=' in item)
            else:
                q_dict = {}
            self._forward_request("get_posts", query_vars=q_dict, require_auth_token=True)
        elif self.path.startswith("/search"):
            if '?' in self.path:
                base, query = self.path.split('?', 1)
                q_dict = dict(item.split('=') for item in query.split('&') if '=' in item)
            else:
                q_dict = {}
            self._forward_request("search", query_vars=q_dict, require_auth_token=True)
        elif self.path.startswith("/chats/") and self.path.endswith("/messages"):
            chat_id = self.path.split('/')[2]
            self._forward_request("get_chat", path_vars={"chatId": chat_id}, require_auth_token=True)
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def log_message(self, format, *args):
        # Optional: comment out to silence logs
        sys.stderr.write("%s - - [%s] %s\n" %
                         (self.client_address[0],
                          self.log_date_time_string(),
                          format % args))

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    server_address = (SERVER_HOST, SERVER_PORT)
    httpd = ThreadedHTTPServer(server_address, ProxyHTTPRequestHandler)
    print(f"Driver HTTP server running at http://{SERVER_HOST}:{SERVER_PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()

if __name__ == '__main__':
    run()