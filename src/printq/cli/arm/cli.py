""" ARM utility commands."""

from contextlib import contextmanager
from logging import getLogger
from typing import TYPE_CHECKING

import click

from printq.cli import console

if TYPE_CHECKING:
    from collections.abc import Callable

    from printq.arm.piper import PiperArm

logger = getLogger(__name__)
chain = None

@click.group(name="arm")
@click.pass_context
def arm_commands(ctx):
    """ARM control and testing utilities"""
    ctx.ensure_object(dict)


def _print_status(piper_arm: "PiperArm") -> None:
    """Render the arm's current joint positions and gripper state."""
    import math

    from rich.table import Table

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


@contextmanager
def _disable_on_interrupt(piper_arm: "PiperArm"):
    """Context manager: on ``KeyboardInterrupt``, disable the arm gracefully.

    Catches ``KeyboardInterrupt`` raised inside the ``with`` block (whether
    from a real Ctrl-C or from code that explicitly raises it) and runs
    ``piper_arm.disable()`` to return the arm to zero and power down the
    motors. Other exceptions propagate unchanged.
    """
    try:
        yield
    except KeyboardInterrupt:
        logger.info("Interrupt received - disabling arm gracefully.")
        piper_arm.disable()
        logger.info("Exiting.")


def _hold_until_interrupt(piper_arm: "PiperArm") -> None:
    """Block until the user sends Ctrl-C, then disable the arm.

    While holding, the operator can press ``s`` at any time to print the
    current arm status without exiting. Ctrl-C ends the hold, disables
    the arm gracefully, and exits.
    """
    console.print(
        "\n[yellow]Holding pose. "
        "Press [bold]s[/bold] to read status, "
        "or [bold]Ctrl-C[/bold] to disable the arm and exit.[/yellow]"
    )
    with _disable_on_interrupt(piper_arm):
        while True:
            # click.getchar() returns a single character without requiring
            # Enter. On Unix it doesn't raise on Ctrl-C; it returns '\x03'
            # instead, so handle that explicitly. On Windows it raises
            # KeyboardInterrupt, which the context manager still catches.
            ch = click.getchar()
            if ch in ("\x03", "\x04"):  # Ctrl-C, Ctrl-D
                raise KeyboardInterrupt
            if ch.lower() == "s":
                _print_status(piper_arm)


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
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    _print_status(piper_arm)

@arm_commands.command(name="go-to-zero")
def go_to_zero():
    """Go to the zero position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_zero()
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="go-to-ready")
def go_to_ready():
    """Go to the ready position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_zero()
    piper_arm.go_to_ready()
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="go-to-scan")
def go_to_scan():
    """Go to the scan position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_scan()
    _hold_until_interrupt(piper_arm)

def _prompt_ik_target() -> tuple[float, float, float]:
    """Prompt the operator for an end-effector target (meters)."""
    x = click.prompt("Target x (meters)", type=float, default=0.3)
    y = click.prompt("Target y (meters)", type=float, default=0.0)
    z = click.prompt("Target z (meters)", type=float, default=0.3)
    return x, y, z


@arm_commands.command(name="move-ik")
@click.option("--x", type=float, default=None, help="Target x in meters.")
@click.option("--y", type=float, default=None, help="Target y in meters.")
@click.option("--z", type=float, default=None, help="Target z in meters.")
def move_ik(x: float | None, y: float | None, z: float | None):
    """Move to a position using inverse kinematics"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    if x is None or y is None or z is None:
        target = _prompt_ik_target()
    else:
        target = (x, y, z)
    piper_arm.move_ik(target)
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="go-to-good-bin")
def go_to_good_bin():
    """Go to the good bin position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_good_bin()
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="go-to-bad-bin")
def go_to_bad_bin():
    """Go to the bad bin position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_bad_bin()
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="go-to-pregrasp")
def go_to_pregrasp():
    """Go to the pre-grasp position"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    piper_arm.go_to_pregrasp()
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="set-gripper")
@click.option(
    "--position",
    type=click.FloatRange(0.0, 10.0),
    default=None,
    help="Gripper position: 0.0=fully closed, 10.0=fully open",
)
def set_gripper_command(position: float | None):
    """Set the gripper to a user-provided position from 0.0 to 10.0"""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()

    if position is None:
        position = click.prompt(
            "Gripper position? 0.0=fully closed, 10.0=fully open",
            type=click.FloatRange(0.0, 10.0),
            default=0.0,
        )

    piper_arm.set_gripper(position)
    _hold_until_interrupt(piper_arm)

@arm_commands.command(name="control")
def control():
    """Interactively move the arm and gripper between presets until Ctrl-C.

    Presents a menu of preset arm positions and gripper actions on each
    iteration. Press the number key for an entry to dispatch it, then
    the menu reappears for the next selection. Ctrl-C (or 'q') exits
    the loop, gracefully returns the arm to zero, and disables the motors.
    """
    from rich.table import Table

    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()
    def _set_gripper_prompt() -> None:
        gripper_position = click.prompt(
            "Gripper position? 0.0=fully closed, 10.0=fully open",
            type=click.FloatRange(0.0, 10.0),
            default=0.0,
        )
        piper_arm.set_gripper(gripper_position)

    def _move_ik_prompt() -> None:
        piper_arm.move_ik(_prompt_ik_target())

    positions: list[tuple[str, "Callable[[], None]"]] = [
        ("zero", piper_arm.go_to_zero),
        ("ready", piper_arm.go_to_ready),
        ("pre-grasp", piper_arm.go_to_pregrasp),
        ("scan", piper_arm.go_to_scan),
        ("good bin", piper_arm.go_to_good_bin),
        ("bad bin", piper_arm.go_to_bad_bin),
        ("open gripper", piper_arm.open_gripper),
        ("close gripper", piper_arm.close_gripper),
        ("move ik", _move_ik_prompt),
        ("set gripper position", _set_gripper_prompt),
    ]

    def _show_menu() -> None:
        table = Table(title="Go To", title_style="bold cyan")
        table.add_column("Key", style="bold yellow", justify="right")
        table.add_column("Position", style="bold")
        for i, (label, _) in enumerate(positions, start=1):
            table.add_row(str(i), label)
        console.print(table)
        console.print(
            f"[dim]Press [bold]1-{len(positions)}[/bold] to move, "
            "or [bold]Ctrl-C[/bold] / [bold]q[/bold] to exit.[/dim]"
        )

    with _disable_on_interrupt(piper_arm):
        while True:
            _show_menu()
            ch = click.getchar()
            if ch in ("\x03", "\x04") or ch.lower() == "q":
                raise KeyboardInterrupt
            if not ch.isdigit():
                continue
            idx = int(ch) - 1
            if not (0 <= idx < len(positions)):
                continue
            label, action = positions[idx]
            logger.info(f"Moving to {label} position...")
            action()

@arm_commands.command(name="run-print-cycle")
def run_print_cycle():
    """Run the full autonomous print pickup, scan, and sorting cycle."""
    from printq.arm.piper import PiperArm

    piper_arm = PiperArm()

    def get_ik_grasp_joints():
        """Replace this with actual IK output."""
        import ik

        # if ik.py has a function that returns the final grasp joints:
        # return ik.get_grasp_joint_positions()

        # temporary hardcoded target using your existing IK function:
        target_position = [0.3, 0.1, 0.4]
        return ik.solve_ik_for_target(target_position)

    def get_bambu_gripper_close_value():
        """Replace this with Bambu Lab print-derived gripping logic"""

        # Temporary safe default. Tune this on the actual print.
        return 5.0

        # Later example:
        # from printq.bambu import get_current_print_grip_value
        # return get_current_print_grip_value()

    def get_vlm_decision():
        """Replace this with VLM quality-check result"""

        # Temporary manual fallback for testing
        decision = click.prompt(
            "VLM decision? Type good or bad",
            type=click.Choice(["good", "bad"], case_sensitive=False),
        )
        return decision

        # Later example:
        # from printq.vision.vlm import classify_current_print
        # return classify_current_print()

    with _disable_on_interrupt(piper_arm):
        decision = piper_arm.run_print_cycle(
            get_ik_grasp_joints=get_ik_grasp_joints,
            get_bambu_gripper_close_value=get_bambu_gripper_close_value,
            get_vlm_decision=get_vlm_decision,
        )

    console.print(f"Print cycle complete. Decision: {decision}")

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