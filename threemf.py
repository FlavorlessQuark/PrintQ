import open3d as o3d
import numpy as np
import cv2
import trimesh
import pyrealsense2 as rs

def load_3mf_as_pointcloud(filepath, num_points=20000):
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
    
    return o3d_mesh, cad_pcd, cad_features