"""Realsense camera as an iceoryx2 publisher node.

Architecture
============

The camera is owned by a *single* child process (the "server") that:

  * Opens the librealsense pipeline on the given device.
  * Publishes every captured frame on iceoryx2 publish-subscribe services
    (one for color, one for depth, one for a low-rate heartbeat).
  * Exits cleanly on SIGTERM / SIGINT, stopping the pipeline.

Every other consumer (the CLI ``show`` command, the print-cycle
orchestrator, etc.) is a lightweight *client* that subscribes to those
services. Clients never touch the camera directly, so multiple
consumers can read the same stream concurrently without contention,
and the camera can keep running independently of any single consumer.

Service names (the public contract):

  * ``printq/camera/color_frame``  — color frames (pub-sub)
  * ``printq/camera/depth_frame``  — depth frames (pub-sub)
  * ``printq/camera/heartbeat``    — liveness + measured fps (pub-sub)

This mirrors the :mod:`printq.arm.piper` pattern; see that module for
the analogous design decisions around lazy IPC init, ``_require_running``
heartbeat checks, ``spawn`` start method (so the child does NOT inherit
any parent file descriptors), and graceful shutdown.
"""

import base64
import ctypes
import multiprocessing as mp
import signal
import time
from logging import getLogger

import cv2
import numpy as np
import pyrealsense2 as rs
import base64
from ultralytics import YOLO

logger = getLogger(__name__)


# --- Payload size budget ------------------------------------------------------
#
# Fixed-size ctypes payloads keep ABI stable and let iceoryx2 allocate
# bounded shared memory. We size them once at module import for the
# largest resolution we expect to support; smaller frames just leave the
# tail of the buffer unused. Picking 1280x720 covers all D4xx color
# resolutions up to 720p plus depth up to 720p, and stays comfortably
# under iceoryx2's default ~4 MB per-sample chunk limit:
#
#     color (RGB8): 1280 * 720 * 3 = 2,764,800 B  ~= 2.65 MB
#     depth (Z16):  1280 * 720 * 2 = 1,843,200 B  ~= 1.76 MB
COLOR_MAX_WIDTH = 1280
COLOR_MAX_HEIGHT = 720
COLOR_CHANNELS = 3
COLOR_MAX_BYTES = COLOR_MAX_WIDTH * COLOR_MAX_HEIGHT * COLOR_CHANNELS

DEPTH_MAX_WIDTH = 1280
DEPTH_MAX_HEIGHT = 720
DEPTH_MAX_PIXELS = DEPTH_MAX_WIDTH * DEPTH_MAX_HEIGHT


class CameraNotRunningError(RuntimeError):
    """Raised when a client call needs a camera server but none is alive.

    Callers should typically present a user-friendly message like
    ``"run 'printq camera start' first"``.
    """


class CameraIntrinsics(ctypes.Structure):
    """Pinhole intrinsics + depth scale for a stream.

    Bundled with every frame so clients can deproject pixels to 3D
    points (or compute a point cloud) without having to round-trip
    through the camera or carry a separate metadata service.
    """

    _pack_ = 4
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("ppx", ctypes.c_float),
        ("ppy", ctypes.c_float),
        ("fx", ctypes.c_float),
        ("fy", ctypes.c_float),
        # Depth-only: meters per raw depth unit (e.g. 0.001 for D435 Z16).
        # Color frames leave this at 0.0.
        ("depth_scale_m_per_unit", ctypes.c_float),
        ("_padding", ctypes.c_uint32),
    ]


class ColorFramePayload(ctypes.Structure):
    """Iceoryx2 payload for a single color frame.

    Pixel layout is **BGR8** (HxWx3 uint8), packed so cv2.imshow can
    consume it directly without a colour-channel swap.
    """

    _pack_ = 8
    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("frame_number", ctypes.c_uint64),
        ("intrinsics", CameraIntrinsics),
        ("data", ctypes.c_uint8 * COLOR_MAX_BYTES),
    ]

    @staticmethod
    def type_name() -> str:
        """System-wide unique type name used by iceoryx2 for type matching."""
        return "printq::RealsenseColorFrame"


class DepthFramePayload(ctypes.Structure):
    """Iceoryx2 payload for a single depth frame.

    Pixel layout is HxW uint16, in raw camera units (multiply by
    ``intrinsics.depth_scale_m_per_unit`` to convert to meters).
    """

    _pack_ = 8
    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("frame_number", ctypes.c_uint64),
        ("intrinsics", CameraIntrinsics),
        ("data", ctypes.c_uint16 * DEPTH_MAX_PIXELS),
    ]

    @staticmethod
    def type_name() -> str:
        return "printq::RealsenseDepthFrame"


class CameraHeartbeatPayload(ctypes.Structure):
    """Liveness + measured frame rate published by the camera server."""

    _pack_ = 8
    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("frame_number", ctypes.c_uint64),
        ("color_fps", ctypes.c_float),
        ("depth_fps", ctypes.c_float),
        ("dropped_frames", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]

    @staticmethod
    def type_name() -> str:
        return "printq::RealsenseCameraHeartbeat"


def _intrinsics_from_stream(profile: rs.stream_profile, depth_scale: float = 0.0) -> CameraIntrinsics:
    """Pack a librealsense ``stream_profile`` into our ABI-stable struct."""
    vsp = profile.as_video_stream_profile()
    ix = vsp.get_intrinsics()
    out = CameraIntrinsics()
    out.width = int(ix.width)
    out.height = int(ix.height)
    out.ppx = float(ix.ppx)
    out.ppy = float(ix.ppy)
    out.fx = float(ix.fx)
    out.fy = float(ix.fy)
    out.depth_scale_m_per_unit = float(depth_scale)
    return out


def _run_camera_loop(
    color_service_name: str,
    depth_service_name: str,
    heartbeat_service_name: str,
    width: int,
    height: int,
    fps: int,
    heartbeat_period_s: float,
    serial: str | None,
    hardware_reset: bool,
) -> None:
    """Child-process entry point: own the camera, publish aligned frames.

    Both the color and depth streams are configured at the same
    ``width x height @ fps`` so that depth can be aligned to color with
    ``rs.align(rs.stream.color)`` — after alignment, the depth pixels
    are spatially registered to the color pixels and share the color
    stream's intrinsics. Subscribers can therefore index depth by the
    same ``(u, v)`` as color without any per-pixel reprojection.

    On startup the loop creates iceoryx2 publishers for color, (aligned)
    depth, and heartbeat, then publishes every captured frame plus a
    ~1 Hz heartbeat with measured frame rates. SIGTERM / SIGINT stop
    the loop cleanly.
    """
    import logging as _logging

    import iceoryx2 as iox2  # local: keep parent import cheap

    # spawn() gives the child a fresh interpreter with no handlers, so
    # without this its logs would be silently dropped.
    if not _logging.getLogger().handlers:
        _logging.basicConfig(
            level=_logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    child_logger = getLogger(f"{__name__}.camera_loop")

    stop_requested = False

    def _request_stop(signum, _frame):
        nonlocal stop_requested
        child_logger.info("Camera loop received signal %d; stopping.", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # Ask the kernel to deliver SIGTERM to us when the parent dies for
    # any reason (including SIGKILL, which has no atexit hook). Without
    # this, ungraceful parent exits leak the camera child process AND
    # hold the camera USB device hostage. Linux-only; harmless to skip
    # on other OSes.
    try:
        import ctypes as _ctypes
        _libc = _ctypes.CDLL("libc.so.6", use_errno=True)
        _PR_SET_PDEATHSIG = 1
        _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except OSError:
        child_logger.debug("prctl(PR_SET_PDEATHSIG) unavailable; skipping.")

    # ---------- (optional) hardware reset ----------
    # D435 firmware occasionally gets stuck after rapid pipeline open/close
    # cycles, in which case ``wait_for_frames`` times out repeatedly even
    # though ``query_devices`` still sees the camera. ``hardware_reset()``
    # power-cycles the USB peripheral and almost always recovers it. We
    # do it opt-in (vs. always) because reset adds a few seconds of
    # startup latency.
    if hardware_reset:
        try:
            ctx = rs.context()
            devs = ctx.query_devices()
            for d in devs:
                if not serial or d.get_info(rs.camera_info.serial_number) == serial:
                    child_logger.info(
                        "Hardware-resetting %s (serial=%s)",
                        d.get_info(rs.camera_info.name),
                        d.get_info(rs.camera_info.serial_number),
                    )
                    d.hardware_reset()
            # Reset detaches + re-enumerates the USB device; give the
            # kernel a moment to come back before we try to open it.
            time.sleep(3.0)
        except Exception:  # noqa: BLE001
            child_logger.exception("hardware_reset failed; trying to continue.")

    # ---------- librealsense pipeline ----------
    pipeline = rs.pipeline()
    config = rs.config()
    if serial:
        config.enable_device(serial)
    # bgr8 so we can hand pixels straight to cv2.imshow without a swap.
    # Both streams are enabled at the same resolution + fps so the
    # rs.align below produces a depth frame that's pixel-for-pixel
    # registered to color, no resize needed.
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)

    child_logger.info(
        "Starting Realsense pipeline (color+depth=%dx%d@%d, serial=%s)",
        width, height, fps, serial or "<default>",
    )
    profile = pipeline.start(config)

    # ``rs.align(stream.color)`` warps the raw depth image so each
    # (u, v) in depth corresponds to the same (u, v) in color. After
    # alignment, both frames share the color stream's resolution AND
    # intrinsics. Cost is a small GPU/CPU pass; well worth it because
    # it eliminates per-pixel reprojection on every subscriber.
    aligner = rs.align(rs.stream.color)

    # Pull per-stream intrinsics + depth scale once. After alignment
    # the depth uses color's intrinsics, so we publish color intrinsics
    # on BOTH services (only differing in ``depth_scale_m_per_unit``).
    try:
        depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
    except Exception:  # noqa: BLE001
        depth_scale = 0.0

    color_intrinsics = CameraIntrinsics()
    for s in profile.get_streams():
        if s.stream_type() == rs.stream.color:
            color_intrinsics = _intrinsics_from_stream(s, depth_scale=0.0)
            break

    # Aligned-depth intrinsics = color intrinsics + depth_scale so
    # subscribers can convert raw values to meters.
    aligned_depth_intrinsics = CameraIntrinsics()
    ctypes.memmove(
        ctypes.byref(aligned_depth_intrinsics),
        ctypes.byref(color_intrinsics),
        ctypes.sizeof(CameraIntrinsics),
    )
    aligned_depth_intrinsics.depth_scale_m_per_unit = float(depth_scale)

    actual_w = int(color_intrinsics.width)
    actual_h = int(color_intrinsics.height)

    # Defensive: refuse to start if the configured resolution is larger
    # than the fixed payload buffer. Loudly fail rather than silently
    # writing past the end of the struct.
    color_nbytes = actual_w * actual_h * COLOR_CHANNELS
    depth_npix = actual_w * actual_h
    if color_nbytes > COLOR_MAX_BYTES:
        raise RuntimeError(
            f"Color stream {actual_w}x{actual_h} ({color_nbytes} B) exceeds "
            f"ColorFramePayload buffer ({COLOR_MAX_BYTES} B). "
            "Bump COLOR_MAX_WIDTH/HEIGHT in printq.camera.realsense."
        )
    if depth_npix > DEPTH_MAX_PIXELS:
        raise RuntimeError(
            f"Aligned depth {actual_w}x{actual_h} ({depth_npix} px) exceeds "
            f"DepthFramePayload buffer ({DEPTH_MAX_PIXELS} px). "
            "Bump DEPTH_MAX_WIDTH/HEIGHT in printq.camera.realsense."
        )

    child_logger.info(
        "Pipeline up. Aligned color+depth %dx%d (depth scale=%.6f m/unit)",
        actual_w, actual_h, depth_scale,
    )

    # ---------- iceoryx2 setup ----------
    node = iox2.NodeBuilder.new().create(iox2.ServiceType.Ipc)

    def _pubsub(name, payload_type, *, buffer_size=None):
        # Same `history`-less rationale as the arm control loop: iceoryx2
        # services persist in shared memory across runs, and requesting a
        # non-default ``history_size`` must match the stale config or the
        # open fails. Frames are streaming-only, so 0 history is fine.
        builder = (
            node.service_builder(iox2.ServiceName.new(name))
            .publish_subscribe(payload_type)
        )
        if buffer_size is not None:
            builder = builder.subscriber_max_buffer_size(buffer_size)
        return builder.open_or_create()

    # buffer_size(1) for frames: only the latest frame matters for live
    # views and pick-and-place; we'd rather drop a stale frame than
    # backlog and let consumers fall behind.
    color_pub = _pubsub(
        color_service_name, ColorFramePayload, buffer_size=1,
    ).publisher_builder().create()
    depth_pub = _pubsub(
        depth_service_name, DepthFramePayload, buffer_size=1,
    ).publisher_builder().create()
    heartbeat_pub = _pubsub(
        heartbeat_service_name, CameraHeartbeatPayload,
    ).publisher_builder().create()

    # ---------- main capture loop ----------
    frame_no = 0
    dropped = 0
    last_hb_at = time.monotonic()
    color_frames_this_window = 0
    depth_frames_this_window = 0

    try:
        while not stop_requested:
            try:
                raw_frames = pipeline.wait_for_frames(timeout_ms=1000)
            except RuntimeError as exc:
                # Most common cause: USB stall / brief unplug. Log and
                # keep going so a transient hiccup doesn't kill the loop.
                child_logger.warning("wait_for_frames timed out: %s", exc)
                dropped += 1
                continue

            now_ns = time.time_ns()
            # Align depth into the color frame so depth[u,v] corresponds
            # to color[u,v] (and both share color's intrinsics).
            frames = aligner.process(raw_frames)
            color = frames.get_color_frame()
            depth = frames.get_depth_frame()

            if color:
                color_arr = np.asanyarray(color.get_data())  # HxWx3 uint8 (BGR)
                if color_arr.nbytes <= COLOR_MAX_BYTES:
                    sample = color_pub.loan_uninit()
                    payload = ColorFramePayload()
                    payload.timestamp_ns = now_ns
                    payload.frame_number = frame_no
                    payload.intrinsics = color_intrinsics
                    ctypes.memmove(
                        ctypes.addressof(payload.data),
                        color_arr.ctypes.data,
                        color_arr.nbytes,
                    )
                    sample = sample.write_payload(payload)
                    sample.send()
                    color_frames_this_window += 1

            if depth:
                depth_arr = np.asanyarray(depth.get_data())  # HxW uint16, color-aligned
                if depth_arr.size <= DEPTH_MAX_PIXELS:
                    sample = depth_pub.loan_uninit()
                    payload = DepthFramePayload()
                    payload.timestamp_ns = now_ns
                    payload.frame_number = frame_no
                    payload.intrinsics = aligned_depth_intrinsics
                    ctypes.memmove(
                        ctypes.addressof(payload.data),
                        depth_arr.ctypes.data,
                        depth_arr.nbytes,
                    )
                    sample = sample.write_payload(payload)
                    sample.send()
                    depth_frames_this_window += 1

            frame_no += 1

            # Heartbeat with measured fps. We sample over a sliding
            # window of ``heartbeat_period_s`` seconds (using monotonic
            # time so wall-clock drift doesn't affect the fps math).
            now_m = time.monotonic()
            window = now_m - last_hb_at
            if window >= heartbeat_period_s:
                color_fps = color_frames_this_window / window
                depth_fps = depth_frames_this_window / window
                color_frames_this_window = 0
                depth_frames_this_window = 0
                last_hb_at = now_m

                hb = CameraHeartbeatPayload()
                hb.timestamp_ns = now_ns
                hb.frame_number = frame_no
                hb.color_fps = color_fps
                hb.depth_fps = depth_fps
                hb.dropped_frames = dropped
                hb.flags = 0
                hb_sample = heartbeat_pub.loan_uninit()
                hb_sample = hb_sample.write_payload(hb)
                hb_sample.send()
                child_logger.debug(
                    "heartbeat: frame=%d color_fps=%.1f depth_fps=%.1f dropped=%d",
                    frame_no, color_fps, depth_fps, dropped,
                )
    finally:
        try:
            pipeline.stop()
        except Exception:  # noqa: BLE001
            child_logger.exception("Failed to stop Realsense pipeline cleanly.")
        child_logger.info(
            "Camera loop exited (last_frame=%d, dropped=%d).", frame_no, dropped,
        )


class RealsenseCamera:
    """Realsense camera client + server controller.

    Two usage patterns are supported:

      1. *Server* (one per host): construct a ``RealsenseCamera`` and
         call :meth:`start`. The spawned child process is now the sole
         owner of the librealsense pipeline. Call :meth:`stop` for a
         clean shutdown.

      2. *Client*: construct a ``RealsenseCamera`` and call any of the
         ``get_*`` / ``show_*`` methods. These all read from the
         iceoryx2 services and require a running server somewhere on
         the system; if none is detected within
         :attr:`DEFAULT_VERIFY_TIMEOUT_S` they raise
         :class:`CameraNotRunningError`.
    """

    # ----- public iceoryx2 contract -----
    COLOR_FRAME_SERVICE_NAME = "printq/camera/color_frame"
    DEPTH_FRAME_SERVICE_NAME = "printq/camera/depth_frame"
    HEARTBEAT_SERVICE_NAME = "printq/camera/heartbeat"

    # ----- defaults -----
    # Both color and depth are enabled at this single resolution so
    # ``rs.align(stream.color)`` can produce pixel-aligned frames with
    # zero resizing. Pick something supported by BOTH streams on your
    # camera (for D435: 640x480, 848x480, 1280x720 all work).
    DEFAULT_WIDTH = 640
    DEFAULT_HEIGHT = 480
    DEFAULT_FPS = 30
    DEFAULT_HEARTBEAT_PERIOD_S = 1.0

    # How long client calls wait for a heartbeat before raising
    # CameraNotRunningError.
    DEFAULT_VERIFY_TIMEOUT_S = 2.0
    # How long client read methods wait for a fresh frame.
    DEFAULT_READ_TIMEOUT_S = 2.0

    WINDOW_NAME = "Realsense Camera"
    model = YOLO("yolov8n.pt")
    depth_scale = None
    align = None
    def __init__(self, serial: str | None = None):

        """Initialize the Realsense camera."""
        self.pipeline = rs.pipeline()
        depth_sensor =  self.pipeline.start().get_device().first_depth_sensor()
        self.depth_scale = depth_sensor.get_depth_scale()

        align_to = rs.stream.color
        self.align = rs.align(align_to)

        """Construct a camera handle.

        IMPORTANT: This constructor does NOT open the camera or create
        an iceoryx2 node. It only stores config. The actual pipeline is
        owned by the child process spawned by :meth:`start`, and IPC
        handles are created lazily on first client call.

        Args:
            serial: Optional Realsense serial number to bind to. ``None``
                lets librealsense pick the first attached device.
        """
        self.serial = serial
        self._camera_process: mp.Process | None = None

        # Lazy iceoryx2 client-side handles.
        self._iox_node = None
        self._color_subscriber = None
        self._depth_subscriber = None
        self._heartbeat_subscriber = None

        # Tunables mirroring class defaults so callers can tweak after init.
        self.width = self.DEFAULT_WIDTH
        self.height = self.DEFAULT_HEIGHT
        self.fps = self.DEFAULT_FPS
        self.heartbeat_period_s = self.DEFAULT_HEARTBEAT_PERIOD_S
        self.hardware_reset_on_start = False

    # ================================================================ Server-side

    def start(self) -> None:
        """Start the camera publisher in a child process.

        Idempotent: a no-op (with a warning) if this instance already
        owns a running child.
        """
        if self._camera_process is not None and self._camera_process.is_alive():
            logger.warning(
                "RealsenseCamera.start() called but child is already running "
                "(pid=%d).", self._camera_process.pid,
            )
            return

        ctx = mp.get_context("spawn")
        self._camera_process = ctx.Process(
            target=_run_camera_loop,
            kwargs=dict(
                color_service_name=self.COLOR_FRAME_SERVICE_NAME,
                depth_service_name=self.DEPTH_FRAME_SERVICE_NAME,
                heartbeat_service_name=self.HEARTBEAT_SERVICE_NAME,
                width=self.width,
                height=self.height,
                fps=self.fps,
                heartbeat_period_s=self.heartbeat_period_s,
                serial=self.serial,
                hardware_reset=self.hardware_reset_on_start,
            ),
            name="RealsenseCameraLoop",
            daemon=False,
        )
        self._camera_process.start()
        logger.info(
            "Started Realsense camera loop "
            "(pid=%d, color+aligned-depth=%dx%d@%d)",
            self._camera_process.pid, self.width, self.height, self.fps,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the camera publisher (no-op if not started by this instance).

        Sends SIGTERM, waits up to ``timeout`` seconds, then SIGKILLs
        as a last resort. Parent-side iceoryx2 handles are released too
        so the next ``start()`` gets a clean slate.
        """
        proc = self._camera_process
        if proc is None:
            return

        if proc.is_alive():
            logger.info(
                "Stopping Realsense camera loop (pid=%d).", proc.pid,
            )
            proc.terminate()
            proc.join(timeout=timeout)
            if proc.is_alive():
                logger.warning(
                    "Camera loop (pid=%d) did not exit within %.1fs; killing.",
                    proc.pid, timeout,
                )
                proc.kill()
                proc.join(timeout=2.0)
        self._camera_process = None

        # Drop our subscribers so the next client call recreates them
        # against fresh services if needed.
        self._color_subscriber = None
        self._depth_subscriber = None
        self._heartbeat_subscriber = None
        self._iox_node = None

    def is_running(self) -> bool:
        """True iff *this instance* owns a live child process."""
        return self._camera_process is not None and self._camera_process.is_alive()

    # ================================================================ Client-side

    def _ensure_iox_node(self):
        import iceoryx2 as iox2

        if self._iox_node is None:
            self._iox_node = iox2.NodeBuilder.new().create(iox2.ServiceType.Ipc)
        return self._iox_node

    def _ensure_color_subscriber(self):
        import iceoryx2 as iox2

        if self._color_subscriber is None:
            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.COLOR_FRAME_SERVICE_NAME))
                .publish_subscribe(ColorFramePayload)
                .subscriber_max_buffer_size(1)
                .open_or_create()
            )
            self._color_subscriber = service.subscriber_builder().create()
        return self._color_subscriber

    def _ensure_depth_subscriber(self):
        import iceoryx2 as iox2

        if self._depth_subscriber is None:
            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.DEPTH_FRAME_SERVICE_NAME))
                .publish_subscribe(DepthFramePayload)
                .subscriber_max_buffer_size(1)
                .open_or_create()
            )
            self._depth_subscriber = service.subscriber_builder().create()
        return self._depth_subscriber

    def _ensure_heartbeat_subscriber(self):
        import iceoryx2 as iox2

        if self._heartbeat_subscriber is None:
            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.HEARTBEAT_SERVICE_NAME))
                .publish_subscribe(CameraHeartbeatPayload)
                .open_or_create()
            )
            self._heartbeat_subscriber = service.subscriber_builder().create()
        return self._heartbeat_subscriber

    def get_heartbeat(self, timeout: float | None = None) -> CameraHeartbeatPayload:
        """Block until a fresh heartbeat is observed, or raise.

        Returns the most-recent ``CameraHeartbeatPayload`` (a fresh
        copy, so callers can hold on to it without worrying about
        iceoryx2 reusing the underlying buffer).
        """
        if timeout is None:
            timeout = self.DEFAULT_VERIFY_TIMEOUT_S

        sub = self._ensure_heartbeat_subscriber()
        deadline = time.monotonic() + timeout
        latest: CameraHeartbeatPayload | None = None
        # Drain backlog so we return the most recent state.
        while True:
            sample = sub.receive()
            if sample is None:
                break
            p = sample.payload().contents
            latest = CameraHeartbeatPayload()
            ctypes.memmove(
                ctypes.byref(latest),
                ctypes.byref(p),
                ctypes.sizeof(CameraHeartbeatPayload),
            )
        if latest is not None:
            return latest

        # Nothing buffered; poll until deadline.
        while time.monotonic() < deadline:
            time.sleep(0.02)
            sample = sub.receive()
            if sample is not None:
                p = sample.payload().contents
                latest = CameraHeartbeatPayload()
                ctypes.memmove(
                    ctypes.byref(latest),
                    ctypes.byref(p),
                    ctypes.sizeof(CameraHeartbeatPayload),
                )
                return latest

        raise CameraNotRunningError(
            f"Realsense camera server is not running "
            f"(no heartbeat on '{self.HEARTBEAT_SERVICE_NAME}' within {timeout:.1f}s). "
            f"Run 'printq camera start' first."
        )

    def _require_running(self, timeout: float | None = None) -> CameraHeartbeatPayload:
        """Alias for :meth:`get_heartbeat` with intent-revealing name."""
        return self.get_heartbeat(timeout=timeout)

    def get_color_frame(
        self,
        timeout: float | None = None,
    ) -> tuple[np.ndarray, CameraIntrinsics, int]:
        """Return the latest color frame as a numpy array.

        Returns:
            ``(image_bgr, intrinsics, timestamp_ns)`` where ``image_bgr``
            is an HxWx3 ``uint8`` array in **BGR** order (ready for
            ``cv2.imshow``). The array is a fresh copy out of shared
            memory, so callers can keep it past the next ``receive()``.
        """
        if timeout is None:
            timeout = self.DEFAULT_READ_TIMEOUT_S

        sub = self._ensure_color_subscriber()
        return self._read_color(sub, timeout)

    def _read_color(self, sub, timeout: float) -> tuple[np.ndarray, CameraIntrinsics, int]:
        deadline = time.monotonic() + timeout
        latest = None
        # Always start by draining backlog so we return the freshest frame.
        while True:
            sample = sub.receive()
            if sample is None:
                break
            latest = sample
        while latest is None:
            if time.monotonic() >= deadline:
                # No frame yet -> figure out whether the server is alive
                # at all so the caller gets a useful error.
                self._require_running(timeout=0.5)
                raise CameraNotRunningError(
                    "Camera server is running but no color frame arrived "
                    f"within {timeout:.1f}s on "
                    f"'{self.COLOR_FRAME_SERVICE_NAME}'."
                )
            time.sleep(0.005)
            sample = sub.receive()
            if sample is not None:
                latest = sample

        p = latest.payload().contents
        w, h = int(p.intrinsics.width), int(p.intrinsics.height)
        nbytes = w * h * COLOR_CHANNELS
        # Copy out of shared memory so the array survives ``sample`` going
        # out of scope and iceoryx2 reclaiming the buffer.
        raw = bytes(p.data[:nbytes])
        img = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, COLOR_CHANNELS)
        # The frombuffer view is read-only; copy so callers can edit.
        return img.copy(), p.intrinsics, int(p.timestamp_ns)

    def get_depth_frame(
        self,
        timeout: float | None = None,
    ) -> tuple[np.ndarray, CameraIntrinsics, int]:
        """Return the latest depth frame as a numpy array.

        Returns:
            ``(depth_u16, intrinsics, timestamp_ns)`` where ``depth_u16``
            is an HxW ``uint16`` array in raw camera units. Multiply by
            ``intrinsics.depth_scale_m_per_unit`` to get meters.
        """
        if timeout is None:
            timeout = self.DEFAULT_READ_TIMEOUT_S

        sub = self._ensure_depth_subscriber()
        return self._read_depth(sub, timeout)

    def _read_depth(self, sub, timeout: float) -> tuple[np.ndarray, CameraIntrinsics, int]:
        deadline = time.monotonic() + timeout
        latest = None
        while True:
            sample = sub.receive()
            if sample is None:
                break
            latest = sample
        while latest is None:
            if time.monotonic() >= deadline:
                self._require_running(timeout=0.5)
                raise CameraNotRunningError(
                    "Camera server is running but no depth frame arrived "
                    f"within {timeout:.1f}s on "
                    f"'{self.DEPTH_FRAME_SERVICE_NAME}'."
                )
            time.sleep(0.005)
            sample = sub.receive()
            if sample is not None:
                latest = sample

        p = latest.payload().contents
        w, h = int(p.intrinsics.width), int(p.intrinsics.height)
        npix = w * h
        # bytes() copies; np.frombuffer needs a writable buffer for .copy()
        # to be a true copy. We use .copy() at the end to detach from the
        # original memory.
        raw = bytes(bytearray(ctypes.cast(
            ctypes.addressof(p.data),
            ctypes.POINTER(ctypes.c_uint8 * (npix * 2)),
        )[0]))
        depth = np.frombuffer(raw, dtype=np.uint16).reshape(h, w)
        return depth.copy(), p.intrinsics, int(p.timestamp_ns)

    def get_frames(
        self,
        timeout: float | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return the latest ``(color_bgr, depth_u16)`` pair.

        Frames are read from independent iceoryx2 services, so they're
        the *most recent each*, not strictly time-synchronised. Use the
        per-frame timestamps if you need to check alignment.
        """
        color, _, _ = self.get_color_frame(timeout=timeout)
        depth, _, _ = self.get_depth_frame(timeout=timeout)
        return color, depth

    def get_point_cloud(
        self,
        timeout: float | None = None,
        voxel_size: float | None = 0.005,
        max_depth_m: float = 3.0,
        with_color: bool = True,
    ):
        """Build an Open3D point cloud from the latest depth (and optionally color) frame.

        Uses the iceoryx2 client path (``get_depth_frame`` /
        ``get_color_frame``), so the camera publisher must already be
        running (``printq camera start``). The depth stream is published
        aligned to color, so when ``with_color=True`` we get a coloured
        cloud "for free" without re-aligning on the client side.

        Args:
            timeout: per-frame read timeout, forwarded to the underlying
                ``get_*_frame`` calls.
            voxel_size: optional voxel size (meters) to downsample with.
                Pass ``None`` or ``0`` to skip downsampling. Default
                ``0.005`` (5 mm) matches the calibration helper.
            max_depth_m: drop / truncate depth samples beyond this range
                (meters). Filters out the long-tail noise the D4xx
                sensors produce past their useful range.
            with_color: if True (default), also subscribe to the color
                stream and return a coloured point cloud.

        Returns:
            ``open3d.geometry.PointCloud`` ready for downstream
            registration / comparison (e.g.
            ``PointCloud.compare_pointclouds``).
        """
        import open3d as o3d  # local: keep parent import cheap

        depth_u16, depth_intr, _ = self.get_depth_frame(timeout=timeout)

        depth_m = depth_u16.astype(np.float32) * float(depth_intr.depth_scale_m_per_unit)
        depth_m[depth_m > max_depth_m] = 0.0

        depth_image = o3d.geometry.Image(depth_m)
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            width=int(depth_intr.width),
            height=int(depth_intr.height),
            fx=float(depth_intr.fx),
            fy=float(depth_intr.fy),
            cx=float(depth_intr.ppx),
            cy=float(depth_intr.ppy),
        )

        if with_color:
            color_bgr, _, _ = self.get_color_frame(timeout=timeout)
            color_rgb = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2RGB)
            # Depth is published aligned to color, so resolutions
            # normally match. Resize defensively in case the publisher
            # was started with mismatched stream configs.
            if color_rgb.shape[:2] != depth_m.shape:
                color_rgb = cv2.resize(
                    color_rgb,
                    (depth_m.shape[1], depth_m.shape[0]),
                )
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                o3d.geometry.Image(np.ascontiguousarray(color_rgb)),
                depth_image,
                # depth is already in meters above, so depth_scale=1.
                depth_scale=1.0,
                depth_trunc=max_depth_m,
                convert_rgb_to_intensity=False,
            )
            pcd = o3d.geometry.PointCloud.create_from_rgbd_image(rgbd, intrinsic)
        else:
            pcd = o3d.geometry.PointCloud.create_from_depth_image(
                depth_image,
                intrinsic,
                depth_scale=1.0,
                depth_trunc=max_depth_m,
            )

        if voxel_size:
            pcd = pcd.voxel_down_sample(voxel_size)

        return pcd

    # ================================================================ Display helpers

    @staticmethod
    def _depth_to_colormap(depth_u16: np.ndarray) -> np.ndarray:
        """Compress 16-bit depth to 8-bit and apply a JET colormap."""
        return cv2.applyColorMap(
            cv2.convertScaleAbs(depth_u16, alpha=0.03),
            cv2.COLORMAP_JET,
        )

    def show_frame(self) -> None:
        """Render the latest color + colorized aligned depth side-by-side.

        Since depth is published color-aligned (same resolution and
        viewpoint), the two panels can be stacked directly without any
        resize. Caller is responsible for pumping the OpenCV event loop
        with ``cv2.waitKey``. Use :meth:`show_frames_loop` for a
        turnkey loop.
        """
        color, depth = self.get_frames()
        depth_color = self._depth_to_colormap(depth)
        combined = np.hstack((color, depth_color))
        cv2.imshow(self.WINDOW_NAME, combined)

    def show_frames_loop(self) -> None:
        """Block, showing live frames until the user presses ``q`` or closes."""
        try:
            while True:
                try:
                    self.show_frame()
                except CameraNotRunningError as exc:
                    logger.error("%s", exc)
                    break
                # waitKey(1) both pumps the OpenCV event loop (so the
                # window updates) AND polls for keypresses; returns -1 if
                # none pressed.
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:  # 'q' or Esc
                    break
        finally:
            cv2.destroyAllWindows()

    # ================================================================ Utilities

    @staticmethod
    def get_serial() -> str | None:
        """Print + return the first attached Realsense serial number.

        Standalone: this does NOT require the iceoryx2 server to be
        running (it just enumerates USB devices), so it can be used as
        a pre-flight check before ``start()``.
        """
        context = rs.context()
        devices = context.query_devices()
        if len(devices) == 0:
            raise RuntimeError(
                "System sees zero RealSense devices. "
                "Unplug and replug the camera."
            )

        first = devices[0]
        serial = first.get_info(rs.camera_info.serial_number)
        name = first.get_info(rs.camera_info.name)
        logger.info("Found RealSense camera: %s (serial: %s)", name, serial)
        return serial

    def close(self) -> None:
        """Backwards-compat alias for :meth:`stop`."""
        self.stop()


    def take_pic(self):
        color_frame = self.get_rgb_frame()
        color_image = np.asanyarray(color_frame.get_data())

        # 2. Encode the image into a memory buffer (e.g., as a JPG)
        # '.jpg' or '.png' both work here
        success, buffer = cv2.imencode('.jpg', color_image)

        if success:
            # 3. Convert the buffer to Base64 bytes
            jpg_as_text = base64.b64encode(buffer)
            
            # 4. Optional: Convert bytes to a UTF-8 string for JSON/HTML
            base64_string = jpg_as_text.decode('utf-8')
            
            print(f"Base64 string starts with: {base64_string[:50]}...")
            return base64_string
        
    def get_obj(self):
        frames = self.pipeline.wait_for_frames()

        # Align the depth frame to color frame
        aligned_frames = self.align.process(frames)
        
        # Get aligned frames
        depth_frame = aligned_frames.get_depth_frame()
        color_frame = aligned_frames.get_color_frame()

        if not depth_frame or not color_frame:
            print("Could not acquire depth or color frames.")
            return

        # Convert images to numpy arrays
        depth_image = np.asanyarray(depth_frame.get_data())
        color_image = np.asanyarray(color_frame.get_data())

        # 3. Run Object Detection
        # We run YOLO on the color image
        results = self.model(color_image, stream=True, verbose=False)

        for result in results:
            boxes = result.boxes
            for box in boxes:
                # Get bounding box coordinates [x1, y1, x2, y2]
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                # print(f"Detected object with bounding box: ({x1}, {y1}), ({x2}, {y2})")
                
                # Get class name (e.g., 'cup', 'cell phone', 'person')
                cls_id = int(box.cls[0])
                class_name = self.model.names[cls_id]

                # 4. Calculate Distance
                # Extract the depth data strictly inside the bounding box
                depth_crop = depth_image[y1:y2, x1:x2].astype(float)
                
                # Filter out zero values (errors/dead pixels in the depth map)
                depth_crop = depth_crop[depth_crop > 0]

                if len(depth_crop) > 0:
                    # Use median instead of mean to ignore background noise at the edges
                    median_depth = np.median(depth_crop)
                    distance_meters = median_depth * self.depth_scale
                    distance_str = f"{distance_meters:.2f}m"
                else:
                    distance_str = "Unknown"

                # 5. Draw Overlays
                # Draw Bounding Box (Green)
                cv2.rectangle(color_image, (x1, y1), (x2, y2), (0, 255, 0), 2)
                
                # Draw Label & Distance Background (so text is readable)
                label = f"{class_name} | {distance_str}"
                (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(color_image, (x1, y1 - 25), (x1 + w, y1), (0, 255, 0), -1)
                
                # Draw Text (Black text on Green background)
                cv2.putText(color_image, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

        # Show the final image with overlays
        cv2.imshow('RealSense Object & Distance Tracker', color_image)
