"""Realsense camera wrapper."""

import cv2
import numpy as np
import pyrealsense2 as rs
import base64
from ultralytics import YOLO

class RealsenseCamera:
    """Realsense camera wrapper."""

    WINDOW_NAME = "Realsense Camera"
    model = YOLO("yolov8n.pt")
    depth_scale = None
    align = None
    def __init__(self):
        """Initialize the Realsense camera."""
        self.pipeline = rs.pipeline()
        depth_sensor =  self.pipeline.start().get_device().first_depth_sensor()
        self.depth_scale = depth_sensor.get_depth_scale()

        align_to = rs.stream.color
        self.align = rs.align(align_to)

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

    def get_serial(self):
        context = rs.context()
        devices = context.query_devices()

        if len(devices) == 0:
            raise RuntimeError("System sees zero RealSense devices. Unplug and replug the camera.")

        # 2. Grab the specific serial number of the first camera found
        specific_camera = devices[0]
        serial_number = specific_camera.get_info(rs.camera_info.serial_number)
        camera_name = specific_camera.get_info(rs.camera_info.name)
        print(f"Found RealSense camera: {camera_name} (Serial: {serial_number})")

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
