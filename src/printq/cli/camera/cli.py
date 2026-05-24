"""Camera control commands.

The Realsense camera follows the same client/server split as the arm:

  * ``printq camera start`` runs the long-lived publisher process. It
    owns the librealsense pipeline and pushes color/depth frames over
    iceoryx2 services. Ctrl-C cleanly stops it.

  * Every other command (``show``, ``stop``, ``serial``) is a thin
    client that talks to those services and reports a clear error if no
    server is running.
"""

import contextlib
import threading
import time
from logging import getLogger

import click
import redis
from rich.console import Console

from printq.camera.qwen import Qwen
from printq.camera.realsense import CameraNotRunningError, RealsenseCamera

logger = getLogger(__name__)
r = redis.Redis(host='localhost', port=6379, decode_responses=True) 


logger = getLogger(__name__)
console = Console()


@click.group(name="camera")
@click.pass_context
def camera_commands(ctx):
    """Camera control utilities."""
    ctx.ensure_object(dict)

def start_loop():
    pubsub = r.pubsub()
    pubsub.subscribe('start')
    print("Subscribed to Redis channel 'time_control'. Listening for messages...")
    for message in pubsub.listen():
        print(f"Received message: {message}")

@camera_commands.command(name="start")
def start():
    """Run the print-quality monitoring loop.

    This is a CLIENT of the camera publisher: it subscribes to color +
    depth over iceoryx2 and runs YOLO + Qwen on captured frames. The
    publisher must already be running -- start it in another terminal
    with ``printq camera start-server``.

    Keybindings (focus the OpenCV window):

        q   quit
        s   toggle the on-screen preview
        d   capture, send to Qwen for QC verdict, publish to Redis
    """
    import json

    import cv2

    camera = _new_client()
    # Fail fast if no publisher is up; otherwise the first frame read
    # would block on the iceoryx2 timeout and the user would see a less
    # helpful error.
    with _handle_camera_not_running():
        camera.get_heartbeat()

    qwen = Qwen()
    show = True
    threading.Thread(target=start_loop, daemon=True).start()
    try:
        while True:
            if show:
                camera.show_frame()
                camera.get_obj()
            # waitKey(1) pumps the OpenCV GUI event loop AND polls for a
            # keypress. Returns -1 (==255 after & 0xFF) when no key is
            # pressed, so we filter that out before matching.
            key = cv2.waitKey(1) & 0xFF
            if key == 0xFF:
                continue
            match key:
                case 113:  # 'q'
                    break
                case 115:  # 's'
                    show = not show
                case 100:  # 'd'
                    show = not show
                    pic = camera.take_pic()
                    status, message = qwen.get_print_status(pic)
                    r.publish("status", json.dumps({
                        "success": status,
                        "desc": message,
                        "image": pic,
                    }))
    finally:
        cv2.destroyAllWindows()


def _new_client() -> RealsenseCamera:
    """Build a client-only RealsenseCamera handle.

    Mirrors ``printq.cli.arm.cli._new_client``: instantiation is cheap
    because the constructor does not touch the camera; the handle is
    purely an iceoryx2 client until ``start()`` is called (which the
    client commands never do).
    """
    return RealsenseCamera()


@contextlib.contextmanager
def _handle_camera_not_running():
    """Print a friendly message and exit non-zero on ``CameraNotRunningError``."""
    try:
        yield
    except CameraNotRunningError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise click.exceptions.Exit(code=1) from exc


# ---------------------------------------------------------------- server

@camera_commands.command(name="start-server")
@click.option(
    "--serial",
    type=str,
    default=None,
    help="Bind to a specific Realsense serial. Default: first device found.",
)
@click.option(
    "--width",
    type=int,
    default=RealsenseCamera.DEFAULT_WIDTH,
    show_default=True,
    help=(
        "Resolution width applied to BOTH color and (aligned) depth. "
        "Pick a value supported by both streams (D435: 640, 848, 1280)."
    ),
)
@click.option(
    "--height",
    type=int,
    default=RealsenseCamera.DEFAULT_HEIGHT,
    show_default=True,
    help="Resolution height applied to both streams (D435: 480 or 720).",
)
@click.option(
    "--fps",
    type=int,
    default=RealsenseCamera.DEFAULT_FPS,
    show_default=True,
    help="Frame rate applied to both streams.",
)
@click.option(
    "--reset/--no-reset",
    default=False,
    show_default=True,
    help=(
        "Issue a USB hardware_reset() before opening the pipeline. "
        "Use this if a previous run left the camera in a stuck state "
        "(e.g. wait_for_frames keeps timing out). Adds ~3s to startup."
    ),
)
def start_server(
    serial: str | None,
    width: int,
    height: int,
    fps: int,
    reset: bool,
):
    """Start the Realsense camera publisher and stream until Ctrl-C.

    The publisher runs in a separate process. Depth is aligned to color
    (via ``rs.align``) and published at the same ``width x height`` as
    color so subscribers can sample ``depth[v, u]`` against the same
    pixel coordinate as ``color[v, u]``.
    """
    camera = RealsenseCamera(serial=serial)
    camera.width = width
    camera.height = height
    camera.fps = fps
    camera.hardware_reset_on_start = reset

    camera.start()

    # Confirm the publisher actually came up before we start streaming
    # status. If the child crashes during startup (no camera, bad config)
    # we want to surface that immediately, not silently spin.
    try:
        camera.get_heartbeat(timeout=5.0)
    except CameraNotRunningError as exc:
        if camera.is_running():
            console.print(f"[bold red]{exc}[/bold red]")
        else:
            console.print(
                "[bold red]Camera publisher child exited during startup. "
                "Check the traceback above (common causes: camera unplugged, "
                "requested resolution unsupported, or stale iceoryx2 services "
                "from a previous run -- try wiping /tmp/iceoryx2 if you keep "
                "hitting this).[/bold red]"
            )
        camera.stop()
        raise click.exceptions.Exit(code=1) from exc

    console.print(
        f"[green]Realsense camera publisher started[/green] "
        f"(pid={camera._camera_process.pid}). Press Ctrl-C to stop."
    )

    try:
        while True:
            try:
                hb = camera.get_heartbeat(timeout=3.0)
            except CameraNotRunningError:
                if not camera.is_running():
                    console.print(
                        "[bold red]Camera publisher exited unexpectedly.[/bold red]"
                    )
                    break
                console.print("[yellow]Heartbeat stalled; still alive.[/yellow]")
                continue
            console.print(
                f"frame={hb.frame_number:>6}  "
                f"color={hb.color_fps:5.1f}fps  "
                f"depth={hb.depth_fps:5.1f}fps  "
                f"dropped={hb.dropped_frames}"
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping camera publisher...[/yellow]")
    finally:
        camera.stop()
        console.print("[green]Camera publisher stopped.[/green]")


# ---------------------------------------------------------------- clients

@camera_commands.command(name="show")
def show():
    """Subscribe to the camera services and display frames until 'q'.

    Requires ``printq camera start`` to be running somewhere on the host.
    Press ``q`` (or Esc) in the OpenCV window to quit. Quitting does
    NOT stop the publisher.
    """
    camera = _new_client()
    with _handle_camera_not_running():
        camera.get_heartbeat()
    camera.show_frames_loop()


@camera_commands.command(name="show-obj")
@click.option(
    "--fps",
    type=float,
    default=1.0,
    show_default=True,
    help=(
        "Detection + display rate in Hz. YOLO inference is heavy, so "
        "the default of 1 Hz keeps CPU/GPU load bounded; raise it for "
        "smoother tracking if your machine can keep up."
    ),
)
def show_obj(fps: float):
    """Subscribe to the camera, run YOLO at ``--fps``, and display detections.

    Like ``show``, but each displayed frame is annotated with the YOLO
    bounding boxes (and median in-bbox distance from the aligned depth
    stream). Inference is throttled to ``--fps`` Hz to keep load
    bounded.

    Requires ``printq camera start-server`` to be running somewhere on
    the host. Press ``q`` (or Esc) in the OpenCV window to quit.
    Quitting does NOT stop the publisher.
    """
    if fps <= 0:
        console.print("[bold red]--fps must be > 0[/bold red]")
        raise click.exceptions.Exit(code=1)

    camera = _new_client()
    with _handle_camera_not_running():
        camera.get_heartbeat()
    camera.show_obj_loop(rate_hz=fps)


@camera_commands.command(name="status")
def status():
    """Print one heartbeat sample and exit (handy for scripts)."""
    camera = _new_client()
    with _handle_camera_not_running():
        hb = camera.get_heartbeat()
    console.print(
        f"frame_number={hb.frame_number}  "
        f"color_fps={hb.color_fps:.1f}  depth_fps={hb.depth_fps:.1f}  "
        f"dropped={hb.dropped_frames}"
    )


@camera_commands.command(name="serial")
def serial():
    """Print the serial number of the first attached Realsense device.

    Standalone: does NOT require the camera publisher to be running.
    """
    try:
        RealsenseCamera.get_serial()
    except RuntimeError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise click.exceptions.Exit(code=1) from exc


@camera_commands.command(name="calibrate")
def calibrate():
    """Run the point-cloud-based calibration helper."""
    from printq.camera.pointclound import PointCloud
    pointcloud = PointCloud()
    pointcloud.calibrate()

@camera_commands.command(name="cmp_cam")
def cmp_cam():
    from printq.camera.pointclound import PointCloud
    from printq.camera.realsense import RealsenseCamera
    pointcloud = PointCloud()

    cam = RealsenseCamera()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/xyz.3mf")
    print(pointcloud.compare_pointclouds(cam.get_point_cloud()))

@camera_commands.command(name="cmp_good")
def cmp_good():
    from printq.camera.pointclound import PointCloud
    from printq.camera.realsense import RealsenseCamera
    pointcloud = PointCloud()

    cam = RealsenseCamera()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/stepgood.3mf")
    print(pointcloud.compare_pointclouds(cam.get_point_cloud()))

@camera_commands.command(name="cmp_bad")
def cmp_bad():
    from printq.camera.pointclound import PointCloud
    from printq.camera.realsense import RealsenseCamera
    pointcloud = PointCloud()

    cam = RealsenseCamera()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/stepbad.3mf")
    print(pointcloud.compare_pointclouds(cam.get_point_cloud()))



@camera_commands.command(name="cmp_self")
def cmp_self():
    from printq.camera.pointclound import PointCloud
    pointcloud = PointCloud()
    pointcloud.load_3mf_as_pointcloud("./src/printq/assets/xyz.3mf")
    print(pointcloud.compare_pointclouds(pointcloud.current_pcd["pcd"]))

@camera_commands.command(name="ask")
def ask_qwen():
    import os

    from openai import OpenAI

    from printq.camera.realsense import RealsenseCamera
    KEY = "sk-5a54c58071af4e7781b714772fc7a233"
    
#     camera = RealsenseCamera()
#     img_data = camera.take_pic()

    client = OpenAI(
        api_key=KEY,
        base_url=("https://dashscope-intl.alyunc.com/compatible-mode/v1")
    )
    completion = client.chat.completions.create(
        model="qwen3.6-plus",
        messages=[
            {"role": "user", 
             "content": [
                 {
                    "type": "text", 
                    "text": "This is a 3d printed object. Describe the quality of the print in a short sentence blsusb"
                }, {
                    "type": "image_url", 
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_data}"
                    }
                }
             ]}])
    print(completion.choices[0].message.content)
    
