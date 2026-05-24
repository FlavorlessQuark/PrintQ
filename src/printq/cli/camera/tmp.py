"""Camera control commands."""

from logging import getLogger
import threading
import click
import redis
from printq.camera.qwen import Qwen
import threading

logger = getLogger(__name__)
r = redis.Redis(host='localhost', port=6379, decode_responses=True) 
@click.group(name="camera")
@click.pass_context
def camera_commands(ctx):
    """Camera control utilities"""
    ctx.ensure_object(dict)

def start_loop():
    pubsub = r.pubsub()
    pubsub.subscribe('start')
    print("Subscribed to Redis channel 'time_control'. Listening for messages...")
    for message in pubsub.listen():
        print(f"Received message: {message}")

@camera_commands.command(name="start")
def start():
    """Start the camera and stream the depth feed until 'q' is pressed."""
    import cv2
    import base64
    import numpy as np
    import json
    from printq.camera.realsense import RealsenseCamera

    camera = RealsenseCamera()
    qwen = Qwen()
    show = 1
    threading.Thread(target=start_loop, daemon=True).start()
    try:
        while True:
            if show:
                camera.show_frame()
                # scamera.get_obj()
            # waitKey(1) both pumps the OpenCV GUI event loop (so the window
            # updates) and polls for a quit key. Returns -1 if no key.
            key = cv2.waitKey(1)
            if key:
                match key & 0xFF:
                    case 113:#q
                        break
                    case 115:#s
                        show ^= 1
                    case 100:#d
                        show ^= 1
                        pic = camera.take_pic()
                        status, message = qwen.get_print_status(pic)
                        r.publish('status', json.dumps({
                                    "success": status,
                                    "desc": message,
                                    "image": pic}))

    finally:
        camera.close()

@camera_commands.command(name="serial")
def serial():

    from printq.camera.realsense import RealsenseCamera

    camera = RealsenseCamera()

    camera.get_serial()



@camera_commands.command(name="calibrate")
def calibrate():
    from printq.camera.pointclound import PointCloud
    pointcloud = PointCloud()
    pointcloud.calibrate()

@camera_commands.command(name="cmp_cam")
def calibrate():
    from printq.camera.pointclound import PointCloud
    from printq.camera.realsense import RealsenseCamera
    pointcloud = PointCloud()

    cam = RealsenseCamera()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/xyz.3mf")
    print(pointcloud.compare_pointclouds(cam.get_point_cloud() ))
