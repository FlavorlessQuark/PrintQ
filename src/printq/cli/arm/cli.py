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

    table = Table(title="ARM Joint Positions", title_style="bold cyan")
    table.add_column("Joint", style="bold", justify="left")
    table.add_column("Radians", justify="right")
    table.add_column("Degrees", justify="right")

    for idx, position in enumerate(joint_positions, start=1):
        table.add_row(
            f"J{idx}",
            f"{position:.4f}",
            f"{math.degrees(position):.2f}°",
        )

    console.print(table)

@arm_commands.command(name="go-to-zero")
def go_to_zero():
    """Go to the zero position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_zero()