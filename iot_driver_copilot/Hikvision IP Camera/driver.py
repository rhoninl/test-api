import os
import io
import cv2
import threading
import uuid
import time
from flask import Flask, Response, jsonify, request, abort

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
RTSP_PORT = int(os.environ.get('RTSP_PORT', 554))
RTSP_USER = os.environ.get('RTSP_USER', 'admin')
RTSP_PASS = os.environ.get('RTSP_PASS', '12345')
RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))

RTSP_URL_TEMPLATE = "rtsp://{user}:{password}@{ip}:{port}{path}"

app = Flask(__name__)

sessions = {}
sessions_lock = threading.Lock()


class StreamSession:
    def __init__(self, session_id, rtsp_url):
        self.session_id = session_id
        self.rtsp_url = rtsp_url
        self.active = True
        self.frame = None
        self.last_frame_time = 0
        self.thread = threading.Thread(target=self._capture_thread, daemon=True)
        self.cap = None
        self.lock = threading.Lock()
        self.thread.start()

    def _capture_thread(self):
        cap = cv2.VideoCapture(self.rtsp_url)
        self.cap = cap
        if not cap.isOpened():
            self.active = False
            return
        while self.active:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.2)
                continue
            with self.lock:
                self.frame = frame
                self.last_frame_time = time.time()
        cap.release()

    def get_jpeg_frame(self):
        with self.lock:
            if self.frame is None:
                return None
            ret, jpeg = cv2.imencode('.jpg', self.frame)
            if not ret:
                return None
            return jpeg.tobytes()

    def stop(self):
        self.active = False
        if self.cap is not None:
            self.cap.release()


def build_rtsp_url():
    return RTSP_URL_TEMPLATE.format(
        user=RTSP_USER,
        password=RTSP_PASS,
        ip=DEVICE_IP,
        port=RTSP_PORT,
        path=RTSP_PATH
    )


@app.route('/video-session', methods=['POST'])
def start_session():
    with sessions_lock:
        # Only allow a single session for this driver
        if sessions:
            # Only one session supported at a time
            sid, sess = next(iter(sessions.items()))
            return jsonify({
                'session_id': sid,
                'access_url': f'http://{SERVER_HOST}:{SERVER_PORT}/video-session',
                'rtsp_url': sess.rtsp_url
            })
        session_id = str(uuid.uuid4())
        rtsp_url = build_rtsp_url()
        session = StreamSession(session_id, rtsp_url)
        # Give some time to initialize the stream
        time.sleep(1)
        if not session.active or session.frame is None:
            session.stop()
            return jsonify({'error': 'Unable to start stream'}), 500
        sessions[session_id] = session
        return jsonify({
            'session_id': session_id,
            'access_url': f'http://{SERVER_HOST}:{SERVER_PORT}/video-session',
            'rtsp_url': rtsp_url
        })


@app.route('/video-session', methods=['GET'])
def get_session():
    with sessions_lock:
        if not sessions:
            return jsonify({'error': 'No active session'}), 404
        sid, sess = next(iter(sessions.items()))

    # Serve MJPEG stream
    def mjpeg_stream():
        while True:
            frame = sess.get_jpeg_frame()
            if frame is None:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.03)  # ~30 fps

    return Response(mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/video-session', methods=['DELETE'])
def stop_session():
    with sessions_lock:
        if not sessions:
            return jsonify({'error': 'No active session'}), 404
        sid, sess = next(iter(sessions.items()))
        sess.stop()
        del sessions[sid]
        return jsonify({'status': 'stopped'}), 200


if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)