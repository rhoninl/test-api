import os
import io
import threading
import requests
import time
from flask import Flask, Response, request, jsonify, stream_with_context

# Environment Variables (Required)
DEVICE_IP = os.getenv("DEVICE_IP", "192.168.1.64")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", "80"))
DEVICE_USER = os.getenv("DEVICE_USER", "admin")
DEVICE_PASS = os.getenv("DEVICE_PASS", "12345")
HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8080"))
SNAPSHOT_PATH = os.getenv("SNAPSHOT_PATH", "/ISAPI/Streaming/channels/101/picture")
STREAM_PATH = os.getenv("STREAM_PATH", "/ISAPI/Streaming/channels/101/httpPreview")
INFO_PATH = os.getenv("INFO_PATH", "/ISAPI/System/deviceInfo")
RECORD_START_PATH = os.getenv("RECORD_START_PATH", "/ISAPI/ContentMgmt/record/control/manual/start")
RECORD_STOP_PATH = os.getenv("RECORD_STOP_PATH", "/ISAPI/ContentMgmt/record/control/manual/stop")

app = Flask(__name__)

def get_device_url(path):
    return f"http://{DEVICE_IP}:{DEVICE_PORT}{path}"

def isapi_get(path, stream=False, timeout=10):
    url = get_device_url(path)
    resp = requests.get(url, auth=(DEVICE_USER, DEVICE_PASS), stream=stream, timeout=timeout)
    resp.raise_for_status()
    return resp

def isapi_post(path, data=None, headers=None):
    url = get_device_url(path)
    resp = requests.post(url, auth=(DEVICE_USER, DEVICE_PASS), data=data, headers=headers)
    resp.raise_for_status()
    return resp

# /info : Device info and status
@app.route('/info', methods=['GET'])
def device_info():
    try:
        resp = isapi_get(INFO_PATH)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/xml'))
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# /snap : On-demand snapshot (image)
@app.route('/snap', methods=['GET'])
def snapshot():
    try:
        resp = isapi_get(SNAPSHOT_PATH, stream=True)
        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        return Response(resp.raw, content_type=content_type)
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# /feed : Live video HTTP proxy (convert ISAPI HTTP MJPEG if available)
@app.route('/feed', methods=['GET'])
def live_feed():
    try:
        # Try HTTP preview (MJPEG or multipart JPEG via ISAPI)
        resp = isapi_get(STREAM_PATH, stream=True, timeout=30)
        def generate():
            for chunk in resp.iter_content(chunk_size=4096):
                if not chunk:
                    break
                yield chunk
        content_type = resp.headers.get('Content-Type', 'multipart/x-mixed-replace;boundary=--boundary')
        return Response(stream_with_context(generate()), content_type=content_type)
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# /record/start : Start recording (manual trigger)
@app.route('/record/start', methods=['POST'])
def start_record():
    try:
        # For most Hikvision ISAPI-compliant devices, this POST triggers manual recording
        headers = {'Content-Type': 'application/xml'}
        # The body may be empty or require a simple XML depending on model
        data = "<ManualRecord><action>start</action></ManualRecord>"
        resp = isapi_post(RECORD_START_PATH, data=data, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/xml'))
    except Exception as e:
        return jsonify({'error': str(e)}), 502

# /record/stop : Stop ongoing recording
@app.route('/record/stop', methods=['POST'])
def stop_record():
    try:
        headers = {'Content-Type': 'application/xml'}
        data = "<ManualRecord><action>stop</action></ManualRecord>"
        resp = isapi_post(RECORD_STOP_PATH, data=data, headers=headers)
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type', 'application/xml'))
    except Exception as e:
        return jsonify({'error': str(e)}), 502

if __name__ == '__main__':
    app.run(host=HTTP_HOST, port=HTTP_PORT, threaded=True)