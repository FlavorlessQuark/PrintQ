import numpy as np
import cv2
import trimesh
import pyrealsense2 as rs
import open3d as o3d


from threemf import load_3mf_as_pointcloud
from singleview import single_view_comparison
# from rotview import rotation_view_comparison

def stream_with_overlay(filepath_3mf):
    # 1. Setup RealSense
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    profile = pipeline.start(config)
    
    align = rs.align(rs.stream.color)
    intrinsics = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    
    print("Camera Intrinsics:", intrinsics)
    # Camera Matrix for OpenCV projection
    camera_matrix = np.array([[intrinsics.fx, 0, intrinsics.ppx], 
                              [0, intrinsics.fy, intrinsics.ppy], 
                              [0, 0, 1]], dtype=float)
    dist_coeffs = np.zeros(4)

    # # Load CAD
    # cad_mesh, cad_pcd, cad_features = load_3mf_as_pointcloud(filepath_3mf)
    
    # # For drawing, extract the 3D vertices of the CAD model
    # cad_vertices = np.asarray(cad_mesh.vertices)
    # print("CAD model loaded with", len(cad_vertices), "vertices.")
    try:
        while True:
            frames = pipeline.wait_for_frames()
            aligned_frames = align.process(frames)
            color_frame = aligned_frames.get_color_frame()
            depth_frame = aligned_frames.get_depth_frame()
            
            if not color_frame or not depth_frame: continue
            
            color_image = np.asanyarray(color_frame.get_data())
            depth_image = np.asanyarray(depth_frame.get_data())
            
            # Create Open3D Point Cloud from RealSense frame
            o3d_color = o3d.geometry.Image(cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB))
            o3d_depth = o3d.geometry.Image(depth_image)
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(o3d_color, o3d_depth, depth_scale=1000.0, depth_trunc=1.5)
            
            pinhole = o3d.camera.PinholeCameraIntrinsic(intrinsics.width, intrinsics.height, intrinsics.fx, intrinsics.fy, intrinsics.ppx, intrinsics.ppy)
            live_pcd = o3d.geometry.PointCloud.create_from_rgbd_image(rgbd, pinhole)

            # --- RUN USE CASE 1 ---
            # Try to find the object and get likeness (Wrapping in a fast try/except for lost tracking)
            # try:
            #     pose_matrix, likeness = single_view_comparison(live_pcd, cad_pcd, cad_features)
                
            #     # --- SUPERPOSITION (AUGMENTED REALITY) ---
            #     # Extract rotation (rvec) and translation (tvec) from pose_matrix
            #     rmat = pose_matrix[:3, :3]
            #     tvec = pose_matrix[:3, 3]
            #     rvec, _ = cv2.Rodrigues(rmat)
                
            #     # Project 3D CAD vertices onto 2D image
            #     image_points, _ = cv2.projectPoints(cad_vertices, rvec, tvec, camera_matrix, dist_coeffs)
                
            #     # Draw the projected points as a green mesh overlay
            #     for p in image_points:
            #         x, y = int(p[0][0]), int(p[0][1])
            #         if 0 <= x < intrinsics.width and 0 <= y < intrinsics.height:
            #             cv2.circle(color_image, (x, y), 1, (0, 255, 0), -1)
                        
            #     cv2.putText(color_image, f"Match: {likeness:.1f}%", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
            # except Exception as e:
            #     cv2.putText(color_image, "Searching for object...", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            # Show video
            cv2.imshow('RealSense CAD Comparison', color_image)
            if cv2.waitKey(1) == 27: # Esc to quit
                break
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    filepath_3mf = "data/xyz.3mf" # Change this to your .3mf file path
    stream_with_overlay(filepath_3mf)

