"""Realsense camera wrapper."""

import cv2
import numpy as np
import pyrealsense2 as rs


class RealsenseCamera:
    """Realsense camera wrapper."""

    WINDOW_NAME = "Realsense Camera"

    def __init__(self):
        """Initialize the Realsense camera."""
        self.pipeline = rs.pipeline()
        self.pipeline.start()

    def get_frames(self):
        """Get the latest synchronized (color, depth) frames from one capture.

        Both frames come from the same ``wait_for_frames`` call so they
        are time-aligned. Use this in preference to ``get_rgb_frame`` /
        ``get_depth_frame`` when you need both streams in sync.

        Returns:
            ``(color_frame, depth_frame)`` as ``pyrealsense2`` frame
            objects, or ``(None, None)`` if either stream was unavailable.
        """
        frames = self.pipeline.wait_for_frames()
        color_frame = frames.get_color_frame()
        depth_frame = frames.get_depth_frame()
        if not color_frame or not depth_frame:
            return None, None
        return color_frame, depth_frame

    def get_rgb_frame(self):
        """Get the latest RGB frame as a raw pyrealsense2 frame.

        Returns:
            A ``pyrealsense2.video_frame`` or ``None`` if no frame was
            available.
        """
        frames = self.pipeline.wait_for_frames()
        rgb_frame = frames.get_color_frame()
        if not rgb_frame:
            return None
        return rgb_frame

    def get_depth_frame(self):
        """Get the latest depth frame as a raw pyrealsense2 frame.

        Returns:
            A ``pyrealsense2.depth_frame`` or ``None`` if no frame was
            available.
        """
        frames = self.pipeline.wait_for_frames()
        depth_frame = frames.get_depth_frame()
        if not depth_frame:
            return None
        return depth_frame

    def close(self):
        """Close the camera and any open OpenCV windows."""
        try:
            self.pipeline.stop()
        finally:
            # destroyAllWindows is idempotent: safe even if no window
            # was ever created (eg. if the first imshow crashed).
            cv2.destroyAllWindows()

    def show_frame(self):
        """Display the latest color frame and depth colormap side-by-side."""
        color_frame, depth_frame = self.get_frames()
        if color_frame is None or depth_frame is None:
            return

        # Realsense frames -> numpy arrays. color is HxWx3 uint8 in RGB byte
        # order; depth is HxW uint16 (millimeters).
        color_image = np.asanyarray(color_frame.get_data())
        depth_image = np.asanyarray(depth_frame.get_data())

        # Realsense delivers color in RGB byte order, but cv2.imshow expects
        # BGR. Without this swap the rendered window shows red and blue
        # channels inverted.
        color_image = cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR)

        # Compress 16-bit depth to 8-bit and colorize for display.
        depth_colormap = cv2.applyColorMap(
            cv2.convertScaleAbs(depth_image, alpha=0.03),
            cv2.COLORMAP_JET,
        )

        # np.hstack requires matching heights; both streams are configured
        # to the same resolution by default on D4xx-series cameras.
        combined = np.hstack((color_image, depth_colormap))
        cv2.imshow(self.WINDOW_NAME, combined)