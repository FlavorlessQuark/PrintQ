import open3d as o3d


def single_view_comparison(live_pcd, cad_pcd, cad_features):
    """
    Figures out which part of the object the camera is facing and calculates likeness.
    """
    # 1. Downsample and extract features from the live RealSense point cloud
    voxel_size = 0.01 # 1cm voxels for speed
    live_down = live_pcd.voxel_down_sample(voxel_size)
    live_down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size*2, max_nn=30))
    
    live_features = o3d.pipelines.registration.compute_fpfh_feature(
        live_down, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size*5, max_nn=100))

    # 2. Global Registration: Figure out rough orientation (Which side are we facing?)
    result_ransac = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
        live_down, cad_pcd, live_features, cad_features, True,
        max_correspondence_distance=voxel_size*1.5,
        estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPoint(False),
        ransac_n=3,
        checkers=[o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
                  o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(voxel_size*1.5)],
        criteria=o3d.pipelines.registration.RANSACConvergenceCriteria(100000, 0.999))

    # 3. Local Registration: Tightly snap the live points to the CAD model
    icp_result = o3d.pipelines.registration.registration_icp(
        live_pcd, cad_pcd, max_correspondence_distance=0.02, # 2cm tolerance
        init=result_ransac.transformation,
        estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane())

    # fitness represents the overlapping area (% likeness)
    likeness_percentage = icp_result.fitness * 100 
    
    # Return the transformation matrix (position relative to camera) and the likeness
    return icp_result.transformation, likeness_percentage