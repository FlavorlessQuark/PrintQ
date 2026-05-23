"""Piper ARM control."""

import time
import numpy as np
from ikpy.chain import chain
from ikpy.link import OriginLink, URDFLink
from logging import getLogger

from piper_control import piper_init, piper_interface

logger = getLogger(__name__)


class PiperArm:
    """Piper ARM control."""

    JOINT_POSITIONS_ZERO = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    JOINT_POSITIONS_READY = (0.04, 0.45, -1.5, 0.0, 1.0, 0.0)
    JOINT_POSITIONS_PREGRASP = (0.02, 1.87, -0.53, 0.04, -1.24, 0.08)
    JOINT_POSITIONS_SCAN = (1.23, -0.01, -0.52, -0.01, 0.58, 0.01)
    JOINT_POSITIONS_GOOD_BIN = (-0.46, 1.84, -0.72, 0.02, -0.69, 0.02)
    JOINT_POSITIONS_BAD_BIN = (0.63, 1.84, -0.73, 0.02, -0.69, 0.02)
    # Gripper "ready" pose. Position is in meters (V2) or radians (V1);
    # effort is in wrapper units where 1.0 corresponds to the SDK demo's
    # default torque of 1000.
    GRIPPER_READY_POSITION = 0.0
    GRIPPER_READY_EFFORT = 1.0
    GRIPPER_PREGRASP_POSITION = 10.0
    GRIPPER_PREGRASP_EFFORT = 1.0
    GRIPPER_BIN_POSITION = 10.0
    GRIPPER_BIN_EFFORT = 1.0
    GRIPPER_OPEN_POSITION = 10.0
    GRIPPER_CLOSED_POSITION = 0.0
    GRIPPER_DEFAULT_EFFORT = 1.0

    chain = None

    def __init__(self, can_port: str = "can0"):
        """Initialize the PiperArm."""
        logger.info(f"Initializing PiperArm on {can_port}")
        self.piper = piper_interface.PiperInterface(can_port=can_port)
        # Resets the robot and enables the motors and motion controller for the arm.
        # This call is necessary to be able to both query state and send commands to the
        # robot.
        logger.info("Resetting the ARM")
        piper_init.reset_arm(
            self.piper,
            arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
            move_mode=piper_interface.MoveMode.JOINT,
        )
        logger.info("Resetting the GRIPPER")
        piper_init.reset_gripper(self.piper)
        logger.info("PiperArm initialized")
        self.chain = chain.from_urdf_file("piper_description.urdf")

    def move_ik(self, end_off):
        joints = np.array(self.get_joint_positions())
        new_joints = joints.copy()
        new_joints[:-1] += 2
        ik_solution = chain.inverse_kinematics(
            target_position=end_off,
            target_orientation=self.piper.get_end_pose().orientation,
            initial_position=joints
        )
        tolerance = 1e-5
            
        for i, link in enumerate(chain.links):
            if link.bounds is None or len(link.bounds) != 2:
                continue
                
            lower_limit, upper_limit = link.bounds
            joint_angle = ik_solution[i]
            
            if joint_angle < (lower_limit - tolerance) or joint_angle > (upper_limit + tolerance):
                print("Out of bounds")
        print("IK solution:", ik_solution)
        return ik_solution



    def get_joint_positions(self) -> tuple[float, ...]:
        """Get the joint positions of the ARM.

        Returns:
            A tuple of joint positions.
        """
        return self.piper.get_joint_positions()

    def get_gripper_state(self) -> tuple[float, float]:
        """Get the gripper state.

        Returns:
            A tuple ``(angle, effort)``. Angle units depend on the gripper
            model (radians for V1, meters for V2 parallel grippers).
        """
        return self.piper.get_gripper_state()

    def go_to_zero(self):
        """Go to the zero position"""
        logger.info("Going to zero position")
        self.piper.command_joint_positions(positions=self.JOINT_POSITIONS_ZERO)
        logger.info("commanded zero position....")

    def go_to_ready(self):
        """Go to the ready position"""
        logger.info("Going to ready position")
        self.piper.command_joint_positions(positions=self.JOINT_POSITIONS_READY)
        logger.info("commanded ready position....")

        # move the gripper to the ready position
        logger.info("Going to gripper ready position")
        self.piper.command_gripper(
            position=self.GRIPPER_READY_POSITION,
            effort=self.GRIPPER_READY_EFFORT,
        )
        logger.info("commanded gripper ready position....")
    
    def go_to_pregrasp(self):
        """Go to the pre-grasp position"""
        logger.info("Going to pre-grasp position")
        self.piper.command_joint_positions(positions=self.JOINT_POSITIONS_PREGRASP)
        logger.info("commanded pre-grasp position....")

        # move the gripper to the scan position
        logger.info("Going to gripper pre-grasp position")
        self.piper.command_gripper(
            position=self.GRIPPER_PREGRASP_POSITION,
            effort=self.GRIPPER_PREGRASP_EFFORT,
        )
        logger.info("commanded gripper pre-grasp position....")

    def go_to_scan(self):
        """Go to the scan position"""
        logger.info("Going to scan position")
        self.piper.command_joint_positions(positions=self.JOINT_POSITIONS_SCAN)
        logger.info("commanded scan position....")
    
    def go_to_good_bin(self):
        """Go to the good bin"""
        logger.info("Going to good bin position")
        self.piper.command_joint_positions(positions=self.JOINT_POSITIONS_GOOD_BIN)
        logger.info("commanded good bin position....")

        # move the gripper to the good bin position
        logger.info("Going to gripper bin position")
        self.piper.command_gripper(
            position=self.GRIPPER_BIN_POSITION,
            effort=self.GRIPPER_BIN_EFFORT,
        )
        logger.info("commanded gripper bin position....")

    def open_gripper(self):
        """Open the gripper fully."""
        logger.info("Opening gripper")
        self.piper.command_gripper(
            position=self.GRIPPER_OPEN_POSITION,
            effort=self.GRIPPER_DEFAULT_EFFORT,
        )
        logger.info("commanded gripper open....")

    def close_gripper(self):
        """Close the gripper fully."""
        logger.info("Closing gripper")
        self.piper.command_gripper(
            position=self.GRIPPER_CLOSED_POSITION,
            effort=self.GRIPPER_DEFAULT_EFFORT,
        )
        logger.info("commanded gripper close....")

    def disable(
        self,
        settle_timeout: float = 10.0,
        position_tolerance: float = 0.1,
    ) -> None:
        """Gracefully return the arm to zero and power down the motors.

        Commands the arm to zero, polls joint positions until they are
        within ``position_tolerance`` of zero (or ``settle_timeout``
        elapses), then disables the gripper and the arm via the
        ``piper_init`` blocking helpers.

        WARNING: Disabling powers down the motors. The settle step exists
        so that the arm is at its resting zero pose before it loses
        power; if it cannot reach zero within the timeout, this method
        still disables and the arm may drop from its last commanded pose.

        Args:
            settle_timeout: Max seconds to wait for the arm to reach zero
                before disabling anyway.
            position_tolerance: Per-joint radian tolerance for "at zero".
        """
        self.go_to_zero()

        logger.info("Waiting for arm to settle at zero...")
        deadline = time.monotonic() + settle_timeout
        settled = False
        while time.monotonic() < deadline:
            positions = self.piper.get_joint_positions()
            if all(abs(p) < position_tolerance for p in positions):
                settled = True
                break
            time.sleep(0.1)

        if not settled:
            logger.warning(
                "Arm did not settle at zero within %.1fs; disabling anyway.",
                settle_timeout,
            )

        logger.info("Disabling gripper")
        piper_init.disable_gripper(self.piper)
        logger.info("Disabling arm")
        piper_init.disable_arm(self.piper)
        logger.info("Arm disabled")

    def go_to_bad_bin(self):
        """Go to the bad bin drop-off"""
        logger.info("Going to bad bin drop-off position")
        self.piper.command_joint_positions(positions=self.JOINT_POSITIONS_BAD_BIN)
        logger.info("commanded bad bin drop-off position....")

        # move the gripper to the bad bin position
        logger.info("Going to gripper bin position")
        self.piper.command_gripper(
            position=self.GRIPPER_BIN_POSITION,
            effort=self.GRIPPER_BIN_EFFORT,
        )
        logger.info("commanded gripper bin position....")

    def calibrate_joints(self) -> None:
        """Calibrate every joint sequentially by setting its current pose as zero.

        For each joint from 1 through 6, the routine:
          1. Disables that single motor so it can be moved by hand.
          2. Prompts the operator to physically move the joint to its zero pose.
          3. On Enter, sets the joint's current position as zero.
          4. Re-enables the motor before moving on to the next joint.

        Enter ``q`` at any prompt to abort calibration early. The current motor
        will be re-enabled before the routine returns.
        """
        logger.warning(
            "Calibration disables motors one at a time. "
            "Support the arm so it does not fall."
        )

        # The wrapper exposes only whole-arm enable/disable, so reach through
        # to the underlying SDK for per-motor control.
        raw_sdk = self.piper.piper

        for joint_num in range(1, 7):
            raw_sdk.DisableArm(joint_num)
            logger.info(
                f"Joint {joint_num} disabled. "
                "Manually move it to its zero position."
            )

            answer = input(
                f"Press Enter to set zero for joint {joint_num} "
                "(or 'q' to abort): "
            )
            if answer.strip().lower() == "q":
                raw_sdk.EnableArm(joint_num)
                logger.warning("Calibration aborted by user.")
                return

            self.piper.set_joint_zero_positions([joint_num - 1])
            raw_sdk.EnableArm(joint_num)
            logger.info(f"Joint {joint_num} zero set and re-enabled.")

        logger.info("All joints calibrated.")


    def calibrate_gripper(self) -> None:
        """Set the gripper's current position as its zero.

        Manually move the gripper to the desired zero pose before calling
        this method. A confirmation prompt is shown before the zero is
        committed; respond with ``y``/``yes`` to proceed, anything else to
        abort.

        The routine mirrors the SDK ``piper_set_gripper_zero`` demo:
        it first sends a disable/settle command to the gripper, waits
        briefly for it to stabilize, then commits the current position
        as the new zero.
        """
        logger.warning(
            "Manually move the gripper to its zero position before continuing."
        )
        answer = input("Set gripper zero at current position? [y/N]: ")
        if answer.strip().lower() not in ("y", "yes"):
            logger.warning("Gripper calibration aborted by user.")
            return

        # Step 1: disable + settle. The wrapper's set_gripper_zero_position()
        # only performs the final commit, so we send this directly via the
        # raw SDK to match the official demo.
        self.piper.piper.GripperCtrl(0, 1000, 0x00, 0)
        time.sleep(1.5)

        # Step 2: commit current position as zero.
        self.piper.set_gripper_zero_position()
        logger.info("Gripper zero position set.")