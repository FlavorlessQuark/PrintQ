import open3d as o3d
import numpy as np
import trimesh

import RealsenseCamera

CAM1SERIAL = "000000000001"
CAM2SERIAL = "000000000002"

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