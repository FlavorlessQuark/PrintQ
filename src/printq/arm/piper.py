"""Piper ARM control."""

from logging import getLogger

from piper_control import piper_init, piper_interface

logger = getLogger(__name__)


class PiperArm:
    """Piper ARM control."""

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

    def go_to_zero(self):
        """Go to the zero position"""
        logger.info("Going to zero position")
        self.piper.command_joint_positions(positions=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        logger.info("commanded zero position....")