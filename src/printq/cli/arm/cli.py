""" ARM utility commands."""

from logging import getLogger

import click

from printq.cli import console

logger = getLogger(__name__)


@click.group(name="arm")
@click.pass_context
def arm_commands(ctx):
    """ARM control and testing utilities"""
    ctx.ensure_object(dict)


@arm_commands.command(name="activate")
def activate():
    """Activate all CAN ports"""
    # Set up the connection to the Piper arm.
    # These steps require sudo access.
    from piper_control import piper_connect

    # Print out the CAN ports that are available to connect.
    logger.info(f"CAN ports: {piper_connect.find_ports()}")

    # Activate all the ports so that you can connect to any arms connected to your
    # machine.
    piper_connect.activate()

    # Check to see that all the ports are active.
    logger.info(f"Active ports: {piper_connect.active_ports()}")

@arm_commands.command()
def status():
    """Get the status of the ARM"""
    import math

    from rich.table import Table

    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    joint_positions = piper_arm.get_joint_positions()
    gripper_angle, gripper_effort = piper_arm.get_gripper_state()

    joints_table = Table(title="ARM Joint Positions", title_style="bold cyan")
    joints_table.add_column("Joint", style="bold", justify="left")
    joints_table.add_column("Radians", justify="right")
    joints_table.add_column("Degrees", justify="right")

    for idx, position in enumerate(joint_positions, start=1):
        joints_table.add_row(
            f"J{idx}",
            f"{position:.4f}",
            f"{math.degrees(position):.2f}°",
        )

    gripper_table = Table(title="Gripper State", title_style="bold cyan")
    gripper_table.add_column("Field", style="bold", justify="left")
    gripper_table.add_column("Value", justify="right")
    gripper_table.add_row("Angle", f"{gripper_angle:.4f}")
    gripper_table.add_row("Effort", f"{gripper_effort:.4f}")

    console.print(joints_table)
    console.print(gripper_table)

@arm_commands.command(name="go-to-zero")
def go_to_zero():
    """Go to the zero position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_zero()


@arm_commands.command(name="calibrate")
@click.option(
    "--joints",
    "joints",
    is_flag=True,
    default=False,
    help="Calibrate all 6 arm joints sequentially.",
)
@click.option(
    "--gripper",
    "gripper",
    is_flag=True,
    default=False,
    help="Calibrate the gripper zero position.",
)
def calibrate(joints: bool, gripper: bool):
    """Calibrate the ARM joints or the gripper.

    Exactly one of --joints or --gripper must be provided.
    """
    if joints and gripper:
        raise click.UsageError(
            "--joints and --gripper are mutually exclusive; specify only one."
        )
    if not joints and not gripper:
        raise click.UsageError("Specify exactly one of --joints or --gripper.")

    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    if joints:
        piper_arm.calibrate_joints()
    else:
        piper_arm.calibrate_gripper()