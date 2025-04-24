import os
import threading
import time
import io
from flask import Flask, Response, request, jsonify, stream_with_context, abort
import cv2

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP')
DEVICE_RTSP_PORT = int(os.environ.get('DEVICE_RTSP_PORT', 554))
DEVICE_USERNAME = os.environ.get('DEVICE_USERNAME', 'admin')
DEVICE_PASSWORD = os.environ.get('DEVICE_PASSWORD', '12345')
RTSP_PATH = os.environ.get('RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

# RTSP URL construction
RTSP_URL = f'rtsp://{DEVICE_USERNAME}:{DEVICE_PASSWORD}@{DEVICE_IP}:{DEVICE_RTSP_PORT}/{RTSP_PATH}'

app = Flask(__name__)

# Global control of streaming session
streaming = {
    'active': False,
    'lock': threading.Lock(),
    'thread': None,
    'stop_event': threading.Event()
}

def video_stream_generator(stop_event):
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        cap.release()
        return

    try:
        while not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            # Encode frame as JPEG
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    finally:
        cap.release()

@app.route('/start', methods=['POST'])
def start_stream():
    with streaming['lock']:
        if streaming['active']:
            # Already running
            return jsonify({
                'status': 'already streaming',
                'url': f'http://{SERVER_HOST}:{SERVER_PORT}/video'
            }), 200
        # Start streaming
        streaming['stop_event'].clear()
        streaming['active'] = True
    return jsonify({
        'status': 'streaming started',
        'url': f'http://{SERVER_HOST}:{SERVER_PORT}/video'
    }), 200

@app.route('/stop', methods=['POST'])
def stop_stream():
    with streaming['lock']:
        if not streaming['active']:
            return jsonify({'status': 'not streaming'}), 200
        streaming['stop_event'].set()
        streaming['active'] = False
    return jsonify({'status': 'streaming stopped'}), 200

@app.route('/video')
def video_feed():
    with streaming['lock']:
        if not streaming['active']:
            abort(403, description="Streaming session is not active. Start it via /start.")
        # Provide the live MJPEG stream
        return Response(
            stream_with_context(video_stream_generator(streaming['stop_event'])),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)