"""Application control commands."""

from logging import DEBUG, getLogger
from pathlib import Path
from time import sleep

import click
from omegaconf import OmegaConf
from rich.panel import Panel

from printq.cli import console
from printq.utils.hydra import load_hydra_config

logger = getLogger(__name__)


@click.group(name="app")
@click.pass_context
def app_commands(ctx):
    """Application control utilities"""
    ctx.ensure_object(dict)


# Delay (seconds) inserted between each step of the pick-and-place cycle so
# the operator can observe motion and the arm has time to settle before the
# next command is published.
_STEP_DELAY_S = 1.0


def _confirm(prompt: str, default: bool = True) -> bool:
    """Wrap ``click.confirm`` so a Ctrl-C at the prompt is bubbled up as
    ``KeyboardInterrupt`` (the rest of the loop already handles that as
    a clean stop request)."""
    try:
        return click.confirm(prompt, default=default)
    except click.exceptions.Abort:
        raise KeyboardInterrupt from None


def _classify_print_quality(camera, qwen) -> tuple[bool, str]:
    """Capture a frame and ask Qwen whether the print looks good or bad.

    Returns ``(is_good, reason)``. ``camera.take_pic()`` reads the
    latest color frame from the publisher and base64-encodes it as JPEG,
    which is exactly what Qwen's vision endpoint expects.
    """
    b64_jpeg = camera.take_pic()
    return qwen.get_print_status(b64_jpeg)


def _show_detection(camera) -> int:
    """Capture a frame, run YOLO, and paint the detection window.

    OpenCV needs a few ``waitKey`` ticks before ``imshow`` actually
    renders, so we pump the event loop briefly. The window stays up
    (with the last painted frame) while we block on the terminal
    confirmation prompt; the caller is responsible for tearing it down.

    Returns the number of bounding boxes drawn.
    """
    import cv2

    num_detections = camera.show_obj()
    for _ in range(15):
        cv2.waitKey(20)
    return num_detections


@app_commands.command(name="start")
def start():
    """Run the interactive pick-and-place application loop until Ctrl-C.

    Each cycle drives the arm to pre-grasp, asks Qwen to classify the
    print as good/bad, runs YOLO object detection for visual confirmation,
    and then prompts the operator to grasp the part. After grasping, the
    arm auto-routes to the good or bad bin based on Qwen's verdict.

    On Ctrl-C (or if the operator declines a cycle) the arm is commanded
    back to its zero pose before exiting.

    Requires both the arm control loop (``printq arm start``) AND the
    camera publisher (``printq camera start-server``) to already be
    running; this command is a client of both.
    """
    import time

    import cv2

    from printq.arm.piper import ArmNotRunningError, PiperArm
    from printq.camera.qwen import Qwen
    from printq.camera.realsense import CameraNotRunningError, RealsenseCamera

    arm = PiperArm()
    camera = RealsenseCamera()
    qwen = Qwen()

    # Fail fast with a clear message if either control loop is down,
    # rather than blowing up partway through the first cycle.
    try:
        arm.get_heartbeat()
    except ArmNotRunningError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise click.exceptions.Exit(code=1) from exc

    try:
        camera.get_heartbeat()
    except CameraNotRunningError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise click.exceptions.Exit(code=1) from exc

    console.print(
        "[bold green]PrintQ application started.[/bold green] "
        "Press [bold]Ctrl-C[/bold] at any time to stop; the arm will return "
        "to the zero pose before exiting."
    )

    cycle = 0
    try:
        while True:
            cycle += 1
            if not _confirm(f"\nRun pick-and-place cycle #{cycle}?", default=True):
                console.print("[yellow]Operator declined; stopping.[/yellow]")
                break

            console.print(
                f"[cyan]Cycle {cycle}:[/cyan] ready position, gripper open"
            )
            arm.go_to_ready()
            arm.open_gripper()
            time.sleep(_STEP_DELAY_S)

            console.print(
                f"[cyan]Cycle {cycle}:[/cyan] pre-grasp, gripper open"
            )
            arm.go_to_pregrasp()
            arm.open_gripper()
            time.sleep(_STEP_DELAY_S)

            console.print(
                f"[cyan]Cycle {cycle}:[/cyan] classifying print quality (Qwen)"
            )
            try:
                is_good, reason = _classify_print_quality(camera, qwen)
            except CameraNotRunningError as exc:
                console.print(f"[bold red]Camera dropped out: {exc}[/bold red]")
                raise click.exceptions.Exit(code=1) from exc

            verdict_color = "green" if is_good else "red"
            verdict_label = "GOOD" if is_good else "BAD"
            console.print(
                f"[bold {verdict_color}]Qwen verdict: {verdict_label}[/bold {verdict_color}] "
                f"-- {reason}"
            )

            console.print(
                f"[cyan]Cycle {cycle}:[/cyan] running object detection"
            )
            try:
                num_detections = _show_detection(camera)
            except CameraNotRunningError as exc:
                console.print(f"[bold red]Camera dropped out: {exc}[/bold red]")
                cv2.destroyAllWindows()
                raise click.exceptions.Exit(code=1) from exc

            if num_detections == 0:
                cv2.destroyAllWindows()
                console.print(
                    "[bold yellow]No objects detected; "
                    "returning to ready and skipping cycle.[/bold yellow]"
                )
                arm.go_to_ready()
                time.sleep(_STEP_DELAY_S)
                continue

            try:
                # grasp = _confirm(
                #     "Move to grasp position and close the gripper?",
                #     default=True,
                # )
                sleep(5)
            finally:
                cv2.destroyAllWindows()

            # if not grasp:
            #     console.print(
            #         "[yellow]Skipping grasp; returning to ready.[/yellow]"
            #     )
            #     arm.go_to_ready()
            #     time.sleep(_STEP_DELAY_S)
            #     continue

            console.print(f"[cyan]Cycle {cycle}:[/cyan] grasp")
            arm.go_to_grasp()
            time.sleep(_STEP_DELAY_S)

            console.print(f"[cyan]Cycle {cycle}:[/cyan] close gripper")
            arm.close_gripper()
            time.sleep(_STEP_DELAY_S)

            console.print(f"[cyan]Cycle {cycle}:[/cyan] back to pre-grasp")
            arm.go_to_pregrasp()
            time.sleep(_STEP_DELAY_S)

            if is_good:
                console.print(
                    f"[cyan]Cycle {cycle}:[/cyan] good bin "
                    f"(Qwen: {reason})"
                )
                arm.go_to_good_bin()
            else:
                console.print(
                    f"[cyan]Cycle {cycle}:[/cyan] bad bin "
                    f"(Qwen: {reason})"
                )
                arm.go_to_bad_bin()
            time.sleep(_STEP_DELAY_S)

            console.print(
                f"[cyan]Cycle {cycle}:[/cyan] open gripper (release)"
            )
            arm.open_gripper()
            time.sleep(_STEP_DELAY_S)

            console.print(f"[cyan]Cycle {cycle}:[/cyan] ready position")
            arm.go_to_ready()
            time.sleep(_STEP_DELAY_S)

            console.print(f"[bold green]Cycle {cycle} complete.[/bold green]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping application...[/yellow]")
    except ArmNotRunningError as exc:
        console.print(f"[bold red]Lost arm control loop: {exc}[/bold red]")
    except CameraNotRunningError as exc:
        console.print(f"[bold red]Lost camera publisher: {exc}[/bold red]")
    finally:
        cv2.destroyAllWindows()
        console.print("[yellow]Returning arm to zero pose...[/yellow]")
        try:
            arm.go_to_zero()
        except ArmNotRunningError as exc:
            console.print(
                f"[red]Could not command zero pose (control loop gone): {exc}[/red]"
            )
        console.print("[bold green]Application stopped.[/bold green]")