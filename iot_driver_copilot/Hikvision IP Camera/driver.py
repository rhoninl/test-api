import os
import threading
import time
from flask import Flask, Response, request, jsonify, stream_with_context
import cv2

# Configuration from environment variables
CAMERA_RTSP_URL = os.environ.get("CAMERA_RTSP_URL")  # e.g. 'rtsp://user:pass@192.168.1.100:554/Streaming/Channels/101'
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
HTTP_VIDEO_PATH = os.environ.get("HTTP_VIDEO_PATH", "/video/stream")
FRAME_RATE = float(os.environ.get("FRAME_RATE", "20"))

if not CAMERA_RTSP_URL:
    raise RuntimeError("CAMERA_RTSP_URL environment variable not set")

app = Flask(__name__)

class VideoStream:
    def __init__(self, rtsp_url, frame_rate=20):
        self.rtsp_url = rtsp_url
        self.frame_rate = frame_rate
        self.cap = None
        self.running = False
        self.lock = threading.Lock()
        self.latest_frame = None
        self.thread = None

    def start(self):
        with self.lock:
            if not self.running:
                self.running = True
                self.cap = cv2.VideoCapture(self.rtsp_url)
                if not self.cap.isOpened():
                    self.running = False
                    raise RuntimeError("Cannot open video stream")
                self.thread = threading.Thread(target=self.update, daemon=True)
                self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            if self.cap:
                self.cap.release()
                self.cap = None
            self.latest_frame = None

    def update(self):
        while self.running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                ret2, jpeg = cv2.imencode('.jpg', frame)
                if ret2:
                    self.latest_frame = jpeg.tobytes()
            time.sleep(1.0 / self.frame_rate)
        with self.lock:
            if self.cap:
                self.cap.release()
                self.cap = None

    def get_frame(self):
        return self.latest_frame

video_stream = VideoStream(CAMERA_RTSP_URL, frame_rate=FRAME_RATE)

@app.route("/video/start", methods=["POST"])
def start_video():
    try:
        video_stream.start()
        return jsonify({"status": "video stream started"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/video/stop", methods=["POST"])
def stop_video():
    video_stream.stop()
    return jsonify({"status": "video stream stopped"}), 200

@app.route(HTTP_VIDEO_PATH, methods=["GET"])
def stream_video():
    def generate():
        while True:
            frame = video_stream.get_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            else:
                time.sleep(0.1)
    if not video_stream.running:
        return jsonify({"error": "Video stream not started. Please POST /video/start first."}), 400
    return Response(stream_with_context(generate()),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/", methods=["GET"])
def index():
    html = f"""
    <html>
    <head>
        <title>Hikvision IP Camera Live Stream</title>
    </head>
    <body>
        <h1>Hikvision IP Camera Live Stream</h1>
        <img src="{HTTP_VIDEO_PATH}" width="720" />
        <br/>
        <form action="/video/start" method="post">
            <button type="submit">Start Stream</button>
        </form>
        <form action="/video/stop" method="post">
            <button type="submit">Stop Stream</button>
        </form>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)