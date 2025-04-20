import os
import json
from urllib.parse import urlencode
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
import threading
import requests

DEVICE_HOST = os.environ.get("DEVICE_HOST", "127.0.0.1")
DEVICE_PORT = int(os.environ.get("DEVICE_PORT", "8080"))
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8000"))

DEVICE_BASE = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

def extract_token(headers):
    auth = headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return auth

class ProxyHandler(BaseHTTPRequestHandler):
    routes = [
        {"method": "POST", "path": "/session/login"},
        {"method": "POST", "path": "/session/logout"},
        {"method": "GET", "path": "/schedules"},
        {"method": "POST", "path": "/schedules"},
        {"method": "GET", "path": "/posts"},
        {"method": "POST", "path": "/posts"},
        {"method": "GET", "path": "/search"},
        {"method": "GET", "path": "/chats/", "wildcard": True, "suffix": "/messages"},
        {"method": "POST", "path": "/chats/", "wildcard": True, "suffix": "/messages"},
    ]

    def _set_headers(self, code=200, extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def _parse_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            content = self.rfile.read(length)
            try:
                return json.loads(content)
            except Exception:
                return content
        return None

    def _forward_request(self, device_method, device_path, query=None, device_headers=None, json_body=None):
        url = DEVICE_BASE + device_path
        if query:
            url += "?" + urlencode(query)
        headers = device_headers or {}
        try:
            resp = requests.request(
                device_method,
                url,
                headers=headers,
                json=json_body if isinstance(json_body, dict) else None,
                data=None if isinstance(json_body, dict) else json_body,
                timeout=10,
                verify=False
            )
            return resp.status_code, resp.headers, resp.text
        except requests.RequestException as e:
            return 502, {}, json.dumps({"error": str(e)})

    def _route(self, method, path):
        for route in self.routes:
            if method != route['method']:
                continue
            if route.get("wildcard"):
                # Matches /chats/{chatId}/messages
                if path.startswith(route["path"]) and path.endswith(route["suffix"]):
                    return route
            else:
                if path == route['path']:
                    return route
        return None

    def do_GET(self):
        route = self._route("GET", self.path.split('?')[0])
        if not route:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())
            return

        device_path = self.path
        device_headers = {}
        token = extract_token(self.headers)
        if token:
            device_headers['Authorization'] = f'Bearer {token}'

        # Handle query parameters transparently
        status, headers, body = self._forward_request("GET", device_path, device_headers=device_headers)
        self._set_headers(status)
        self.wfile.write(body.encode())

    def do_POST(self):
        path = self.path.split('?')[0]
        route = self._route("POST", path)
        if not route:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())
            return

        # Forward headers and body
        device_path = self.path
        device_headers = {}
        token = extract_token(self.headers)
        if token:
            device_headers['Authorization'] = f'Bearer {token}'

        body = self._parse_body()
        status, headers, resp_body = self._forward_request("POST", device_path, device_headers=device_headers, json_body=body)
        self._set_headers(status)
        self.wfile.write(resp_body.encode())

    def log_message(self, format, *args):
        # Suppress default logging
        return

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run():
    server = ThreadedHTTPServer((SERVER_HOST, SERVER_PORT), ProxyHandler)
    print(f"HTTP Proxy Driver running at http://{SERVER_HOST}:{SERVER_PORT}/")
    server.serve_forever()

if __name__ == "__main__":
    run()