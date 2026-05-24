"""Camera control commands."""

from logging import getLogger

import click

logger = getLogger(__name__)

@click.group(name="camera")
@click.pass_context
def camera_commands(ctx):
    """Camera control utilities"""
    ctx.ensure_object(dict)

@camera_commands.command(name="start")
def start():
    """Start the camera and stream the depth feed until 'q' is pressed."""
    import cv2

    from printq.camera.realsense import RealsenseCamera

    camera = RealsenseCamera()
    try:
        while True:
            camera.show_frame()
            # waitKey(1) both pumps the OpenCV GUI event loop (so the window
            # updates) and polls for a quit key. Returns -1 if no key.
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
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

@camera_commands.command(name="cmp_self")
def calibrate():
    from printq.camera.pointclound import PointCloud
    pointcloud = PointCloud()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/xyz.3mf")
    print(pointcloud.compare_pointclouds(pointcloud.current_pcd["pcd"]))
    