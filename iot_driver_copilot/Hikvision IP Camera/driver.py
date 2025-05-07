import os
import uuid
import threading
import cv2
import time
from flask import Flask, Response, request, jsonify, abort

# Configuration from environment variables
DEVICE_IP = os.environ.get('DEVICE_IP', '192.168.1.64')
RTSP_PORT = int(os.environ.get('RTSP_PORT', '554'))
RTSP_USER = os.environ.get('RTSP_USER', 'admin')
RTSP_PASSWORD = os.environ.get('RTSP_PASSWORD', '12345')
RTSP_PATH = os.environ.get('RTSP_PATH', '/Streaming/Channels/101')
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('SERVER_PORT', '8080'))

RTSP_URL = f'rtsp://{RTSP_USER}:{RTSP_PASSWORD}@{DEVICE_IP}:{RTSP_PORT}{RTSP_PATH}'

app = Flask(__name__)

# Session state
sessions = {}
sessions_lock = threading.Lock()

def gen_mjpeg_frames(cap, session_id):
    while True:
        with sessions_lock:
            session = sessions.get(session_id)
            if not session or not session['active']:
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
    cap.release()

@app.route('/video-session', methods=['POST'])
def start_video_session():
    with sessions_lock:
        # Close previous sessions
        for sid in list(sessions.keys()):
            sessions[sid]['active'] = False
            if sessions[sid]['capture']:
                sessions[sid]['capture'].release()
            del sessions[sid]
        # Create new session
        session_id = str(uuid.uuid4())
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            return jsonify({"error": "Failed to connect to camera RTSP stream"}), 500
        sessions[session_id] = {
            'capture': cap,
            'active': True,
            'created': time.time(),
            'access_url': f'http://{SERVER_HOST}:{SERVER_PORT}/video-session?session_id={session_id}',
            'rtsp_url': RTSP_URL
        }
        return jsonify({
            'session_id': session_id,
            'access_url': sessions[session_id]['access_url'],
            'rtsp_url': RTSP_URL
        })

@app.route('/video-session', methods=['GET'])
def get_video_session():
    session_id = request.args.get('session_id')
    with sessions_lock:
        if not session_id or session_id not in sessions or not sessions[session_id]['active']:
            return jsonify({"error": "No active session. Start one with POST /video-session."}), 404
        cap = sessions[session_id]['capture']
    return Response(
        gen_mjpeg_frames(cap, session_id),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/video-session', methods=['DELETE'])
def stop_video_session():
    session_id = request.args.get('session_id')
    with sessions_lock:
        if not session_id or session_id not in sessions:
            return jsonify({'error': 'Session not found'}), 404
        sessions[session_id]['active'] = False
        if sessions[session_id]['capture']:
            sessions[session_id]['capture'].release()
        del sessions[session_id]
    return jsonify({'status': 'session stopped'})

if __name__ == '__main__':
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)