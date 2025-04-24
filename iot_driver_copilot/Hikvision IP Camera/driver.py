import os
import threading
import time
from flask import Flask, Response, jsonify, request
import cv2

# --- Environment Variables ---
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_USERNAME = os.environ.get('RTSP_USERNAME', 'admin')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '12345')
RTSP_PATH = os.environ.get('RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))
STREAM_FORMAT = os.environ.get('STREAM_FORMAT', 'MJPEG')  # 'MJPEG' or 'H264'

# --- Camera Stream Control ---
stream_active = False
stream_lock = threading.Lock()
cap = None

def get_rtsp_url():
    return f'rtsp://{RTSP_USERNAME}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}'

def open_camera():
    global cap
    if cap is None or not cap.isOpened():
        rtsp_url = get_rtsp_url()
        cap = cv2.VideoCapture(rtsp_url)
    return cap

def close_camera():
    global cap
    if cap is not None:
        cap.release()
        cap = None

# --- Flask App ---
app = Flask(__name__)

@app.route('/start', methods=['POST'])
def start_stream():
    global stream_active
    with stream_lock:
        if not stream_active:
            open_camera()
            stream_active = True
        return jsonify({'status': 'streaming_started'}), 200

@app.route('/stop', methods=['POST'])
def stop_stream():
    global stream_active
    with stream_lock:
        if stream_active:
            stream_active = False
            close_camera()
        return jsonify({'status': 'streaming_stopped'}), 200

def gen_mjpeg():
    """Generator that yields MJPEG frames from the camera."""
    global stream_active
    while True:
        with stream_lock:
            if not stream_active or cap is None:
                break
            ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = jpeg.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    # Stream ended, cleanup
    close_camera()

@app.route('/stream', methods=['GET'])
def stream_video():
    if STREAM_FORMAT.upper() == 'MJPEG':
        with stream_lock:
            if not stream_active:
                return jsonify({'error': 'stream_not_active'}), 400
        return Response(gen_mjpeg(), mimetype='multipart/x-mixed-replace; boundary=frame')
    else:
        return jsonify({'error': 'unsupported_stream_format'}), 400

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)