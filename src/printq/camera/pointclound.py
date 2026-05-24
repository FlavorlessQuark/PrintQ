import copy
import time
import pyrealsense2 as rs
import open3d as o3d
import numpy as np
import trimesh

from .realsense import RealsenseCamera

CAM1SERIAL = "139522074081"
CAM2SERIAL = "146322072402"

# TRANSFORM_CAM1_TO_GLOBAL = np.array([
#     [24.0, 0.0, 0.0,  0.0],
#     [24.0, 1.0, 0.0,  0.0],
#     [47.0, 0.0, 1.0,  0.0],
#     [0.0, 0.0, 0.0,  1.0]
# ])

# TRANSFORM_CAM2_TO_GLOBAL = np.array([
#     [-24.0,  0.0,  0.0,  0.0],
#     [-24.0, -1.0,  0.0,  0.0],
#     [ 47.0,  0.0,  1.0,  0.0],
#     [ 0.0,  0.0,  0.0,  1.0]
# ])

TRANSFORM_CAM1_TO_GLOBAL = np.array([
    [ 0.70710678, -0.40824829,  0.57735026, -0.24],
    [ 0.00000000, -0.81649658, -0.57735026,  0.24],
    [ 0.70710678,  0.40824829, -0.57735026,  0.24],
    [ 0.00000000,  0.00000000,  0.00000000,  1.00]
])

# Camera 2 Position: Top-Right-Back of the cube
# Located at X=+0.24m, Y=+0.24m, Z=-0.24m
# Pitched down ~35.26 degrees, Yaw rotated 180 deg opposite of Cam 1
TRANSFORM_CAM2_TO_GLOBAL = np.array([
    [-0.70710678,  0.40824829, -0.57735026,  0.24],
    [ 0.00000000, -0.81649658, -0.57735026,  0.24],
    [-0.70710678, -0.40824829,  0.57735026, -0.24],
    [ 0.00000000,  0.00000000,  0.00000000,  1.00]
])

class PointCloud:
    current_pcd =  {
        "mesh": None,
        "pcd": None,
        "features": None
    }
    
    def __init__(self):
        pass
    
    def load_3mf_as_pointcloud(self, filepath, num_points=20000):
    # Trimesh handles .3mf flawlessly
        mesh = trimesh.load(filepath, force='mesh')
        
        # Convert to Open3D mesh
        vertices = o3d.utility.Vector3dVector(mesh.vertices)
        triangles = o3d.utility.Vector3iVector(mesh.faces)
        o3d_mesh = o3d.geometry.TriangleMesh(vertices, triangles)
        o3d_mesh.compute_vertex_normals()
        
        # Sample points from the mesh to create a reference Point Cloud
        cad_pcd = o3d_mesh.sample_points_uniformly(number_of_points=num_points)
        cad_pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
        
        # Extract FPFH features (3D fingerprints used to figure out "which part we are facing")
        radius_feature = 0.05 # Adjust based on physical scale (e.g., 5cm)
        cad_features = o3d.pipelines.registration.compute_fpfh_feature(
        cad_pcd, o3d.geometry.KDTreeSearchParamHybrid(radius=radius_feature, max_nn=100))
    
        self.current_pcd = {
            "mesh": o3d_mesh,
            "pcd": cad_pcd,
            "features": cad_features
        }

    def calc_likeness(self, cam1:RealsenseCamera, cam2:RealsenseCamera):
        pcd1 = cam1.get_point_cloud()
        pcd2 = cam2.get_point_cloud()

        pcd1.transform(TRANSFORM_CAM1_TO_GLOBAL)
        pcd2.transform(TRANSFORM_CAM2_TO_GLOBAL)

        # 2. Fuse the two point clouds
        fused_pcd = pcd1 + pcd2

        # 3. Clean up the fused cloud (downsample and remove outliers)
        fused_pcd = fused_pcd.voxel_down_sample(voxel_size=0.01)
        fused_pcd, _ = fused_pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
        fused_pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))


        threshold = 0.05 
        init_guess = np.identity(4)

        reg_p2p = o3d.pipelines.registration.registration_icp(
            fused_pcd, self.current_pcd, threshold, init_guess,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=2000)
        )

        # Inlier RMSE: Root Mean Squared Error of aligned points (lower is better)
        print("\n--- LIKENESS ESTIMATION RESULTS ---")
        print(f"Alignment Fitness Score: {reg_p2p.fitness:.4f} (Closer to 1.0 is a better match)")
        print(f"Surface Deviation (RMSE): {reg_p2p.inlier_rmse:.6f} meters")

    def visualize(self, cam1, cam2):
        pcd1 = cam1.get_point_cloud()
        pcd2 = cam2.get_point_cloud()
        vis_pcd1 = pcd1.clone() if hasattr(pcd1, 'clone') else copy.deepcopy(pcd1)
        vis_pcd2 = pcd2.clone() if hasattr(pcd2, 'clone') else copy.deepcopy(pcd2)

        vis_pcd1.paint_uniform_color([0, 0.651, 0.929]) 
        vis_pcd2.paint_uniform_color([1, 0.706, 0])      

        origin_frame = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1, origin=[0, 0, 0])

        o3d.visualization.draw_geometries(
            [vis_pcd1, vis_pcd2, origin_frame], 
            window_name="Camera Alignment Debugger (Close window to continue)",
            width=1024, height=768
        )
    

    def capture_pointcloud(self, serial, color):
        """Initializes a RealSense camera, captures a frame, and returns an Open3D point cloud."""
        print(f"Connecting to Camera {serial}...")
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_device(serial)
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        
        pipeline.start(config)
        
        # Let auto-exposure settle
        time.sleep(2)
        
        # Capture multiple frames and take the last one for a stable image
        for _ in range(10):
            frames = pipeline.wait_for_frames()
            
        depth_frame = frames.get_depth_frame()
        
        # Generate point cloud
        pc = rs.pointcloud()
        points = pc.calculate(depth_frame)
        
        # Convert to Open3D format
        v = points.get_vertices()
        verts = np.asanyarray(v).view(np.float32).reshape(-1, 3)
        
        # Filter out zero-depth points
        verts = verts[~np.all(verts == 0, axis=1)]
        
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(verts)
        
        # Downsample slightly for easier processing
        pcd = pcd.voxel_down_sample(voxel_size=0.005)
        pcd.paint_uniform_color(color)
        
        pipeline.stop()
        print(f"Captured {len(pcd.points)} points from Camera {serial}.")
        return pcd

    def pick_points(self, pcd, window_name="Pick Points"):
        """Opens Open3D visualizer to pick points manually."""
        print(f"\n--- INSTRUCTIONS FOR: {window_name} ---")
        print("1) Hold [Shift] + [Left Click] to select a point.")
        print("2) Select 3 or 4 distinct features on the calibration object.")
        print("3) Close the window when finished.")
        
        vis = o3d.visualization.VisualizerWithEditing()
        vis.create_window(window_name=window_name, width=1280, height=720)
        vis.add_geometry(pcd)
        vis.run() 
        vis.destroy_window()
        
        return vis.get_picked_points()

    def calibrate(self):
        print("=== MULTI-CAMERA EXTRINSIC CALIBRATION ===")
        print("Place an asymmetric object (like a box with markings) where BOTH cameras can see it.")
        input("Press [ENTER] to capture 3D frames...")

        # Capture clouds (Cam 1 = Blue, Cam 2 = Yellow)
        pcd1 = self.capture_pointcloud(CAM1SERIAL, [0, 0.651, 0.929])
        pcd2 = self.capture_pointcloud(CAM2SERIAL, [1, 0.706, 0])

        print("\nSTEP 1: Pick points on Camera 1 (Base)")
        indices1 = self.pick_points(pcd1, "Camera 1 (Base) - Pick 3+ points")

        print("\nSTEP 2: Pick points on Camera 2 (Target)")
        print("WARNING: You must click the exact same physical corners in the EXACT SAME ORDER.")
        indices2 = self.pick_points(pcd2, "Camera 2 (Target) - Pick the same points")

        if len(indices1) < 3 or len(indices1) != len(indices2):
            print("\n❌ Error: You must pick at least 3 points, and the number of points must match.")
            return

        # Map the points
        corr = np.zeros((len(indices2), 2))
        corr[:, 0] = indices2
        corr[:, 1] = indices1
        corres_pairs = o3d.utility.Vector2iVector(corr)

        # Compute transformation matrix
        print("\nCalculating 4x4 Transformation Matrix...")
        estimator = o3d.pipelines.registration.TransformationEstimationPointToPoint()
        matrix = estimator.compute_transformation(pcd2, pcd1, corres_pairs)

        print("\n==========================================")
        print("✅ SUCCESS! COPY THIS INTO YOUR MAIN SCRIPT:")
        print("==========================================")
        print("TRANSFORM_CAM2_TO_GLOBAL = np.array([")
        for row in matrix:
            print(f"    [{row[0]: 10.6f}, {row[1]: 10.6f}, {row[2]: 10.6f}, {row[3]: 10.6f}],")
        print("])")
        print("==========================================\n")

        # Visualize the aligned result
        print("Visualizing alignment. The Blue and Yellow objects should overlap perfectly.")
        pcd2_aligned = copy.deepcopy(pcd2)
        pcd2_aligned.transform(matrix)
        
        # Create coordinate frame at origin
        origin = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1, origin=[0, 0, 0])
        
        o3d.visualization.draw_geometries([pcd1, pcd2_aligned, origin], window_name="Final Alignment Verification")


    def compare_pointclouds(self, pcd):
        # 1. Downsample and extract features from the live RealSense point cloud
        voxel_size = 0.01 # 1cm voxels for speed
        live_down = pcd.voxel_down_sample(voxel_size)
        live_down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size*2, max_nn=30))
        
        live_features = o3d.pipelines.registration.compute_fpfh_feature(
        live_down, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size*5, max_nn=100))

        # 2. Global Registration: Figure out rough orientation (Which side are we facing?)
        result_ransac = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            live_down, self.current_pcd["pcd"], live_features, self.current_pcd["features"], True,
            max_correspondence_distance=voxel_size*1.5,
            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPoint(False),
            ransac_n=3,
            checkers=[o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
                    o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(voxel_size*1.5)],
            criteria=o3d.pipelines.registration.RANSACConvergenceCriteria(100000, 0.999))

        # 3. Local Registration: Tightly snap the live points to the CAD model
        icp_result = o3d.pipelines.registration.registration_icp(
            pcd, self.current_pcd["pcd"], max_correspondence_distance=0.02, # 2cm tolerance
            init=result_ransac.transformation,
            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane())

        # fitness represents the overlapping area (% likeness)
        likeness_percentage = icp_result.fitness * 100 
        
        # Return the transformation matrix (position relative to camera) and the likeness
        return likeness_percentage