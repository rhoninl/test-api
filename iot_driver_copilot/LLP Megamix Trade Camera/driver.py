import os
import threading
import requests
from flask import Flask, Response, stream_with_context

app = Flask(__name__)

# Environment Variables
CAMERA_IP = os.environ.get("CAMERA_IP", "127.0.0.1")
CAMERA_RTSP_PORT = os.environ.get("CAMERA_RTSP_PORT", "554")
CAMERA_HTTP_PORT = os.environ.get("CAMERA_HTTP_PORT", "80")
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASS = os.environ.get("CAMERA_PASS", "admin")
CAMERA_CHANNEL = os.environ.get("CAMERA_CHANNEL", "1")
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8080"))

# Dahua MJPEG HTTP stream URL (supported by most Dahua cameras)
# Example: http://<CAMERA_IP>/cgi-bin/mjpg/video.cgi?channel=1&subtype=0
# subtype=0: main stream, subtype=1: sub stream
CAMERA_MJPEG_URL = (
    f"http://{CAMERA_IP}:{CAMERA_HTTP_PORT}/cgi-bin/mjpg/video.cgi"
    f"?channel={CAMERA_CHANNEL}&subtype=1"
)

def mjpeg_proxy():
    """
    Generator that proxies MJPEG stream from camera to HTTP client.
    """
    with requests.get(
        CAMERA_MJPEG_URL,
        auth=(CAMERA_USER, CAMERA_PASS),
        stream=True,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as r:
        r.raise_for_status()
        boundary = None
        content_type = r.headers.get("Content-Type", "")
        if "boundary=" in content_type:
            boundary = content_type.split("boundary=")[-1]
        else:
            boundary = "--myboundary"
        boundary = boundary.strip()
        yield f"--{boundary}\r\n".encode()

        for chunk in r.iter_content(chunk_size=4096):
            if chunk:
                yield chunk

@app.route("/stream", methods=["GET"])
def stream():
    """
    Access a live video stream from the IP camera.
    Returns MJPEG stream for browser consumption.
    """
    # We'll forward all headers and content-type as received from camera
    def generate():
        try:
            for chunk in mjpeg_proxy():
                yield chunk
        except Exception:
            # Camera connection lost or client disconnected
            pass

    # A proper 'multipart/x-mixed-replace' MJPEG stream
    resp_headers = {
        "Cache-Control": "no-cache, private",
        "Pragma": "no-cache",
        "Connection": "close"
    }
    resp = Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=--myboundary",
        headers=resp_headers
    )
    return resp

if __name__ == "__main__":
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)