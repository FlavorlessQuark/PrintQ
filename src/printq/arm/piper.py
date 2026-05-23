"""Piper ARM control."""

import time
from logging import getLogger

from piper_control import piper_init, piper_interface

logger = getLogger(__name__)


class PiperArm:
    """Piper ARM control."""

    JOINT_POSITIONS_READY = (0.04, 0.45, -1.5, 0.0, 1.0, 0.0)
    # Gripper "ready" pose. Position is in meters (V2) or radians (V1);
    # effort is in wrapper units where 1.0 corresponds to the SDK demo's
    # default torque of 1000.
    GRIPPER_READY_POSITION = 0.0
    GRIPPER_READY_EFFORT = 1.0

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
        self.piper.command_joint_positions(positions=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
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