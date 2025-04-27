import os
import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import roslibpy

# Configuration from environment variables
ROSBRIDGE_HOST = os.environ.get('ROSBRIDGE_HOST', 'localhost')
ROSBRIDGE_PORT = int(os.environ.get('ROSBRIDGE_PORT', '9090'))
HTTP_SERVER_HOST = os.environ.get('HTTP_SERVER_HOST', '0.0.0.0')
HTTP_SERVER_PORT = int(os.environ.get('HTTP_SERVER_PORT', '8080'))
ROS_IMAGE_TOPIC = os.environ.get('ROS_IMAGE_TOPIC', '/david')
ROS_IMAGE_TYPE = os.environ.get('ROS_IMAGE_TYPE', 'sensor_msgs/Image')


class RosPublisher:
    def __init__(self, host, port, topic, msg_type):
        self.client = roslibpy.Ros(host=host, port=port)
        self.topic_name = topic
        self.msg_type = msg_type
        self.topic = roslibpy.Topic(self.client, self.topic_name, self.msg_type)
        self._connected = False

    def connect(self):
        if not self._connected:
            self.client.run()
            self._connected = True

    def disconnect(self):
        if self._connected:
            self.topic.unadvertise()
            self.client.terminate()
            self._connected = False

    def publish(self, msg):
        self.connect()
        self.topic.advertise()
        self.topic.publish(roslibpy.Message(msg))


ros_publisher = RosPublisher(
    ROSBRIDGE_HOST, ROSBRIDGE_PORT, ROS_IMAGE_TOPIC, ROS_IMAGE_TYPE
)


class Handler(BaseHTTPRequestHandler):

    def _set_headers(self, status_code=200, content_type='application/json'):
        self.send_response(status_code)
        self.send_header('Content-type', content_type)
        self.end_headers()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/pubimg':
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Empty body.'}).encode())
                return

            post_data = self.rfile.read(content_length)
            try:
                # Accept both JSON and application/x-www-form-urlencoded
                if self.headers.get('Content-Type', '').startswith('application/json'):
                    msg = json.loads(post_data)
                else:
                    # Assume form-data with 'msg' field as JSON string
                    fields = parse_qs(post_data.decode())
                    if 'msg' in fields:
                        msg = json.loads(fields['msg'][0])
                    else:
                        raise ValueError('No msg field found.')
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': f'Invalid JSON: {str(e)}'}).encode())
                return

            try:
                ros_publisher.publish(msg)
                self._set_headers(200)
                self.wfile.write(json.dumps({'status': 'success', 'message': 'Image published to /david'}).encode())
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({'error': f'Failed to publish: {str(e)}'}).encode())
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Not found'}).encode())

    def do_GET(self):
        # Simple index/help
        self._set_headers(200)
        self.wfile.write(json.dumps({
            'endpoints': [
                {
                    'method': 'POST',
                    'path': '/pubimg',
                    'description': 'Publishes an image to the /david ROS topic. Users submit image data in ROS image message format, which is then transmitted via roslibpy.'
                }
            ]
        }).encode())

    def log_message(self, format, *args):
        # Silence the default log
        return


def run_server():
    server_address = (HTTP_SERVER_HOST, HTTP_SERVER_PORT)
    httpd = HTTPServer(server_address, Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ros_publisher.disconnect()


if __name__ == '__main__':
    run_server()