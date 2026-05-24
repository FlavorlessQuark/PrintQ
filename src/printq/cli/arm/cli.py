"""ARM utility commands.

The CLI is the primary test harness for the iceoryx2 services exposed by
:class:`printq.arm.piper.PiperArm`. There are two kinds of commands:

  * ``printq arm start`` is the *server*: it spawns the control-loop child
    process that owns CAN and keeps it alive until Ctrl-C, then cleanly
    stops the loop (which in turn commands the arm to zero and disables
    the motors).

  * Every other command (``go-to-*``, ``set-gripper``, ``status``,
    ``send-command``, ``estop``, ``clear-estop``, ``control``,
    ``run-print-cycle``) is a *client*: it publishes to / subscribes from
    the iceoryx2 services and fails with a clear error message if the
    control loop is not running.

``calibrate`` is special: it does NOT go through iceoryx2 (it needs
direct per-motor control), and it refuses to run when a control loop is
detected.
"""

from contextlib import contextmanager
from logging import getLogger
from typing import TYPE_CHECKING

import click

from printq.cli import console

if TYPE_CHECKING:
    from collections.abc import Callable

    from printq.arm.piper import PiperArm

logger = getLogger(__name__)


@click.group(name="arm")
@click.pass_context
def arm_commands(ctx):
    """ARM control and testing utilities"""
    ctx.ensure_object(dict)


# ----------------------------------------------------------------------- helpers


def _new_client() -> "PiperArm":
    """Return a fresh service-only :class:`PiperArm`.

    The constructor does not touch CAN, so this is safe to call from any
    one-shot CLI subcommand. The first method invocation will lazily set
    up the iceoryx2 node and (where required) verify that a control loop
    is alive.
    """
    from printq.arm.piper import PiperArm

    return PiperArm()


@contextmanager
def _handle_arm_not_running():
    """Translate :class:`ArmNotRunningError` into a clean CLI failure."""
    from printq.arm.piper import ArmNotRunningError

    try:
        yield
    except ArmNotRunningError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise click.exceptions.Exit(code=1) from exc


def _mode_label(mode: int) -> str:
    from printq.arm.piper import ControlMode

    return {
        ControlMode.IDLE: "IDLE",
        ControlMode.TRACKING: "TRACKING",
        ControlMode.HOLDING: "HOLDING",
        ControlMode.ESTOPPED: "ESTOPPED",
        ControlMode.DISABLED: "DISABLED",
    }.get(mode, f"UNKNOWN({mode})")


def _flag_labels(flags: int) -> list[str]:
    from printq.arm.piper import ControlFlags

    out = []
    for name in (
        "WATCHDOG_TRIPPED",
        "ESTOP_ACTIVE",
        "OUT_OF_BOUNDS",
        "RATE_LIMITED",
        "CYCLE_OVERRUN",
        "CAN_ERROR",
    ):
        bit = getattr(ControlFlags, name)
        if flags & bit:
            out.append(name)
    return out


def _print_status(piper_arm: "PiperArm") -> None:
    """Subscribe to feedback/gripper/heartbeat and render a single snapshot."""
    import math

    from rich.table import Table

    heartbeat = piper_arm.get_heartbeat()
    joint_positions = piper_arm.get_joint_positions()
    gripper_angle, gripper_effort = piper_arm.get_gripper_state()

    hb_table = Table(title="Control Loop", title_style="bold cyan")
    hb_table.add_column("Field", style="bold")
    hb_table.add_column("Value", justify="right")
    hb_table.add_row("Cycle", str(int(heartbeat.cycle)))
    hb_table.add_row("Mode", _mode_label(int(heartbeat.mode)))
    flags = _flag_labels(int(heartbeat.flags))
    hb_table.add_row("Flags", ", ".join(flags) if flags else "—")

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

    console.print(hb_table)
    console.print(joints_table)
    console.print(gripper_table)


# --------------------------------------------------------------------- commands


@arm_commands.command(name="activate")
def activate():
    """Activate all CAN ports (requires sudo)."""
    from piper_control import piper_connect

    logger.info(f"CAN ports: {piper_connect.find_ports()}")
    piper_connect.activate()
    logger.info(f"Active ports: {piper_connect.active_ports()}")


@arm_commands.command(name="start")
@click.option(
    "--can-port",
    type=str,
    default="can0",
    show_default=True,
    help="CAN interface name (e.g. can0).",
)
@click.option(
    "--frequency",
    "control_frequency_hz",
    type=click.FloatRange(1.0, 500.0),
    default=None,
    help="Control loop frequency in Hz. Defaults to PiperArm.DEFAULT_CONTROL_FREQUENCY_HZ.",
)
@click.option(
    "--watchdog",
    "watchdog_timeout_s",
    type=click.FloatRange(0.01, 5.0),
    default=None,
    help="Seconds without a fresh command before falling back to HOLDING mode.",
)
def start(
    can_port: str,
    control_frequency_hz: float | None,
    watchdog_timeout_s: float | None,
):
    """Start the PiperArm control loop and stream a live status until Ctrl-C.

    This is a long-running command: it spawns the iceoryx2 control loop
    in a child process (which owns CAN), then prints a heartbeat
    summary periodically. Press Ctrl-C to stop the loop; the child
    will command the arm back to zero and disable motors before
    exiting.
    """
    import time

    from printq.arm.piper import ArmNotRunningError, PiperArm

    arm = PiperArm(can_port=can_port)
    arm.start(
        control_frequency_hz=control_frequency_hz,
        watchdog_timeout_s=watchdog_timeout_s,
    )

    pid = arm._publisher_process.pid if arm._publisher_process else None
    console.print(
        f"[bold green]PiperArm control loop started[/bold green] "
        f"(pid={pid}, freq={arm.control_frequency_hz:.1f} Hz). "
        "Press [bold]Ctrl-C[/bold] to stop."
    )

    # Wait for the first heartbeat. If the child dies during setup (e.g.
    # CAN init, stale iceoryx2 service config), we want to surface that
    # clearly rather than blame a missing heartbeat.
    try:
        arm.get_heartbeat(timeout=5.0)
    except ArmNotRunningError as exc:
        if arm.is_running():
            console.print(f"[bold red]{exc}[/bold red]")
        else:
            console.print(
                "[bold red]Control loop child exited during startup. "
                "Check the traceback above (common causes: CAN bus not activated, "
                "or stale iceoryx2 services from a previous run -- "
                "try wiping /tmp/iceoryx2 if you keep hitting this).[/bold red]"
            )
        arm.stop()
        raise click.exceptions.Exit(code=1) from exc

    try:
        while arm.is_running():
            try:
                hb = arm.get_heartbeat(timeout=2.0)
            except ArmNotRunningError as exc:
                console.print(f"[red]Heartbeat lost: {exc}[/red]")
                break

            flags = _flag_labels(int(hb.flags))
            console.print(
                f"[dim]cycle={int(hb.cycle):>8} "
                f"mode={_mode_label(int(hb.mode)):>9} "
                f"flags={','.join(flags) if flags else '—'}[/dim]"
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping control loop...[/yellow]")
    finally:
        arm.stop()
        console.print("[bold green]Control loop stopped.[/bold green]")


@arm_commands.command(name="stop")
def stop_command():
    """Trigger an e-stop. Use Ctrl-C on 'printq arm start' to fully shut down.

    There is no separate "stop the loop" RPC: the loop is a child of the
    long-running ``printq arm start`` process, so killing that process
    (with Ctrl-C or SIGTERM) is the supported way to bring it down.
    This command publishes an e-stop instead, so the arm holds its
    current pose if you cannot reach the start terminal.
    """
    arm = _new_client()
    with _handle_arm_not_running():
        arm.trigger_estop()
    console.print(
        "[bold red]E-stop triggered.[/bold red] "
        "The control loop will hold the current pose. "
        "Use [bold]printq arm clear-estop[/bold] to release."
    )


@arm_commands.command(name="estop")
def estop_command():
    """Latch the control loop into E-STOPPED (alias of 'arm stop')."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.trigger_estop()
    console.print("[bold red]E-stop latched.[/bold red]")


@arm_commands.command(name="clear-estop")
def clear_estop_command():
    """Release a previously-latched e-stop."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.clear_estop()
    console.print("[bold yellow]E-stop released.[/bold yellow]")


@arm_commands.command()
def status():
    """Print the latest joint, gripper, and control-loop status snapshot."""
    arm = _new_client()
    with _handle_arm_not_running():
        _print_status(arm)


@arm_commands.command(name="go-to-zero")
def go_to_zero():
    """Command the arm to the zero pose."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.go_to_zero()


@arm_commands.command(name="go-to-ready")
def go_to_ready():
    """Command the arm to the ready pose."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.go_to_ready()


@arm_commands.command(name="go-to-pregrasp")
def go_to_pregrasp():
    """Command the arm to the pre-grasp pose."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.go_to_pregrasp()


@arm_commands.command(name="go-to-scan")
def go_to_scan():
    """Command the arm to the scan pose."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.go_to_scan()


@arm_commands.command(name="go-to-good-bin")
def go_to_good_bin():
    """Command the arm to the good-bin drop-off pose."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.go_to_good_bin()


@arm_commands.command(name="go-to-bad-bin")
def go_to_bad_bin():
    """Command the arm to the bad-bin drop-off pose."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.go_to_bad_bin()


@arm_commands.command(name="send-command")
@click.option(
    "--joints",
    "joints",
    type=str,
    required=True,
    help="Comma-separated joint targets in radians (1-6 values).",
)
@click.option(
    "--priority",
    type=int,
    default=0,
    show_default=True,
    help="Priority for multi-publisher arbitration.",
)
@click.option(
    "--max-velocity",
    "max_velocity",
    type=str,
    default=None,
    help=(
        "Optional comma-separated per-joint velocity caps in rad/s. "
        "Must have 6 entries; use 0 to defer to the loop's default cap."
    ),
)
def send_command(joints: str, priority: int, max_velocity: str | None):
    """Publish a raw joint-position command to the control loop."""
    arm = _new_client()
    targets = [float(x.strip()) for x in joints.split(",") if x.strip()]
    vlimit = None
    if max_velocity:
        vlimit = tuple(float(x.strip()) for x in max_velocity.split(",") if x.strip())
    with _handle_arm_not_running():
        arm.send_command(targets, priority=priority, max_velocity=vlimit)
    console.print(f"[green]Published command:[/green] targets={targets} priority={priority}")


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
    """Solve IK for an (x, y, z) end-effector target and publish the joints."""
    arm = _new_client()
    if x is None or y is None or z is None:
        target = _prompt_ik_target()
    else:
        target = (x, y, z)
    with _handle_arm_not_running():
        arm.move_ik(target)


@arm_commands.command(name="set-gripper")
@click.option(
    "--position",
    type=click.FloatRange(0.0, 10.0),
    default=None,
    help="Gripper position: 0.0=fully closed, 10.0=fully open",
)
@click.option(
    "--effort",
    type=float,
    default=None,
    help="Gripper effort (default uses the controller's default).",
)
def set_gripper_command(position: float | None, effort: float | None):
    """Command the gripper to a position from 0.0 to 10.0."""
    arm = _new_client()

    if position is None:
        position = click.prompt(
            "Gripper position? 0.0=fully closed, 10.0=fully open",
            type=click.FloatRange(0.0, 10.0),
            default=0.0,
        )

    with _handle_arm_not_running():
        arm.set_gripper(position, effort=effort)


@arm_commands.command(name="open-gripper")
def open_gripper_command():
    """Fully open the gripper."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.open_gripper()


@arm_commands.command(name="close-gripper")
def close_gripper_command():
    """Fully close the gripper."""
    arm = _new_client()
    with _handle_arm_not_running():
        arm.close_gripper()


@arm_commands.command(name="control")
def control():
    """Interactively dispatch preset poses / gripper actions until Ctrl-C / 'q'.

    Each menu pick publishes one command and returns to the menu. The
    control loop continues to hold the last commanded pose between
    selections (via its watchdog), so the menu is purely a publisher.
    Quitting the menu does NOT stop the loop.
    """
    from rich.table import Table

    arm = _new_client()

    def _set_gripper_prompt() -> None:
        gripper_position = click.prompt(
            "Gripper position? 0.0=fully closed, 10.0=fully open",
            type=click.FloatRange(0.0, 10.0),
            default=0.0,
        )
        arm.set_gripper(gripper_position)

    def _move_ik_prompt() -> None:
        arm.move_ik(_prompt_ik_target())

    # (key, label, action). Keys are intentionally single characters so
    # ``click.getchar()`` can dispatch immediately on the first keystroke
    # — no Enter required, no ambiguity between "1" and "11". Digits are
    # reserved for the named poses; mnemonic letters cover the actions.
    actions: list[tuple[str, str, "Callable[[], None]"]] = [
        ("1", "zero", arm.go_to_zero),
        ("2", "ready", arm.go_to_ready),
        ("3", "pre-grasp", arm.go_to_pregrasp),
        ("4", "scan", arm.go_to_scan),
        ("5", "good bin", arm.go_to_good_bin),
        ("6", "bad bin", arm.go_to_bad_bin),
        ("o", "open gripper", arm.open_gripper),
        ("c", "close gripper", arm.close_gripper),
        ("i", "move ik", _move_ik_prompt),
        ("g", "set gripper position", _set_gripper_prompt),
        ("s", "status", lambda: _print_status(arm)),
    ]
    action_map = {key.lower(): (label, fn) for key, label, fn in actions}

    def _show_menu() -> None:
        table = Table(title="PiperArm Control", title_style="bold cyan")
        table.add_column("Key", style="bold yellow", justify="right")
        table.add_column("Action", style="bold")
        for key, label, _ in actions:
            table.add_row(key, label)
        table.add_row("q", "quit menu (control loop keeps running)")
        console.print(table)
        console.print(
            "[dim]Press a key to dispatch, or [bold]Ctrl-C[/bold] / "
            "[bold]q[/bold] to exit. (Quitting does not stop the control "
            "loop.)[/dim]"
        )

    # Verify the loop is up before showing the menu so the user gets a
    # clear error rather than failing on the first action.
    with _handle_arm_not_running():
        arm.get_heartbeat()

    while True:
        _show_menu()
        try:
            ch = click.getchar()
        except KeyboardInterrupt:
            break
        if ch in ("\x03", "\x04") or ch.lower() == "q":
            break
        entry = action_map.get(ch.lower())
        if entry is None:
            continue
        label, action = entry
        logger.info(f"Dispatching: {label}")
        try:
            with _handle_arm_not_running():
                action()
        except click.exceptions.Exit:
            # _handle_arm_not_running already printed; drop back to the menu
            # rather than terminating the whole control session.
            continue


@arm_commands.command(name="run-print-cycle")
def run_print_cycle():
    """Run the full autonomous print pickup, scan, and sorting cycle."""
    arm = _new_client()

    def get_ik_grasp_joints():
        """Replace this with actual IK target output."""
        return [0.3, 0.1, 0.4]

    def get_bambu_gripper_close_value():
        """Replace this with Bambu Lab print-derived gripping logic."""
        return 5.0

    def get_vlm_decision():
        """Replace this with VLM quality-check result."""
        return click.prompt(
            "VLM decision? Type good or bad",
            type=click.Choice(["good", "bad"], case_sensitive=False),
        )

    with _handle_arm_not_running():
        decision = arm.run_print_cycle(
            get_ik_grasp_joints=get_ik_grasp_joints,
            get_bambu_gripper_close_value=get_bambu_gripper_close_value,
            get_vlm_decision=get_vlm_decision,
        )

    console.print(f"Print cycle complete. Decision: {decision}")


@arm_commands.command(name="calibrate")
@click.option(
    "--can-port",
    type=str,
    default="can0",
    show_default=True,
    help="CAN interface name (e.g. can0).",
)
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
def calibrate(can_port: str, joints: bool, gripper: bool):
    """Calibrate the ARM joints or the gripper.

    Mutually exclusive with ``printq arm start``: this command opens its
    own direct CAN connection and will refuse to run if a control loop
    heartbeat is detected on the system.

    Exactly one of ``--joints`` or ``--gripper`` must be provided.
    """
    if joints and gripper:
        raise click.UsageError(
            "--joints and --gripper are mutually exclusive; specify only one."
        )
    if not joints and not gripper:
        raise click.UsageError("Specify exactly one of --joints or --gripper.")

    from printq.arm.piper import PiperArm

    arm = PiperArm(can_port=can_port)
    try:
        if joints:
            arm.calibrate_joints()
        else:
            arm.calibrate_gripper()
    except RuntimeError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise click.exceptions.Exit(code=1) from exc
