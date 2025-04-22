import os
import base64
import threading
import requests
from flask import Flask, Response, stream_with_context

# Read configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
DEVICE_RTSP_PORT = os.environ.get('DEVICE_RTSP_PORT', '554')
DEVICE_HTTP_PORT = os.environ.get('DEVICE_HTTP_PORT', '80')
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', '')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '1')  # Some cameras use channel

if not DEVICE_IP:
    raise RuntimeError('DEVICE_IP environment variable is required')

# Dahua snapshot and MJPEG info (many Dahua IP cams support MJPEG over HTTP)
def get_mjpeg_url():
    # MJPEG HTTP path (Dahua default: /cgi-bin/mjpg/video.cgi)
    # Some models may require a different path or param, adjust as needed
    return f"http://{DEVICE_IP}:{DEVICE_HTTP_PORT}/cgi-bin/mjpg/video.cgi?channel={CAMERA_CHANNEL}&subtype=0"

def get_auth():
    if DEVICE_USERNAME and DEVICE_PASSWORD:
        return (DEVICE_USERNAME, DEVICE_PASSWORD)
    return None

def mjpeg_stream():
    url = get_mjpeg_url()
    auth = get_auth()
    with requests.get(url, auth=auth, stream=True, timeout=10) as r:
        r.raise_for_status()
        boundary = None

        content_type = r.headers.get('Content-Type', '')
        if 'boundary=' in content_type:
            boundary = content_type.split('boundary=')[-1]
            if boundary.startswith('"') and boundary.endswith('"'):
                boundary = boundary[1:-1]
        if not boundary:
            boundary = '--myboundary'

        def generate():
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    break
                yield chunk
        return boundary, generate()

app = Flask(__name__)

@app.route('/stream', methods=['GET'])
def stream():
    try:
        boundary, generator = mjpeg_stream()
    except Exception as e:
        return Response(f"Could not connect to camera: {str(e)}", status=502, mimetype='text/plain')
    return Response(
        stream_with_context(generator),
        mimetype=f'multipart/x-mixed-replace; boundary={boundary}'
    )

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)