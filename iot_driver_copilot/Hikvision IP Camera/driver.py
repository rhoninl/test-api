import os
import threading
import io
import av
import queue
from flask import Flask, Response, stream_with_context

# Get configuration from environment variables
DEVICE_IP = os.environ.get("DEVICE_IP")
RTSP_PORT = int(os.environ.get("RTSP_PORT", "554"))
RTSP_USER = os.environ.get("RTSP_USER", "")
RTSP_PASS = os.environ.get("RTSP_PASS", "")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))
RTSP_PATH = os.environ.get("RTSP_PATH", "Streaming/Channels/101")

# RTSP URL construction
if RTSP_USER and RTSP_PASS:
    RTSP_URL = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"
else:
    RTSP_URL = f"rtsp://{DEVICE_IP}:{RTSP_PORT}/{RTSP_PATH}"

app = Flask(__name__)

class RTSPtoMJPEGStreamer:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.clients = []
        self.frame_queue = queue.Queue(maxsize=50)
        self.running = False
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def start(self):
        if not self.running:
            self.running = True
            self.thread.start()

    def stop(self):
        self.running = False

    def _worker(self):
        try:
            container = av.open(self.rtsp_url)
            stream = next(s for s in container.streams if s.type == "video")
            for packet in container.demux(stream):
                if not self.running:
                    break
                for frame in packet.decode():
                    img = frame.to_image()
                    with io.BytesIO() as output:
                        img.save(output, format="JPEG")
                        jpeg_bytes = output.getvalue()
                        try:
                            self.frame_queue.put(jpeg_bytes, timeout=1)
                        except queue.Full:
                            # Drop frames if queue is full
                            pass
        except Exception:
            pass

    def get_frame(self):
        try:
            return self.frame_queue.get(timeout=5)
        except queue.Empty:
            return None

streamer = RTSPtoMJPEGStreamer(RTSP_URL)
streamer.start()

def mjpeg_stream():
    while True:
        frame = streamer.get_frame()
        if frame is None:
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
            frame + b"\r\n"
        )

@app.route("/stream", methods=["GET"])
def stream_video():
    return Response(
        stream_with_context(mjpeg_stream()),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)