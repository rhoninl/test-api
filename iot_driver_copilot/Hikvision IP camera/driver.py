import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify
import cv2

app = Flask(__name__)

# Configuration from environment variables
CAMERA_IP = os.environ.get('HIKVISION_CAMERA_IP', '192.168.1.64')
CAMERA_USERNAME = os.environ.get('HIKVISION_CAMERA_USERNAME', 'admin')
CAMERA_PASSWORD = os.environ.get('HIKVISION_CAMERA_PASSWORD', '12345')
CAMERA_RTSP_PORT = int(os.environ.get('HIKVISION_CAMERA_RTSP_PORT', '554'))
CAMERA_STREAM_PATH = os.environ.get('HIKVISION_CAMERA_STREAM_PATH', '/Streaming/Channels/101')
SERVER_HOST = os.environ.get('HTTP_SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('HTTP_SERVER_PORT', '8080'))

RTSP_URL = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{CAMERA_IP}:{CAMERA_RTSP_PORT}{CAMERA_STREAM_PATH}"

# Streaming state
streaming_active = False
frame_queue = queue.Queue(maxsize=10)
stream_thread = None
stream_lock = threading.Lock()
stop_event = threading.Event()

def stream_worker(rtsp_url, queue, stop_event):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        return
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        try:
            queue.put(jpeg.tobytes(), timeout=0.1)
        except queue.Full:
            pass
    cap.release()

def start_stream():
    global streaming_active, stream_thread, stop_event, frame_queue
    with stream_lock:
        if not streaming_active:
            stop_event.clear()
            frame_queue = queue.Queue(maxsize=10)
            stream_thread = threading.Thread(target=stream_worker, args=(RTSP_URL, frame_queue, stop_event))
            stream_thread.daemon = True
            stream_thread.start()
            streaming_active = True

def stop_stream():
    global streaming_active, stream_thread, stop_event
    with stream_lock:
        if streaming_active:
            stop_event.set()
            if stream_thread is not None:
                stream_thread.join(timeout=2)
            streaming_active = False

def gen_mjpeg():
    while streaming_active:
        try:
            frame = frame_queue.get(timeout=1)
        except queue.Empty:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
    yield b''

@app.route('/stream', methods=['POST'])
def activate_stream():
    start_stream()
    return jsonify({'status': 'streaming_started'}), 200

@app.route('/stream', methods=['GET'])
def get_stream():
    if not streaming_active:
        return jsonify({'error': 'stream not active'}), 400
    return Response(gen_mjpeg(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stream', methods=['DELETE'])
def terminate_stream():
    stop_stream()
    return jsonify({'status': 'streaming_stopped'}), 200

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)