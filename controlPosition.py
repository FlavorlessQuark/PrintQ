import time
import threading
from piper_control import *

CONTROL_FREQ_HZ = 50

START_COMMAND = None # Add starting position values []

command_lock = threading.Lock()
new_command = None

def normalize_command(command):
    """
    Expects ik output in this format: 
        [j1, j2, j3, j4, j5, j6]
    """

    if command is None:
        return None

    command = list(command)

    if len(command) != 6:
        raise ValueError("Command must contain 6 joint positions")

    return [float(value) for value in command]

def send_command(command):
    """
    Calls ik thread
    Stores ik command so Piper control loop can use
    """
    global new_command

    command = normalize_command(command)

    if command is None:
        return

    with command_lock:
        new_command = command

def get_new_command():
    """
    Check if ik sent new command
    If yes, returns it and clears
    If no, returns none
    """
    global new_command

    with command_lock:
        if new_command is None:
            return None
        
        command = new_command
        new_command = None
        return command

def setup_can():
    """
    Activate CAN ports before connecting to robot
    """
    print(piper_connect.find_ports())
    piper_connect.activate()
    print(piper_connect.active_ports())

def setup_piper():
    """
    Connect to piper and reset arm
    """
    setup_can()

    piper = piper_interface.PiperInterface(can_port="can0")
    piper_init.reset_arm(
        piper,
        arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
        move_mode=piper_interface.MoveMode.JOINT
    )
    piper_init.reset_gripper(piper)

    return piper

def run_control_loop(control_freq, command):
    """
    control_freq: how many times per second to send commands
    command: joint position command
    """
    piper = setup_piper()

    if command is None:
        previous_command = piper.get_joint_positions()
        print("No start position, hold current position")
    else:
        previous_command = normalize_command(command)
        print("Moving to start position")

    piper.command_joint_positions(previous_command)

    time.sleep(2.0)

    dt = 1.0 / control_freq

    print("Starting control loop")

    while True:
        loop_start = time.monotonic()

        current_new_command = get_new_command()

        if current_new_command is not None:
            previous_command = current_new_command
            print("New command: ", previous_command)

        piper.command_joint_positions(previous_command)

        elapsed = time.monotonic() - loop_start
        sleep_time = dt - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

def start_ik_system():
    try:
        import ik

        if not hasattr(ik, "ik_loop"):
            raise AttributeError("ik.py must define ik_loop")
        
        ik.ik_loop(send_command)
        print("Started ik system")
        return True

    except Exception as error:
        print("Couldn't start ik system")
        print(error)
        return False

def main():
    ik_started = start_ik_system()

    if not ik_started:
        print("ik system didn't start, piper will hold position")
    
    try:
        run_control_loop(CONTROL_FREQ_HZ, START_COMMAND)
    except KeyboardInterrupt:
        print("Stopping control loop")

if __name__ == "__main__":
    main()