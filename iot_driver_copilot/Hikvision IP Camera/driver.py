import os
import threading
import time
import io
import queue
from flask import Flask, Response, request, jsonify

import cv2

# Environment Variables
CAMERA_IP = os.environ.get('CAMERA_IP', '127.0.0.1')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', 554))
CAMERA_RTSP_USER = os.environ.get('CAMERA_RTSP_USER', 'admin')
CAMERA_RTSP_PASS = os.environ.get('CAMERA_RTSP_PASS', '12345')
CAMERA_CHANNEL = os.environ.get('CAMERA_CHANNEL', '101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))
CAMERA_PROTOCOL = os.environ.get('CAMERA_PROTOCOL', 'rtsp')
CAMERA_STREAM = os.environ.get('CAMERA_STREAM', 'main')  # "main" or "sub"

# Compose RTSP URL based on environment
if CAMERA_PROTOCOL == 'rtsp':
    RTSP_URL = f"rtsp://{CAMERA_RTSP_USER}:{CAMERA_RTSP_PASS}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/Streaming/Channels/{CAMERA_CHANNEL}"
else:
    RTSP_URL = ''

app = Flask(__name__)

# Streaming state
streaming_state = {
    'running': False,
    'thread': None,
    'frame_queue': None,
    'stop_event': None,
}

# Camera Streaming Thread
def camera_stream_thread(rtsp_url, frame_queue, stop_event):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        frame_queue.put(None)
        return
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            frame_queue.put(None)
            break
        # Encode as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = jpeg.tobytes()
        try:
            frame_queue.put(frame_bytes, timeout=1)
        except queue.Full:
            pass
    cap.release()

# MJPEG Stream Generator
def mjpeg_generator():
    frame_queue = streaming_state['frame_queue']
    if frame_queue is None:
        return
    while streaming_state['running']:
        try:
            frame = frame_queue.get(timeout=5)
        except queue.Empty:
            frame = None
        if frame is None:
            yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n\r\n'
            break
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video/start', methods=['POST'])
def start_stream():
    if streaming_state['running']:
        return jsonify({'status': 'already running'}), 200
    streaming_state['running'] = True
    streaming_state['frame_queue'] = queue.Queue(maxsize=10)
    streaming_state['stop_event'] = threading.Event()
    streaming_state['thread'] = threading.Thread(
        target=camera_stream_thread,
        args=(RTSP_URL, streaming_state['frame_queue'], streaming_state['stop_event']),
        daemon=True
    )
    streaming_state['thread'].start()
    # Wait until at least one frame is available, or failed
    try:
        frame = streaming_state['frame_queue'].get(timeout=5)
        if frame is None:
            streaming_state['running'] = False
            streaming_state['thread'] = None
            streaming_state['frame_queue'] = None
            streaming_state['stop_event'] = None
            return jsonify({'status': 'error', 'message': 'Unable to connect to camera'}), 500
        else:
            # Put it back for streaming
            streaming_state['frame_queue'].put(frame)
    except queue.Empty:
        streaming_state['running'] = False
        streaming_state['thread'] = None
        streaming_state['frame_queue'] = None
        streaming_state['stop_event'] = None
        return jsonify({'status': 'error', 'message': 'Camera timeout'}), 500
    return jsonify({'status': 'started'}), 200

@app.route('/video/stop', methods=['POST'])
def stop_stream():
    if not streaming_state['running']:
        return jsonify({'status': 'not running'}), 200
    streaming_state['running'] = False
    if streaming_state['stop_event']:
        streaming_state['stop_event'].set()
    if streaming_state['thread']:
        streaming_state['thread'].join(timeout=2)
    streaming_state['thread'] = None
    streaming_state['frame_queue'] = None
    streaming_state['stop_event'] = None
    return jsonify({'status': 'stopped'}), 200

@app.route('/video/live')
def video_feed():
    if not streaming_state['running']:
        return jsonify({'error': 'Stream not started. Use POST /video/start first.'}), 400
    return Response(
        mjpeg_generator(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/')
def index():
    return '''
    <html>
    <head>
    <title>Hikvision Camera Stream</title>
    </head>
    <body>
    <h1>Hikvision Camera MJPEG Stream</h1>
    <img src="/video/live" width="640" />
    <form action="/video/start" method="post">
      <button type="submit">Start Stream</button>
    </form>
    <form action="/video/stop" method="post">
      <button type="submit">Stop Stream</button>
    </form>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)