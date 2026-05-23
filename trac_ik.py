import numpy as np
from trac_ik import TracIK

ik_solver = TracIK(
    base_link_name="base_link",
    tip_link_name="link6",
    urdf_path="piper.urdf"
)

target_position = np.array([0.3, 0.1, 0.4])
target_rotation = np.eye(3) 
seed_state = np.zeros(ik_solver.number_of_joints)
try:
    solution = ik_solver.ik(
        target_position, 
        target_rotation, 
        seed_jnt_values=seed_state
    )
    
    if solution is not None:
        print("solution:")
        print(np.round(solution, 3))
    else:
        print("Nope")
        
except Exception as e:
    print("Error during IK calculation:", e)