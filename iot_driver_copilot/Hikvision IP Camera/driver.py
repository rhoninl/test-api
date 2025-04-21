import os
import io
import threading
import queue

from flask import Flask, Response, request, abort
import cv2
import requests

# Environment variable configuration
CAMERA_IP = os.environ.get('CAMERA_IP')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')
RTSP_USER = os.environ.get('RTSP_USER', '')
RTSP_PASS = os.environ.get('RTSP_PASS', '')

HTTP_SNAPSHOT_PORT = int(os.environ.get('HTTP_SNAPSHOT_PORT', 80))
SNAPSHOT_PATH = os.environ.get('SNAPSHOT_PATH', '/ISAPI/Streaming/channels/101/picture')
SNAPSHOT_USER = os.environ.get('SNAPSHOT_USER', RTSP_USER)
SNAPSHOT_PASS = os.environ.get('SNAPSHOT_PASS', RTSP_PASS)

SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

app = Flask(__name__)

# Shared state for stream
streaming_thread = None
streaming_active = threading.Event()
frame_queue = queue.Queue(maxsize=10)

def get_rtsp_url():
    auth = ''
    if RTSP_USER and RTSP_PASS:
        auth = f'{RTSP_USER}:{RTSP_PASS}@'
    return f'rtsp://{auth}{CAMERA_IP}:{RTSP_PORT}{RTSP_PATH}'

def get_snapshot_url():
    return f'http://{CAMERA_IP}:{HTTP_SNAPSHOT_PORT}{SNAPSHOT_PATH}'

def rtsp_stream_worker(url):
    cap = cv2.VideoCapture(url)
    try:
        while streaming_active.is_set():
            ret, frame = cap.read()
            if not ret:
                continue
            # Encode frame as JPEG
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            # Put frame in queue
            try:
                frame_queue.put(jpeg.tobytes(), timeout=0.1)
            except queue.Full:
                pass  # Drop frame if queue is full
    finally:
        cap.release()
        with frame_queue.mutex:
            frame_queue.queue.clear()

def generate_mjpeg():
    while streaming_active.is_set():
        try:
            frame = frame_queue.get(timeout=1)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/stream', methods=['PUT', 'DELETE'])
def stream_control():
    global streaming_thread
    if request.method == 'PUT':
        if streaming_active.is_set():
            return ('{"result": "already streaming"}', 200, {'Content-Type': 'application/json'})
        # Start streaming thread
        streaming_active.set()
        rtsp_url = get_rtsp_url()
        streaming_thread = threading.Thread(target=rtsp_stream_worker, args=(rtsp_url,), daemon=True)
        streaming_thread.start()
        return ('{"result": "stream started"}', 200, {'Content-Type': 'application/json'})
    elif request.method == 'DELETE':
        streaming_active.clear()
        if streaming_thread and streaming_thread.is_alive():
            streaming_thread.join(timeout=2)
        with frame_queue.mutex:
            frame_queue.queue.clear()
        return ('{"result": "stream stopped"}', 200, {'Content-Type': 'application/json'})
    else:
        abort(405)

@app.route('/stream', methods=['GET'])
def stream_view():
    if not streaming_active.is_set():
        return ('{"error": "stream not started"}', 400, {'Content-Type': 'application/json'})
    return Response(generate_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/snapshot', methods=['GET'])
def snapshot():
    url = get_snapshot_url()
    auth = None
    if SNAPSHOT_USER and SNAPSHOT_PASS:
        auth = (SNAPSHOT_USER, SNAPSHOT_PASS)
    try:
        resp = requests.get(url, auth=auth, timeout=5)
        if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image'):
            return Response(resp.content, mimetype='image/jpeg')
        else:
            abort(502)
    except Exception:
        abort(504)

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)