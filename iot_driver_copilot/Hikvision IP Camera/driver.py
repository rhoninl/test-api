import os
import threading
import queue
import time
from flask import Flask, Response, request, jsonify
import cv2

# Get environment variables for configuration
CAMERA_IP = os.environ.get('CAMERA_IP')
CAMERA_RTSP_PORT = int(os.environ.get('CAMERA_RTSP_PORT', '554'))
CAMERA_RTSP_USER = os.environ.get('CAMERA_RTSP_USER', 'admin')
CAMERA_RTSP_PASS = os.environ.get('CAMERA_RTSP_PASS', '12345')
CAMERA_RTSP_PATH = os.environ.get('CAMERA_RTSP_PATH', 'Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

# Camera session state
streaming_flag = threading.Event()
frame_queue = queue.Queue(maxsize=100)
stream_thread = None
stream_lock = threading.Lock()

app = Flask(__name__)

def get_rtsp_url():
    return f"rtsp://{CAMERA_RTSP_USER}:{CAMERA_RTSP_PASS}@{CAMERA_IP}:{CAMERA_RTSP_PORT}/{CAMERA_RTSP_PATH}"

def video_capture_worker():
    rtsp_url = get_rtsp_url()
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        streaming_flag.clear()
        cap.release()
        return
    while streaming_flag.is_set():
        ret, frame = cap.read()
        if not ret:
            break
        # Encode frame as JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        # Put jpeg bytes into the queue
        try:
            frame_queue.put(jpeg.tobytes(), timeout=1)
        except queue.Full:
            # Drop frame if queue is full
            continue
    cap.release()

def start_streaming():
    global stream_thread
    with stream_lock:
        if not streaming_flag.is_set():
            streaming_flag.set()
            # Empty the frame queue before starting
            while not frame_queue.empty():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    break
            stream_thread = threading.Thread(target=video_capture_worker, daemon=True)
            stream_thread.start()

def stop_streaming():
    with stream_lock:
        if streaming_flag.is_set():
            streaming_flag.clear()
        # Empty the frame queue after stopping
        while not frame_queue.empty():
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                break

@app.route('/start', methods=['POST'])
@app.route('/video/start', methods=['POST'])
def api_start():
    start_streaming()
    return jsonify({"status": "streaming started"}), 200

@app.route('/stop', methods=['POST'])
@app.route('/video/stop', methods=['POST'])
def api_stop():
    stop_streaming()
    return jsonify({"status": "streaming stopped"}), 200

def mjpeg_generator():
    while streaming_flag.is_set():
        try:
            frame = frame_queue.get(timeout=5)
        except queue.Empty:
            break
        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
        )

@app.route('/stream', methods=['GET'])
def stream():
    if not streaming_flag.is_set():
        return jsonify({"error": "stream not started"}), 503
    return Response(mjpeg_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)