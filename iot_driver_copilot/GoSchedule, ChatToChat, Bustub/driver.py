import os
import json
import sys
from urllib.parse import urljoin, urlencode
from functools import wraps

from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver

import threading
import requests

DEVICE_HOST = os.environ.get('DEVICE_HOST', '127.0.0.1')
DEVICE_PORT = int(os.environ.get('DEVICE_PORT', 8080))
DEVICE_PROTOCOL = os.environ.get('DEVICE_PROTOCOL', 'http').lower()

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8000))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

API_MAP = {
    ('POST', '/session/login'): {
        'device_path': '/session/login',
        'headers': {'Content-Type': 'application/json'},
        'auth': False,
        'request_body': True,
        'path_params': [],
        'query_params': []
    },
    ('POST', '/session/logout'): {
        'device_path': '/session/logout',
        'headers': {'Content-Type': 'application/json'},
        'auth': True,
        'request_body': False,
        'path_params': [],
        'query_params': []
    },
    ('GET', '/schedules'): {
        'device_path': '/schedules',
        'headers': {},
        'auth': True,
        'request_body': False,
        'path_params': [],
        'query_params': []
    },
    ('POST', '/schedules'): {
        'device_path': '/schedules',
        'headers': {'Content-Type': 'application/json'},
        'auth': True,
        'request_body': True,
        'path_params': [],
        'query_params': []
    },
    ('GET', '/posts'): {
        'device_path': '/posts',
        'headers': {},
        'auth': True,
        'request_body': False,
        'path_params': [],
        'query_params': ['page']
    },
    ('POST', '/posts'): {
        'device_path': '/posts',
        'headers': {'Content-Type': 'application/json'},
        'auth': True,
        'request_body': True,
        'path_params': [],
        'query_params': []
    },
    ('GET', '/search'): {
        'device_path': '/search',
        'headers': {},
        'auth': True,
        'request_body': False,
        'path_params': [],
        'query_params': ['q']
    },
    ('GET', '/chats/{chatId}/messages'): {
        'device_path': '/chats/{chatId}/messages',
        'headers': {},
        'auth': True,
        'request_body': False,
        'path_params': ['chatId'],
        'query_params': []
    },
    ('POST', '/chats/{chatId}/messages'): {
        'device_path': '/chats/{chatId}/messages',
        'headers': {'Content-Type': 'application/json'},
        'auth': True,
        'request_body': True,
        'path_params': ['chatId'],
        'query_params': []
    }
}

def parse_path(path):
    for (method, api_path), spec in API_MAP.items():
        if '{' in api_path:
            parts_api = api_path.strip('/').split('/')
            parts_path = path.strip('/').split('/')
            if len(parts_api) != len(parts_path):
                continue
            params = {}
            matched = True
            for api, actual in zip(parts_api, parts_path):
                if api.startswith('{') and api.endswith('}'):
                    params[api[1:-1]] = actual
                elif api != actual:
                    matched = False
                    break
            if matched:
                return (method, api_path, params)
        elif path == api_path:
            return (method, api_path, {})
    return (None, None, None)

def require_auth(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if hasattr(self, 'token') and self.token:
            return func(self, *args, **kwargs)
        else:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"Unauthorized"}')
    return wrapper

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

class DeviceProxyHandler(BaseHTTPRequestHandler):
    # Session token cache, thread safe
    tokens = {}
    lock = threading.Lock()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.end_headers()

    def do_GET(self):
        self.handle_proxy('GET')

    def do_POST(self):
        self.handle_proxy('POST')

    def _get_token(self):
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:]
        return None

    def _set_token(self, token):
        # Optionally store token per client (by IP)
        with self.lock:
            self.tokens[self.client_address[0]] = token

    def _get_stored_token(self):
        with self.lock:
            return self.tokens.get(self.client_address[0])

    def handle_proxy(self, method):
        # Parse path and params
        base_path = self.path.split('?', 1)[0]
        query_string = self.path.split('?', 1)[1] if '?' in self.path else ''
        method_key, api_path, path_params = parse_path(base_path)
        if method_key != method:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"Not found"}')
            return

        spec = API_MAP[(method, api_path)]
        device_path = spec['device_path']
        # Fill path params if needed
        for param in spec.get('path_params', []):
            if param in path_params:
                device_path = device_path.replace(f'{{{param}}}', path_params[param])

        # Prepare headers
        headers = dict(spec.get('headers', {}))
        # Token management
        token = self._get_token()
        if not token and spec['auth']:
            token = self._get_stored_token()
        if token and spec['auth']:
            headers['Authorization'] = f'Bearer {token}'

        # Proxy request to device
        url = urljoin(DEVICE_BASE_URL, device_path)
        if spec.get('query_params'):
            query = {}
            for k in spec['query_params']:
                if k in self._get_query_params():
                    query[k] = self._get_query_params()[k]
            if query:
                url += '?' + urlencode(query)

        data = None
        if method == 'POST' and spec.get('request_body', False):
            length = int(self.headers.get('Content-Length', 0))
            data = self.rfile.read(length) if length else None

        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                data=data,
                timeout=10
            )
        except requests.RequestException as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Device unavailable', 'detail': str(e)}).encode())
            return

        # For login, store token from response
        if method == 'POST' and api_path == '/session/login' and resp.status_code == 200:
            try:
                payload = resp.json()
            except:
                payload = {}
            token = payload.get('token') or payload.get('session_token')
            if token:
                self._set_token(token)

        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in ['content-length', 'connection', 'content-encoding', 'transfer-encoding']:
                self.send_header(k, v)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(resp.content)

    def _get_query_params(self):
        params = {}
        if '?' in self.path:
            qs = self.path.split('?', 1)[1]
            for kv in qs.split('&'):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    params[k] = v
        return params

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), DeviceProxyHandler)
    print(f"HTTP proxy driver running on {SERVER_HOST}:{SERVER_PORT}, proxying to {DEVICE_BASE_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    run()