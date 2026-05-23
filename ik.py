from ikpy.chain import Chain
import threading
import time
import numpy as np

piper_chain = Chain.from_urdf_file("piper_description.urdf")

def print_chain_info():
    """
    Use for verifying which indices correspond to joints
    """
    for i, link in enumerate(piper_chain.links):
        print(i, link.name, link.bounds)

def solution_in_bounds(ik_solution, tolerance=1e-5):
    """
    Check if full ik solution is within bounds
    """
    for i, link in enumerate(piper_chain.links):
        if link.bounds is None or len(link.bounds) != 2:
            continue
        
        lower_limit, upper_limit = link.bounds
        joint_angle = ik_solution[i]

        if joint_angle < (lower_limit - tolerance) or joint_angle > (upper_limit + tolerance):
            print(
                "Out of bounds: ",
                link.name,
                "angle: ",
                joint_angle,
                "bounds: ", 
                lower_limit,
                upper_limit
            )
            return False
    
    return True

def solve_ik_for_target(target_position, target_orientation=None):
    """
    Target position should be [x,y,z] in meters
    """
    if target_orientation is None:
        target_orientation = [0, 0, -1]

    initial_pos = [0.0] * len(piper_chain.links)

    ik_solution = piper_chain.inverse_kinematics(
        target_position=target_position,
        target_orientation=target_orientation,
        initial_position=initial_pos,
    )

    print("Full ik solution:", np.round(ik_solution, 3))

    if not solution_in_bounds(ik_solution):
        print("Solution not in bounds, return none")
        return None

    joints = ik_solution[1:7] # double check what is returned in position 0 to see what indices to take here

    if len(joints) != 6:
        raise ValueError(f"Expected 6 joints, got {len(joints)}")

    return [float(joint) for joint in joints]


def get_object_target_position():
    # Replace with real object detection output
    return [0.3, 0.1, 0.4]


def ik_loop(send_command_callback):
    while True:
        target_position = get_object_target_position()

        if target_position is not None:
            try:
                joints = solve_ik_for_target(target_position)
                if joints is not None:
                    send_command_callback(joints)
            except Exception as error:
                print("ik failed: ", error)

        time.sleep(0.05)


def start_ik_loop(send_command_callback):
    thread = threading.Thread(
        target=ik_loop,
        args=(send_command_callback,),
        daemon=True,
    )
    thread.start()
    return thread