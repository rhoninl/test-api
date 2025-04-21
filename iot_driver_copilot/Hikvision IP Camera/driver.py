import os
import io
import threading
import time
import requests
from flask import Flask, Response, jsonify, stream_with_context

# Configuration via environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
DEVICE_USER = os.environ.get('DEVICE_USER', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))
RTSP_PORT = int(os.environ.get('RTSP_PORT', '554'))
HTTP_PORT = int(os.environ.get('HTTP_PORT', '80'))
SNAPSHOT_PATH = os.environ.get('SNAPSHOT_PATH', '/ISAPI/Streaming/channels/101/picture')
MJPEG_PATH = os.environ.get('MJPEG_PATH', '/ISAPI/Streaming/channels/101/httpPreview')
RTSP_CHANNEL = os.environ.get('RTSP_CHANNEL', '101')

# Flask App
app = Flask(__name__)

def get_rtsp_url():
    return f'rtsp://{DEVICE_USER}:{DEVICE_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/Streaming/Channels/{RTSP_CHANNEL}'

def get_snapshot_url():
    return f'http://{DEVICE_IP}:{HTTP_PORT}{SNAPSHOT_PATH}'

def get_mjpeg_url():
    return f'http://{DEVICE_IP}:{HTTP_PORT}{MJPEG_PATH}'

def gen_mjpeg_stream():
    url = get_mjpeg_url()
    auth = (DEVICE_USER, DEVICE_PASSWORD)
    headers = {'Accept': 'multipart/x-mixed-replace'}
    try:
        with requests.get(url, stream=True, auth=auth, timeout=10, headers=headers) as r:
            r.raise_for_status()
            boundary = None
            ctype = r.headers.get('Content-Type', '')
            if 'boundary=' in ctype:
                boundary = ctype.split('boundary=')[1].strip().strip('"')
            else:
                boundary = '--myboundary'
            boundary = boundary if boundary.startswith('--') else '--' + boundary
            buffer = b''
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    break
                buffer += chunk
                while True:
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9')
                    if start != -1 and end != -1 and end > start:
                        jpg = buffer[start:end+2]
                        buffer = buffer[end+2:]
                        part = (
                            b'--frame\r\n'
                            b'Content-Type: image/jpeg\r\n\r\n' +
                            jpg + b'\r\n'
                        )
                        yield part
                    else:
                        # Not enough data for a full JPEG
                        break
    except Exception as e:
        # End stream on error
        return

@app.route('/camera/live', methods=['GET'])
def camera_live():
    return Response(
        stream_with_context(gen_mjpeg_stream()),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/camera/rtsp-url', methods=['GET'])
def camera_rtsp_url():
    return jsonify({
        'rtsp_url': get_rtsp_url()
    })

@app.route('/camera/snapshot', methods=['GET'])
def camera_snapshot():
    url = get_snapshot_url()
    try:
        r = requests.get(url, auth=(DEVICE_USER, DEVICE_PASSWORD), stream=True, timeout=10)
        r.raise_for_status()
        return Response(
            r.content,
            mimetype='image/jpeg',
            headers={
                'Content-Disposition': 'inline; filename=snapshot.jpg'
            }
        )
    except Exception as e:
        return jsonify({'error': 'Failed to get snapshot', 'details': str(e)}), 500

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)