import os
import json
from flask import Flask, request, Response, jsonify, stream_with_context
import requests

app = Flask(__name__)

# Environment Variables
DEVICE_HOST = os.environ.get("DEVICE_HOST", "localhost")
DEVICE_PORT = os.environ.get("DEVICE_PORT", "8080")
DEVICE_PROTOCOL = os.environ.get("DEVICE_PROTOCOL", "http")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

DEVICE_BASE_URL = f"{DEVICE_PROTOCOL}://{DEVICE_HOST}:{DEVICE_PORT}"

def device_url(path):
    return DEVICE_BASE_URL + path

def get_auth_header():
    auth = request.headers.get("Authorization")
    if auth:
        return {"Authorization": auth}
    return {}

def proxy_device_response(device_resp):
    resp = Response(
        device_resp.content,
        status=device_resp.status_code,
        headers={k: v for k, v in device_resp.headers.items() if k.lower() not in ["content-encoding", "transfer-encoding", "content-length", "connection"]}
    )
    return resp

@app.route("/session/login", methods=["POST"])
def session_login():
    req_data = request.get_json(force=True)
    headers = {"Content-Type": "application/json"}
    r = requests.post(device_url("/session/login"), json=req_data, headers=headers)
    return proxy_device_response(r)

@app.route("/session/logout", methods=["POST"])
def session_logout():
    headers = {"Authorization": request.headers.get("Authorization", "")}
    r = requests.post(device_url("/session/logout"), headers=headers)
    return proxy_device_response(r)

@app.route("/schedules", methods=["GET", "POST"])
def schedules():
    headers = get_auth_header()
    if request.method == "GET":
        r = requests.get(device_url("/schedules"), headers=headers, params=request.args)
        return proxy_device_response(r)
    elif request.method == "POST":
        req_data = request.get_json(force=True)
        headers["Content-Type"] = "application/json"
        r = requests.post(device_url("/schedules"), headers=headers, json=req_data)
        return proxy_device_response(r)

@app.route("/posts", methods=["GET", "POST"])
def posts():
    headers = get_auth_header()
    if request.method == "GET":
        r = requests.get(device_url("/posts"), headers=headers, params=request.args)
        return proxy_device_response(r)
    elif request.method == "POST":
        req_data = request.get_json(force=True)
        headers["Content-Type"] = "application/json"
        r = requests.post(device_url("/posts"), headers=headers, json=req_data)
        return proxy_device_response(r)

@app.route("/search", methods=["GET"])
def search():
    headers = get_auth_header()
    r = requests.get(device_url("/search"), headers=headers, params=request.args)
    return proxy_device_response(r)

@app.route("/chats/<chat_id>/messages", methods=["GET", "POST"])
def chat_messages(chat_id):
    headers = get_auth_header()
    path = f"/chats/{chat_id}/messages"
    if request.method == "GET":
        r = requests.get(device_url(path), headers=headers, params=request.args)
        return proxy_device_response(r)
    elif request.method == "POST":
        req_data = request.get_json(force=True)
        headers["Content-Type"] = "application/json"
        r = requests.post(device_url(path), headers=headers, json=req_data)
        return proxy_device_response(r)

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT)