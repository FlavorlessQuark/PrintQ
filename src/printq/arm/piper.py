"""Piper ARM control."""

import ctypes
import multiprocessing as mp
import signal
import time
from importlib.resources import as_file, files
from logging import getLogger

import numpy as np
from ikpy.chain import Chain
from ikpy.link import OriginLink, URDFLink
from piper_control import piper_init, piper_interface

logger = getLogger(__name__)


# --- iceoryx2 event-id constants for the e-stop notifier service --------------
#
# Two ids are used so a single event service can carry both edges: anything
# sending ``ESTOP_TRIGGER_EVENT_ID`` latches the controller into ESTOPPED,
# and ``ESTOP_CLEAR_EVENT_ID`` releases it. Subscribers/clients can use
# any other id values for their own signalling without colliding.
ESTOP_TRIGGER_EVENT_ID = 1
ESTOP_CLEAR_EVENT_ID = 2


class ArmNotRunningError(RuntimeError):
    """Raised when a client operation requires the control loop, but it
    is not running (no heartbeat observed within the verification timeout).

    Callers should typically convert this into a user-facing message that
    says something like ``"run 'printq arm start' first"``.
    """


class ControlMode:
    """Enum-like control-loop modes; published in ``HeartbeatPayload.mode``."""

    IDLE = 0
    TRACKING = 1   # Following the latest fresh command, possibly clamped.
    HOLDING = 2    # Command watchdog tripped; holding current pose.
    ESTOPPED = 3   # E-stop latched; commanding current pose, ignoring inputs.
    DISABLED = 4   # Reserved for future use (controller intentionally idle).


class ControlFlags:
    """Bitfield flags reported in ``HeartbeatPayload.flags``.

    Multiple flags can be set per cycle to summarise what the safety layer
    had to do, so subscribers can alarm/UI on degraded modes.
    """

    NONE = 0
    WATCHDOG_TRIPPED = 1 << 0
    ESTOP_ACTIVE = 1 << 1
    OUT_OF_BOUNDS = 1 << 2
    RATE_LIMITED = 1 << 3
    CYCLE_OVERRUN = 1 << 4
    CAN_ERROR = 1 << 5


class JointPositionsPayload(ctypes.Structure):
    """Iceoryx2 payload describing a single joint-positions feedback sample.

    Layout is fixed-size and ABI-stable so that subscribers in other
    processes (or other languages) can rely on it without negotiation.
    """

    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("num_joints", ctypes.c_uint32),
        ("_padding", ctypes.c_uint32),
        ("positions", ctypes.c_double * 6),
    ]

    @staticmethod
    def type_name() -> str:
        """System-wide unique type name used by iceoryx2 for type matching."""
        return "printq::PiperArmJointPositions"


class JointCommandPayload(ctypes.Structure):
    """Iceoryx2 payload carrying a streaming joint-position setpoint.

    Semantics:
        - ``timestamp_ns``: Wall-clock-ish (``time.time_ns()``) stamp at the
          moment of publish. The control loop uses this to (a) reject stale
          samples and (b) trip the freshness watchdog when no recent command
          has arrived.
        - ``priority``: Higher value wins on the same cycle. Equal priority
          falls back to "newest timestamp wins" (last-writer-wins).
        - ``num_joints``: Number of meaningful entries in ``targets`` /
          ``max_velocity``; remaining slots are ignored.
        - ``targets``: Desired joint angles in radians.
        - ``max_velocity``: Per-joint speed cap in rad/s. ``0`` for a joint
          means "use the controller's default cap".
    """

    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("priority", ctypes.c_uint32),
        ("num_joints", ctypes.c_uint32),
        ("targets", ctypes.c_double * 6),
        ("max_velocity", ctypes.c_double * 6),
    ]

    @staticmethod
    def type_name() -> str:
        return "printq::PiperArmJointCommand"


class HeartbeatPayload(ctypes.Structure):
    """Iceoryx2 payload published every cycle so subscribers can detect a
    dead or degraded controller and react accordingly."""

    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("cycle", ctypes.c_uint64),
        ("mode", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]

    @staticmethod
    def type_name() -> str:
        return "printq::PiperArmHeartbeat"


class GripperCommandPayload(ctypes.Structure):
    """Iceoryx2 payload carrying a streaming gripper setpoint.

    Same priority / last-writer-wins semantics as ``JointCommandPayload``.
    Position units follow the wrapper convention (``0..GRIPPER_OPEN_POSITION``
    in :class:`PiperArm` units, 0 = closed). Effort uses wrapper units
    where ``1.0`` is the SDK demo's default torque.
    """

    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("priority", ctypes.c_uint32),
        ("_padding", ctypes.c_uint32),
        ("position", ctypes.c_double),
        ("effort", ctypes.c_double),
    ]

    @staticmethod
    def type_name() -> str:
        return "printq::PiperArmGripperCommand"


class GripperStatePayload(ctypes.Structure):
    """Iceoryx2 payload published every cycle with gripper state.

    ``angle`` is in wrapper units (``0..GRIPPER_OPEN_POSITION``) so it
    matches the scale used for commands. ``effort`` is in N·m (passed
    through unchanged from the SDK).
    """

    _fields_ = [
        ("timestamp_ns", ctypes.c_uint64),
        ("angle", ctypes.c_double),
        ("effort", ctypes.c_double),
    ]

    @staticmethod
    def type_name() -> str:
        return "printq::PiperArmGripperState"


def _clamp_and_rate_limit(
    targets: tuple[float, ...],
    current: tuple[float, ...],
    joint_min: tuple[float, ...],
    joint_max: tuple[float, ...],
    max_joint_velocity: tuple[float, ...],
    dt: float,
) -> tuple[tuple[float, ...], int]:
    """Apply joint-limit clamp and per-cycle velocity limit.

    For each joint independently:
        1. Clamp ``target`` to ``[joint_min, joint_max]``.
        2. Clamp ``target - current`` to ``±(max_joint_velocity * dt)`` so
           the commanded delta never asks the arm to move faster than the
           configured per-joint speed cap.

    Returns:
        ``(commanded, flags)`` where ``flags`` is a bitwise OR of
        ``ControlFlags`` values describing which safety actions fired.
    """
    flags = ControlFlags.NONE
    commanded: list[float] = []

    bound_tol = 1e-6
    for i in range(6):
        target = targets[i]

        lo = joint_min[i]
        hi = joint_max[i]
        if target < lo - bound_tol or target > hi + bound_tol:
            flags |= ControlFlags.OUT_OF_BOUNDS
        target = max(lo, min(hi, target))

        vmax = max_joint_velocity[i]
        if vmax > 0.0:
            max_delta = vmax * dt
            delta = target - current[i]
            if delta > max_delta:
                delta = max_delta
                flags |= ControlFlags.RATE_LIMITED
            elif delta < -max_delta:
                delta = -max_delta
                flags |= ControlFlags.RATE_LIMITED
            commanded.append(current[i] + delta)
        else:
            commanded.append(target)

    return tuple(commanded), flags


def _run_control_loop(
    can_port: str,
    control_frequency_hz: float,
    feedback_service_name: str,
    command_service_name: str,
    heartbeat_service_name: str,
    estop_service_name: str,
    gripper_command_service_name: str,
    gripper_state_service_name: str,
    watchdog_timeout_s: float,
    default_max_joint_velocity: tuple[float, ...],
    heartbeat_decimation: int,
    shutdown_settle_s: float,
) -> None:
    """Child-process entry point: subscribe + control + publish on one CAN owner.

    This function is the *only* process that touches the Piper CAN bus while
    ``PiperArm.start()`` is active. On startup it resets the arm and the
    gripper; on shutdown it commands the arm to zero, settles, then powers
    down the motors. Each cycle it:

      1. Drains any iceoryx2 e-stop event notifications.
      2. Drains the joint-command subscriber and keeps the freshest sample,
         using ``(priority, timestamp_ns)`` as the tie-breaker.
      3. Drains the gripper-command subscriber the same way.
      4. Reads current joint positions and gripper state from the arm.
      5. Picks a ``ControlMode`` (ESTOPPED > HOLDING > TRACKING) and computes
         the actual commanded pose, applying joint-limit clamp + per-cycle
         velocity rate limit.
      6. Sends the joint pose to the arm via CAN, and re-sends the latest
         gripper command if the setpoint changed (unless e-stopped).
      7. Publishes joint feedback and gripper state every cycle, and (every
         ``heartbeat_decimation`` cycles) a heartbeat sample.
      8. Sleeps the rest of the period using a drift-free monotonic deadline.

    The loop exits on SIGTERM / SIGINT (set up here) or on a fatal iceoryx2
    error from the wait primitives.
    """
    import logging as _logging

    import iceoryx2 as iox2  # local: avoids being imported at parent-import time

    # The "spawn" multiprocessing start method gives the child a fresh
    # interpreter with no logging handlers, so its info-level logs would
    # otherwise be silently dropped. Wire up a basic stderr handler so
    # the user sees the child's diagnostics in the same terminal as the
    # parent ``printq arm start`` command.
    if not _logging.getLogger().handlers:
        _logging.basicConfig(
            level=_logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    child_logger = getLogger(f"{__name__}.control_loop")
    child_logger.info(
        "Control loop starting (freq=%.2f Hz, watchdog=%.3fs, can=%s)",
        control_frequency_hz,
        watchdog_timeout_s,
        can_port,
    )

    stop_requested = False

    def _request_stop(signum, _frame):
        nonlocal stop_requested
        child_logger.info("Control loop received signal %d; stopping.", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    # The child is the sole CAN owner: open the interface and reset the arm
    # + gripper so subsequent commands have a well-defined starting point.
    piper = piper_interface.PiperInterface(can_port=can_port)
    child_logger.info("Resetting ARM")
    piper_init.reset_arm(
        piper,
        arm_controller=piper_interface.ArmController.POSITION_VELOCITY,
        move_mode=piper_interface.MoveMode.JOINT,
    )
    child_logger.info("Resetting GRIPPER")
    piper_init.reset_gripper(piper)

    joint_limits = piper.joint_limits
    joint_min = tuple(joint_limits["min"][:6])
    joint_max = tuple(joint_limits["max"][:6])

    # The Piper SDK's ``command_gripper(position=...)`` expects meters
    # (clipped to ``[0, gripper_angle_max]``, typically 0.07 m), and
    # ``get_gripper_state()`` reports the angle in meters as well. We
    # expose a friendlier 0..``GRIPPER_OPEN_POSITION`` scale to clients
    # (see :class:`PiperArm`), so the loop owns the unit conversion at
    # the SDK boundary in both directions.
    gripper_angle_max = float(piper.gripper_angle_max)
    gripper_position_scale = gripper_angle_max / PiperArm.GRIPPER_OPEN_POSITION
    child_logger.info(
        "Gripper position scale: 1 wrapper unit = %.6f m (max=%.6f m at wrapper=%.1f).",
        gripper_position_scale,
        gripper_angle_max,
        PiperArm.GRIPPER_OPEN_POSITION,
    )

    node = iox2.NodeBuilder.new().create(iox2.ServiceType.Ipc)

    def _pubsub(name, payload_type, *, buffer_size=None):
        # NOTE: we intentionally do NOT request a service ``history_size``.
        # Iceoryx2 services persist in shared memory across runs, and
        # ``open_or_create`` requires the requested history (and buffer
        # size) to be compatible with whatever stale service config exists
        # on the system. Streaming services don't need history anyway —
        # the loop publishes every cycle and subscribers poll — so we
        # leave it at the default (0) which is always compatible.
        builder = (
            node.service_builder(iox2.ServiceName.new(name))
            .publish_subscribe(payload_type)
        )
        if buffer_size is not None:
            builder = builder.subscriber_max_buffer_size(buffer_size)
        return builder.open_or_create()

    feedback_pub = _pubsub(
        feedback_service_name, JointPositionsPayload,
    ).publisher_builder().create()
    heartbeat_pub = _pubsub(
        heartbeat_service_name, HeartbeatPayload,
    ).publisher_builder().create()
    gripper_state_pub = _pubsub(
        gripper_state_service_name, GripperStatePayload,
    ).publisher_builder().create()

    # buffer_size(1) keeps only the latest unread setpoint queued for the
    # loop, so older samples don't pile up; we still drain on every cycle
    # but this bounds memory if the loop ever stalls briefly.
    command_sub = _pubsub(
        command_service_name,
        JointCommandPayload,
        buffer_size=1,
    ).subscriber_builder().create()
    gripper_command_sub = _pubsub(
        gripper_command_service_name,
        GripperCommandPayload,
        buffer_size=1,
    ).subscriber_builder().create()

    estop_service = (
        node.service_builder(iox2.ServiceName.new(estop_service_name))
        .event()
        .open_or_create()
    )
    estop_listener = estop_service.listener_builder().create()

    period = 1.0 / control_frequency_hz
    # Watchdog: fall back to HOLDING if no fresh command in this many ns.
    # Default floor of 3 cycles keeps us from tripping on routine jitter.
    watchdog_ns = int(max(watchdog_timeout_s, 3 * period) * 1e9)

    initial = tuple(float(p) for p in piper.get_joint_positions()[:6])
    while len(initial) < 6:
        initial = initial + (0.0,)
    current: tuple[float, ...] = initial
    last_cmd_targets: tuple[float, ...] = initial
    last_cmd_velocity_limit: tuple[float, ...] = default_max_joint_velocity
    last_cmd_ts = 0
    last_cmd_priority = 0
    last_gripper_cmd: tuple[float, float] | None = None
    last_gripper_sent: tuple[float, float] | None = None
    last_gripper_ts = 0
    last_gripper_priority = 0
    estop_active = False
    cycle = 0

    try:
        while not stop_requested:
            cycle += 1
            cycle_start = time.monotonic()

            # 1. Drain e-stop events. Multiple notifications per cycle are
            #    coalesced; the last one observed wins, matching the
            #    "latest signal" intent of an e-stop button.
            try:
                while True:
                    eid = estop_listener.try_wait_one()
                    if eid is None:
                        break
                    value = eid.as_value
                    if value == ESTOP_CLEAR_EVENT_ID:
                        if estop_active:
                            child_logger.warning(
                                "E-stop CLEARED via event id=%d (cycle=%d).",
                                value, cycle,
                            )
                        estop_active = False
                    else:
                        if not estop_active:
                            child_logger.error(
                                "E-STOP TRIGGERED via event id=%d (cycle=%d).",
                                value, cycle,
                            )
                        estop_active = True
            except iox2.ListenerWaitError as exc:
                child_logger.exception("E-stop listener error: %s", exc)

            # 2. Drain joint-command subscriber. With buffer_size=1 there's
            #    at most one queued sample, but loop defensively for
            #    resilience.
            best_ts = last_cmd_ts
            best_priority = last_cmd_priority
            best_targets: tuple[float, ...] | None = None
            best_vlimit: tuple[float, ...] | None = None
            try:
                while True:
                    sample = command_sub.receive()
                    if sample is None:
                        break
                    p = sample.payload().contents
                    if (
                        p.priority > best_priority
                        or (p.priority == best_priority and p.timestamp_ns > best_ts)
                    ):
                        best_ts = int(p.timestamp_ns)
                        best_priority = int(p.priority)
                        n = min(int(p.num_joints), 6)
                        targets_out = [float(p.targets[i]) for i in range(6)]
                        vlimit_out = [
                            float(p.max_velocity[i]) if i < n else 0.0
                            for i in range(6)
                        ]
                        # Untouched joints (when num_joints < 6) hold their
                        # last commanded target instead of jumping to zero.
                        for i in range(n, 6):
                            targets_out[i] = last_cmd_targets[i]
                        best_targets = tuple(targets_out)
                        best_vlimit = tuple(vlimit_out)
            except iox2.ReceiveError as exc:
                child_logger.exception("Command subscriber error: %s", exc)

            if best_targets is not None:
                last_cmd_ts = best_ts
                last_cmd_priority = best_priority
                last_cmd_targets = best_targets
                # Per-joint override: 0 means "use the default cap".
                last_cmd_velocity_limit = tuple(
                    best_vlimit[i] if (best_vlimit is not None and best_vlimit[i] > 0.0)
                    else default_max_joint_velocity[i]
                    for i in range(6)
                )
                # Debug-level: with stream_command publishing at 20 Hz, an
                # info-level log per fresh sample would flood the terminal.
                child_logger.debug(
                    "Received joint command (cycle=%d, priority=%d, age_ms=%.1f): %s",
                    cycle,
                    last_cmd_priority,
                    (time.time_ns() - last_cmd_ts) / 1e6,
                    ", ".join(f"{v:+.3f}" for v in last_cmd_targets),
                )

            # 3. Drain gripper-command subscriber. Same (priority, ts) policy.
            best_g_ts = last_gripper_ts
            best_g_priority = last_gripper_priority
            best_gripper: tuple[float, float] | None = None
            try:
                while True:
                    sample = gripper_command_sub.receive()
                    if sample is None:
                        break
                    g = sample.payload().contents
                    if (
                        g.priority > best_g_priority
                        or (g.priority == best_g_priority and g.timestamp_ns > best_g_ts)
                    ):
                        best_g_ts = int(g.timestamp_ns)
                        best_g_priority = int(g.priority)
                        best_gripper = (float(g.position), float(g.effort))
            except iox2.ReceiveError as exc:
                child_logger.exception("Gripper command subscriber error: %s", exc)

            if best_gripper is not None:
                last_gripper_cmd = best_gripper
                last_gripper_ts = best_g_ts
                last_gripper_priority = best_g_priority
                # Info-level is fine here: gripper commands are typically
                # edge events (open/close/set-once), not a 20 Hz stream.
                child_logger.info(
                    "Received gripper command (cycle=%d, priority=%d, age_ms=%.1f): "
                    "position=%.3f, effort=%.3f",
                    cycle,
                    last_gripper_priority,
                    (time.time_ns() - last_gripper_ts) / 1e6,
                    last_gripper_cmd[0],
                    last_gripper_cmd[1],
                )

            # 4. Read state.
            try:
                raw_positions = piper.get_joint_positions()
                current = tuple(float(raw_positions[i]) for i in range(6))
            except Exception as exc:  # noqa: BLE001
                child_logger.exception("Failed to read joint positions: %s", exc)
            try:
                raw_angle_m, gripper_effort = piper.get_gripper_state()
            except Exception as exc:  # noqa: BLE001
                child_logger.exception("Failed to read gripper state: %s", exc)
                raw_angle_m, gripper_effort = 0.0, 0.0
            # Convert SDK meters → wrapper units (0..GRIPPER_OPEN_POSITION)
            # so subscribers see the same scale used for commands.
            gripper_angle = (
                raw_angle_m / gripper_position_scale
                if gripper_position_scale > 0.0
                else 0.0
            )

            # 5. Mode selection.
            now_ns = time.time_ns()
            cmd_age_ns = (now_ns - last_cmd_ts) if last_cmd_ts > 0 else (1 << 63)
            flags = ControlFlags.NONE

            if estop_active:
                mode = ControlMode.ESTOPPED
                flags |= ControlFlags.ESTOP_ACTIVE
                commanded = current
            elif cmd_age_ns > watchdog_ns:
                mode = ControlMode.HOLDING
                flags |= ControlFlags.WATCHDOG_TRIPPED
                commanded = current
            else:
                commanded, applied = _clamp_and_rate_limit(
                    targets=last_cmd_targets,
                    current=current,
                    joint_min=joint_min,
                    joint_max=joint_max,
                    max_joint_velocity=last_cmd_velocity_limit,
                    dt=period,
                )
                flags |= applied
                mode = ControlMode.TRACKING

            # 6. Send to arm. We always send (even in HOLDING/ESTOPPED) so
            #    the firmware-side watchdog stays happy and the arm
            #    actively holds rather than going limp.
            try:
                piper.command_joint_positions(positions=commanded)
            except Exception as exc:  # noqa: BLE001
                flags |= ControlFlags.CAN_ERROR
                child_logger.exception("CAN command failed: %s", exc)

            # Gripper: only re-send when the setpoint changes (or right
            # after an e-stop release), so we don't pointlessly hammer the
            # CAN bus with the same gripper frame at the control rate.
            if (
                not estop_active
                and last_gripper_cmd is not None
                and last_gripper_cmd != last_gripper_sent
            ):
                wrapper_pos, wrapper_effort = last_gripper_cmd
                sdk_pos_m = wrapper_pos * gripper_position_scale
                try:
                    piper.command_gripper(
                        position=sdk_pos_m,
                        effort=wrapper_effort,
                    )
                    last_gripper_sent = last_gripper_cmd
                    child_logger.debug(
                        "Gripper SDK command: wrapper=%.3f → %.6f m, effort=%.3f.",
                        wrapper_pos, sdk_pos_m, wrapper_effort,
                    )
                except Exception as exc:  # noqa: BLE001
                    flags |= ControlFlags.CAN_ERROR
                    child_logger.exception("Gripper CAN command failed: %s", exc)

            # 7a. Joint feedback every cycle.
            fb = JointPositionsPayload()
            fb.timestamp_ns = now_ns
            fb.num_joints = 6
            for i in range(6):
                fb.positions[i] = current[i]
            fb_sample = feedback_pub.loan_uninit()
            fb_sample = fb_sample.write_payload(fb)
            fb_sample.send()

            # 7b. Gripper state every cycle.
            gs = GripperStatePayload()
            gs.timestamp_ns = now_ns
            gs.angle = gripper_angle
            gs.effort = gripper_effort
            gs_sample = gripper_state_pub.loan_uninit()
            gs_sample = gs_sample.write_payload(gs)
            gs_sample.send()

            # 7c. Heartbeat (decimated but still timely).
            if heartbeat_decimation > 0 and cycle % heartbeat_decimation == 0:
                hb = HeartbeatPayload()
                hb.timestamp_ns = now_ns
                hb.cycle = cycle
                hb.mode = mode
                hb.flags = flags
                hb_sample = heartbeat_pub.loan_uninit()
                hb_sample = hb_sample.write_payload(hb)
                hb_sample.send()

            # 8. Drift-free sleep against ``cycle_start`` (not now()) so
            #    cumulative jitter does not drag the loop slow over time.
            elapsed = time.monotonic() - cycle_start
            slack = period - elapsed
            if slack > 0:
                time.sleep(slack)
            else:
                child_logger.warning(
                    "Cycle %d overran by %.2f ms (period=%.2f ms).",
                    cycle, -slack * 1000.0, period * 1000.0,
                )
    finally:
        child_logger.info(
            "Control loop exiting (cycle=%d); commanding zero + disabling motors.",
            cycle,
        )
        # Best-effort shutdown: command zero, give the arm a moment to
        # settle, then disable the gripper and arm. Each step is wrapped
        # so a CAN failure in one step does not prevent the others.
        try:
            piper.command_joint_positions(positions=(0.0,) * 6)
        except Exception:  # noqa: BLE001
            child_logger.exception("Shutdown: failed to command zero.")
        time.sleep(shutdown_settle_s)
        try:
            piper_init.disable_gripper(piper)
        except Exception:  # noqa: BLE001
            child_logger.exception("Shutdown: failed to disable gripper.")
        try:
            piper_init.disable_arm(piper)
        except Exception:  # noqa: BLE001
            child_logger.exception("Shutdown: failed to disable arm.")
        child_logger.info("Control loop fully shut down.")


class PiperArm:
    """Piper ARM control."""

    JOINT_POSITIONS_ZERO = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    # JOINT_POSITIONS_READY = (0.04, 0.45, -1.5, 0.0, 1.0, 0.0)
    JOINT_POSITIONS_READY = (0.0039, -0.0030, -0.4229, 0.0383, 0.5101, 0.0145)
    # JOINT_POSITIONS_PREGRASP = (0.02, 1.87, -0.53, 0.04, -1.24, 0.08) # original
    # JOINT_POSITIONS_PREGRASP = (-0.0418, 1.7753, -1.3291, -0.0102, -0.1861, 0.0228) # last night
    # JOINT_POSITIONS_PREGRASP = (-0.1283, 1.6746, -0.7168, -0.0809, -0.8140, 0.0397) # today morning 
    JOINT_POSITIONS_PREGRASP = (-0.0669, 1.3180, -1.1601, -0.0626, 0.3447, 0.1427) # today morning for obj detection 
    JOINT_POSITIONS_GRASP = (-0.0705, 2.6633, -2.1799, -0.1648, -0.1409, 0.1268)
    JOINT_POSITIONS_SCAN = (1.23, -0.01, -0.52, -0.01, 0.58, 0.01)
    # JOINT_POSITIONS_GOOD_BIN = (-0.46, 1.84, -0.72, 0.02, -0.69, 0.02)
    JOINT_POSITIONS_GOOD_BIN = (-0.6574, 1.5, -0.4441, 0.1783, -0.7182, 0.0219)
    JOINT_POSITIONS_BAD_BIN = (0.63, 1.84, -0.73, 0.02, -0.69, 0.02)
    # Gripper position bounds. Position is in meters (V2) or radians (V1);
    # effort is in wrapper units where 1.0 corresponds to the SDK demo's
    # default torque of 1000. The ``go_to_*`` preset moves deliberately
    # leave the gripper alone — it changes only on an explicit gripper
    # command (e.g. ``open_gripper`` / ``close_gripper`` / ``set_gripper``).
    GRIPPER_OPEN_POSITION = 10.0
    GRIPPER_CLOSED_POSITION = 0.0
    GRIPPER_DEFAULT_EFFORT = 1.0

    # Iceoryx2 service names. These are the public contract subscribers and
    # other clients should match against.
    JOINT_POSITIONS_SERVICE_NAME = "printq/arm/joint_positions"   # feedback (pub-sub)
    JOINT_COMMAND_SERVICE_NAME = "printq/arm/joint_command"       # setpoints (pub-sub)
    GRIPPER_STATE_SERVICE_NAME = "printq/arm/gripper_state"       # feedback (pub-sub)
    GRIPPER_COMMAND_SERVICE_NAME = "printq/arm/gripper_command"   # setpoints (pub-sub)
    HEARTBEAT_SERVICE_NAME = "printq/arm/heartbeat"               # alive + mode (pub-sub)
    ESTOP_SERVICE_NAME = "printq/arm/estop"                       # latched signal (event)

    # Control-loop defaults.
    DEFAULT_CONTROL_FREQUENCY_HZ = 50.0
    DEFAULT_WATCHDOG_TIMEOUT_S = 0.1
    # Per-joint speed cap (rad/s) used when a command does not specify
    # its own ``max_velocity`` for a joint. This is the *loop's*
    # rate-limit, applied on top of the firmware's own velocity profile.
    #
    # Picking this too low double-throttles the arm: the COMMANDED target
    # ramps slowly, and the physical arm lags behind that ramp, so a
    # ``go_to_*`` call's ``settle_time`` can elapse before the arm
    # reaches the goal — at which point the watchdog drops the loop into
    # HOLDING and the arm freezes mid-trajectory. Empirically the Piper
    # firmware itself slews around 2-3 rad/s, so we set the loop's cap
    # well above that so it acts as a safety/jerk limiter for jumps but
    # not as the dominant speed control during a normal pose move.
    DEFAULT_MAX_JOINT_VELOCITY = (3.0, 3.0, 3.0, 3.5, 3.5, 3.5)
    # Heartbeat every N control cycles (e.g. 5 @ 50 Hz = 10 Hz).
    DEFAULT_HEARTBEAT_DECIMATION = 5
    # On shutdown the control loop commands the arm to zero, sleeps this
    # many seconds to let it settle, then disables motors.
    DEFAULT_SHUTDOWN_SETTLE_S = 1.5
    # How long client calls wait for a heartbeat before raising
    # ArmNotRunningError. Short enough to fail fast on missing loops,
    # long enough to tolerate one cycle of jitter at 10 Hz heartbeat.
    DEFAULT_VERIFY_TIMEOUT_S = 1.0
    # How long client read methods (get_joint_positions, get_gripper_state)
    # wait for a fresh sample.
    DEFAULT_READ_TIMEOUT_S = 1.0
    # Rate (Hz) at which :meth:`stream_command` republishes the same target
    # so the control loop's freshness watchdog never trips while a "move to
    # pose" call is in progress. Must be comfortably faster than
    # ``1 / DEFAULT_WATCHDOG_TIMEOUT_S``; 20 Hz @ 100 ms watchdog gives 2x
    # margin which tolerates a missed publish without falling into HOLDING.
    DEFAULT_STREAM_RATE_HZ = 20.0
    # Per-joint absolute error (radians) below which :meth:`stream_command`
    # considers a "go to pose" call settled and returns early. ~0.02 rad
    # ~= 1.15° which is well inside any practical pick-and-place tolerance
    # but loose enough to absorb the firmware's natural steady-state ripple.
    DEFAULT_SETTLE_TOLERANCE_RAD = 0.02

    def __init__(self, can_port: str = "can0"):
        """Construct a PiperArm handle.

        IMPORTANT: This constructor does NOT touch the CAN bus, reset the
        arm, or create an iceoryx2 node. It only stores configuration.
        The actual control loop (and thus CAN access) is owned by the
        child process spawned by :meth:`start`.

        Two usage patterns are supported:

          1. *Server* (one per system): create a ``PiperArm`` and call
             ``start()``. The spawned control loop is now the only CAN
             owner. Call ``stop()`` to bring it down cleanly. This is
             what ``printq arm start`` does.

          2. *Client*: create a ``PiperArm`` and call any of the
             ``go_to_*`` / ``set_gripper`` / ``send_*`` / ``get_*``
             methods. These all publish via iceoryx2 and require a
             running control loop somewhere on the system; if none is
             detected within ``DEFAULT_VERIFY_TIMEOUT_S`` they raise
             :class:`ArmNotRunningError`.

        Calibration methods (:meth:`calibrate_joints`,
        :meth:`calibrate_gripper`) open their own short-lived CAN
        connection and require that the control loop NOT be running.
        """
        self.can_port = can_port
        self._publisher_process: mp.Process | None = None

        # Lazily-created iceoryx2 handles. None of these are touched
        # until the first IPC call, so importing or instantiating
        # PiperArm stays cheap.
        self._iox_node = None
        self._command_publisher = None
        self._gripper_command_publisher = None
        self._estop_notifier = None
        self._heartbeat_subscriber = None
        self._feedback_subscriber = None
        self._gripper_state_subscriber = None

        # IK chain is loaded on first move_ik() so the URDF + ikpy import
        # cost isn't paid by clients that only do joint-space moves.
        self.chain = None

        # Tunables mirroring class defaults so callers can tweak them
        # after init without subclassing.
        self.control_frequency_hz = self.DEFAULT_CONTROL_FREQUENCY_HZ
        self.watchdog_timeout_s = self.DEFAULT_WATCHDOG_TIMEOUT_S
        self.max_joint_velocity = self.DEFAULT_MAX_JOINT_VELOCITY
        self.heartbeat_decimation = self.DEFAULT_HEARTBEAT_DECIMATION
        self.shutdown_settle_s = self.DEFAULT_SHUTDOWN_SETTLE_S

    def start(
        self,
        control_frequency_hz: float | None = None,
        watchdog_timeout_s: float | None = None,
        max_joint_velocity: tuple[float, ...] | None = None,
        heartbeat_decimation: int | None = None,
    ) -> None:
        """Start the iceoryx2 control loop in a child process.

        Spawns a fresh Python interpreter (``"spawn"`` start method, so the
        child does not inherit any FDs from the parent) that:

          1. Opens its own ``PiperInterface`` against ``can_port``.
          2. Resets the arm + gripper.
          3. Becomes the sole CAN owner for the lifetime of the loop.
          4. Subscribes to the joint/gripper command services + e-stop
             event service.
          5. Publishes joint feedback, gripper state, and heartbeats.
          6. On shutdown (SIGTERM/SIGINT) commands the arm back to zero,
             settles, and disables motors.

        After this call, only the spawned process talks to CAN — every
        other method on :class:`PiperArm` is a thin iceoryx2 client.

        Args:
            control_frequency_hz: Loop rate in Hz.
            watchdog_timeout_s: If no fresh command arrives within this
                many seconds, the loop drops to ``HOLDING`` mode.
                Floored to ``3 / control_frequency_hz`` inside the loop.
            max_joint_velocity: Per-joint speed cap (rad/s) used when a
                command does not override it.
            heartbeat_decimation: Publish a heartbeat every N cycles.

        Idempotent: a no-op (with a warning) if the loop is already
        running on *this* instance.
        """
        if self._publisher_process is not None and self._publisher_process.is_alive():
            logger.warning(
                "PiperArm control loop already running (pid=%d); ignoring start().",
                self._publisher_process.pid,
            )
            return

        if control_frequency_hz is not None:
            self.control_frequency_hz = control_frequency_hz
        if watchdog_timeout_s is not None:
            self.watchdog_timeout_s = watchdog_timeout_s
        if max_joint_velocity is not None:
            if len(max_joint_velocity) != 6:
                raise ValueError(
                    f"max_joint_velocity must have 6 entries, got {len(max_joint_velocity)}"
                )
            self.max_joint_velocity = tuple(float(v) for v in max_joint_velocity)
        if heartbeat_decimation is not None:
            self.heartbeat_decimation = int(heartbeat_decimation)

        ctx = mp.get_context("spawn")
        process = ctx.Process(
            target=_run_control_loop,
            args=(
                self.can_port,
                self.control_frequency_hz,
                self.JOINT_POSITIONS_SERVICE_NAME,
                self.JOINT_COMMAND_SERVICE_NAME,
                self.HEARTBEAT_SERVICE_NAME,
                self.ESTOP_SERVICE_NAME,
                self.GRIPPER_COMMAND_SERVICE_NAME,
                self.GRIPPER_STATE_SERVICE_NAME,
                self.watchdog_timeout_s,
                self.max_joint_velocity,
                self.heartbeat_decimation,
                self.shutdown_settle_s,
            ),
            name="PiperArmControlLoop",
            daemon=True,
        )
        process.start()
        self._publisher_process = process
        logger.info(
            "Started PiperArm control loop (pid=%d, freq=%.2f Hz, watchdog=%.3fs)",
            process.pid,
            self.control_frequency_hz,
            self.watchdog_timeout_s,
        )

    def stop(self, timeout: float | None = None) -> None:
        """Stop the control-loop child process and tear down parent IPC.

        Sends ``SIGTERM`` and joins for up to ``timeout`` seconds before
        escalating to ``SIGKILL``. The control loop's ``finally`` block
        commands the arm back to zero, settles, and disables motors
        before exiting, so a clean ``stop()`` leaves the arm safely
        powered down.

        ``timeout`` defaults to ``shutdown_settle_s + 5`` seconds so the
        loop has time to do its shutdown sequence.

        Also releases any lazily-created parent-side iceoryx2 handles.
        Safe to call when nothing was started.
        """
        if timeout is None:
            timeout = self.shutdown_settle_s + 5.0

        process = self._publisher_process
        if process is not None and process.is_alive():
            logger.info("Stopping PiperArm control loop (pid=%d).", process.pid)
            process.terminate()
            process.join(timeout=timeout)
            if process.is_alive():
                logger.warning(
                    "Control loop (pid=%d) did not exit after %.1fs; killing.",
                    process.pid,
                    timeout,
                )
                process.kill()
                process.join(timeout=1.0)
        self._publisher_process = None

        # Drop parent-side iceoryx2 handles so we do not leak ports if the
        # caller later re-uses the same PiperArm instance.
        self._command_publisher = None
        self._gripper_command_publisher = None
        self._estop_notifier = None
        self._heartbeat_subscriber = None
        self._feedback_subscriber = None
        self._gripper_state_subscriber = None
        self._iox_node = None

    def is_running(self) -> bool:
        """Return True iff this instance owns a live control-loop child."""
        return (
            self._publisher_process is not None
            and self._publisher_process.is_alive()
        )

    # ------------------------------------------------------------------ IPC
    #
    # All client-facing helpers below route through iceoryx2 services and
    # require an active control loop somewhere on the system. They never
    # touch CAN directly. ``_require_running`` is the gate that turns
    # "no heartbeat" into a clear ArmNotRunningError.

    def _ensure_iox_node(self):
        """Lazily create the parent-side iceoryx2 node."""
        import iceoryx2 as iox2

        if self._iox_node is None:
            self._iox_node = iox2.NodeBuilder.new().create(iox2.ServiceType.Ipc)
        return self._iox_node

    def _ensure_command_publisher(self):
        if self._command_publisher is None:
            import iceoryx2 as iox2

            # Service config must match what the control loop sets
            # (``buffer_size=1``, no ``history_size``). See the loop's
            # ``_pubsub`` comment for why we avoid history here.
            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.JOINT_COMMAND_SERVICE_NAME))
                .publish_subscribe(JointCommandPayload)
                .subscriber_max_buffer_size(1)
                .open_or_create()
            )
            self._command_publisher = service.publisher_builder().create()
        return self._command_publisher

    def _ensure_gripper_command_publisher(self):
        if self._gripper_command_publisher is None:
            import iceoryx2 as iox2

            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.GRIPPER_COMMAND_SERVICE_NAME))
                .publish_subscribe(GripperCommandPayload)
                .subscriber_max_buffer_size(1)
                .open_or_create()
            )
            self._gripper_command_publisher = service.publisher_builder().create()
        return self._gripper_command_publisher

    def _ensure_estop_notifier(self):
        if self._estop_notifier is None:
            import iceoryx2 as iox2

            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.ESTOP_SERVICE_NAME))
                .event()
                .open_or_create()
            )
            self._estop_notifier = service.notifier_builder().create()
        return self._estop_notifier

    def _ensure_heartbeat_subscriber(self):
        if self._heartbeat_subscriber is None:
            import iceoryx2 as iox2

            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.HEARTBEAT_SERVICE_NAME))
                .publish_subscribe(HeartbeatPayload)
                .open_or_create()
            )
            self._heartbeat_subscriber = service.subscriber_builder().create()
        return self._heartbeat_subscriber

    def _ensure_feedback_subscriber(self):
        if self._feedback_subscriber is None:
            import iceoryx2 as iox2

            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.JOINT_POSITIONS_SERVICE_NAME))
                .publish_subscribe(JointPositionsPayload)
                .open_or_create()
            )
            self._feedback_subscriber = service.subscriber_builder().create()
        return self._feedback_subscriber

    def _ensure_gripper_state_subscriber(self):
        if self._gripper_state_subscriber is None:
            import iceoryx2 as iox2

            node = self._ensure_iox_node()
            service = (
                node.service_builder(iox2.ServiceName.new(self.GRIPPER_STATE_SERVICE_NAME))
                .publish_subscribe(GripperStatePayload)
                .open_or_create()
            )
            self._gripper_state_subscriber = service.subscriber_builder().create()
        return self._gripper_state_subscriber

    def _require_running(self, timeout: float | None = None) -> HeartbeatPayload:
        """Verify that a control loop is alive on the system, or raise.

        Subscribes to the heartbeat service (lazily) and waits up to
        ``timeout`` seconds for a sample. Returns the latest heartbeat
        on success; raises :class:`ArmNotRunningError` on timeout.

        Cheap to call repeatedly: the subscriber is cached, and at the
        default 10 Hz heartbeat rate a sample typically arrives within a
        few ms.
        """
        if timeout is None:
            timeout = self.DEFAULT_VERIFY_TIMEOUT_S

        sub = self._ensure_heartbeat_subscriber()
        deadline = time.monotonic() + timeout
        latest: HeartbeatPayload | None = None
        # Drain any backlog first so we report the most recent state.
        while True:
            sample = sub.receive()
            if sample is None:
                break
            p = sample.payload().contents
            latest = HeartbeatPayload()
            ctypes.memmove(ctypes.byref(latest), ctypes.byref(p), ctypes.sizeof(HeartbeatPayload))
        if latest is not None:
            return latest

        # No backlog: poll until one arrives or we time out.
        while time.monotonic() < deadline:
            time.sleep(0.02)
            sample = sub.receive()
            if sample is not None:
                p = sample.payload().contents
                latest = HeartbeatPayload()
                ctypes.memmove(
                    ctypes.byref(latest), ctypes.byref(p), ctypes.sizeof(HeartbeatPayload),
                )
                return latest

        raise ArmNotRunningError(
            f"PiperArm control loop is not running "
            f"(no heartbeat on '{self.HEARTBEAT_SERVICE_NAME}' within {timeout:.1f}s). "
            f"Run 'printq arm start' first."
        )

    def _is_loop_running(self, timeout: float = 0.3) -> bool:
        """Best-effort check used by methods that must run when the loop is NOT up."""
        try:
            self._require_running(timeout=timeout)
            return True
        except ArmNotRunningError:
            return False

    def send_command(
        self,
        targets,
        priority: int = 0,
        max_velocity: tuple[float, ...] | None = None,
    ) -> None:
        """Publish a *single* joint-position setpoint to the control loop.

        This is the low-level streaming primitive used by teleop-style
        callers that publish setpoints continuously at a high rate. For
        "go to this pose and hold for N seconds" use cases, prefer
        :meth:`stream_command` (or the ``go_to_*`` helpers built on it),
        because the control loop's freshness watchdog will trip the loop
        back into ``HOLDING`` after ``watchdog_timeout_s`` (default 100 ms),
        which combined with the per-joint velocity cap means a single
        ``send_command`` can move the arm at most
        ``max_velocity * watchdog_timeout_s`` rad before it stops.

        Requires an active control loop (raises :class:`ArmNotRunningError`
        otherwise). The control loop applies bounds + rate limiting, so
        callers do not need to interpolate themselves.

        Args:
            targets: Iterable of up to 6 joint targets in radians. Missing
                joints retain their last commanded value (the controller
                handles the "hold" semantics).
            priority: Higher values win over lower-priority publishers on
                the same cycle. Default 0; reserve higher numbers for
                safety/override sources.
            max_velocity: Optional per-joint speed cap in rad/s. ``0`` (or
                omitted) for a joint means "use the controller default".
        """
        targets_list = [float(t) for t in targets]
        if not targets_list:
            raise ValueError("targets must contain at least one joint value")
        if len(targets_list) > 6:
            raise ValueError(
                f"targets must have at most 6 entries, got {len(targets_list)}"
            )

        if max_velocity is None:
            vlimit_list = [0.0] * 6
        else:
            vlimit_list = [float(v) for v in max_velocity]
            if len(vlimit_list) != 6:
                raise ValueError(
                    f"max_velocity must have 6 entries, got {len(vlimit_list)}"
                )

        self._require_running()
        publisher = self._ensure_command_publisher()

        payload = JointCommandPayload()
        payload.timestamp_ns = time.time_ns()
        payload.priority = int(priority)
        payload.num_joints = len(targets_list)
        for i in range(6):
            payload.targets[i] = targets_list[i] if i < len(targets_list) else 0.0
            payload.max_velocity[i] = vlimit_list[i]

        sample = publisher.loan_uninit()
        sample = sample.write_payload(payload)
        sample.send()

    def stream_command(
        self,
        targets,
        duration: float,
        *,
        rate_hz: float | None = None,
        priority: int = 0,
        max_velocity: tuple[float, ...] | None = None,
        settle_tolerance: float | None = None,
        settle_hold_s: float = 0.15,
    ) -> bool:
        """Hold a joint-position setpoint by republishing it until the arm arrives.

        The control loop has a freshness watchdog: if no command arrives
        within ``watchdog_timeout_s`` (default 100 ms), it falls back to
        ``HOLDING`` mode and commands the current pose. To make the arm
        actually traverse to ``targets`` we have to keep publishing the
        same setpoint until the arm physically reaches it.

        This method republishes the setpoint at ``rate_hz`` (default
        :attr:`DEFAULT_STREAM_RATE_HZ`, comfortably faster than the
        watchdog) and polls the joint-positions feedback service each
        cycle. It returns as soon as **every** joint has been within
        ``settle_tolerance`` of its target for ``settle_hold_s`` seconds
        (so we don't return on a brief overshoot/undershoot zero-crossing).
        If the arm has not settled by the time ``duration`` elapses the
        method still returns (the caller can decide whether to treat that
        as a soft timeout).

        After the call returns, the watchdog naturally trips a few cycles
        later and the loop holds the arm wherever it is — which is the
        target if settling succeeded.

        Args:
            targets: Iterable of up to 6 joint targets in radians (same
                semantics as :meth:`send_command`).
            duration: Upper-bound timeout, in seconds. ``0`` or negative
                publishes once and returns immediately without waiting.
            rate_hz: Republish rate. Must be > ``1/watchdog_timeout_s`` to
                avoid the loop dropping into HOLDING mid-stream.
            priority: Forwarded to the published command.
            max_velocity: Forwarded to the published command.
            settle_tolerance: Max per-joint error (radians) considered
                "arrived". Defaults to
                :attr:`DEFAULT_SETTLE_TOLERANCE_RAD`. Set to a large value
                (e.g. ``math.inf``) to disable early exit and always
                stream for the full ``duration``.
            settle_hold_s: How long the per-joint error must stay below
                ``settle_tolerance`` before we declare the arm settled.

        Returns:
            ``True`` if the arm settled within tolerance before the
            deadline, ``False`` if the deadline was hit first.
        """
        if rate_hz is None:
            rate_hz = self.DEFAULT_STREAM_RATE_HZ
        if rate_hz <= 0:
            raise ValueError(f"rate_hz must be positive, got {rate_hz}")
        if settle_tolerance is None:
            settle_tolerance = self.DEFAULT_SETTLE_TOLERANCE_RAD

        # Validate inputs + verify the loop is alive once up front so the
        # timing loop is tight (each send_command would otherwise re-verify
        # via the heartbeat subscriber).
        targets_list = [float(t) for t in targets]
        if not targets_list:
            raise ValueError("targets must contain at least one joint value")
        if len(targets_list) > 6:
            raise ValueError(
                f"targets must have at most 6 entries, got {len(targets_list)}"
            )
        if max_velocity is None:
            vlimit_list = [0.0] * 6
        else:
            vlimit_list = [float(v) for v in max_velocity]
            if len(vlimit_list) != 6:
                raise ValueError(
                    f"max_velocity must have 6 entries, got {len(vlimit_list)}"
                )

        self._require_running()
        publisher = self._ensure_command_publisher()
        # Always create/grab the feedback subscriber up front, even if
        # ``duration <= 0``, so the underlying iceoryx2 service exists
        # and a later read won't race the publisher's setup.
        feedback_sub = self._ensure_feedback_subscriber()

        n_targets = len(targets_list)

        def _publish_once() -> None:
            payload = JointCommandPayload()
            payload.timestamp_ns = time.time_ns()
            payload.priority = int(priority)
            payload.num_joints = n_targets
            for i in range(6):
                payload.targets[i] = targets_list[i] if i < n_targets else 0.0
                payload.max_velocity[i] = vlimit_list[i]
            sample = publisher.loan_uninit()
            sample = sample.write_payload(payload)
            sample.send()

        def _latest_positions() -> tuple[float, ...] | None:
            """Drain the feedback subscriber, return the freshest sample (or None)."""
            latest: tuple[float, ...] | None = None
            while True:
                sample = feedback_sub.receive()
                if sample is None:
                    return latest
                p = sample.payload().contents
                latest = tuple(float(p.positions[i]) for i in range(6))

        def _within_tolerance(positions: tuple[float, ...]) -> bool:
            for i in range(n_targets):
                if abs(positions[i] - targets_list[i]) > settle_tolerance:
                    return False
            return True

        _publish_once()
        if duration <= 0:
            return False

        period = 1.0 / rate_hz
        deadline = time.monotonic() + duration
        settled_since: float | None = None

        while True:
            sleep_for = min(period, deadline - time.monotonic())
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()

            positions = _latest_positions()
            if positions is not None and _within_tolerance(positions):
                if settled_since is None:
                    settled_since = now
                elif (now - settled_since) >= settle_hold_s:
                    return True
            else:
                settled_since = None

            if now >= deadline:
                return False

            _publish_once()

    def send_gripper_command(
        self,
        position: float,
        effort: float | None = None,
        priority: int = 0,
    ) -> None:
        """Publish a gripper setpoint to the control loop.

        Unlike :meth:`send_command`, a single gripper publish is enough:
        the control loop is edge-triggered for the gripper (it only
        re-issues a CAN gripper frame when the setpoint *changes*) and
        the gripper firmware holds its commanded position on its own, so
        there is no watchdog-style fallback to design around.

        Requires an active control loop. The position is clamped here
        against :attr:`GRIPPER_CLOSED_POSITION` / :attr:`GRIPPER_OPEN_POSITION`
        before being sent; the SDK does its own clamping too, but doing
        it here keeps the published value sensible.
        """
        if effort is None:
            effort = self.GRIPPER_DEFAULT_EFFORT

        position = max(
            self.GRIPPER_CLOSED_POSITION,
            min(self.GRIPPER_OPEN_POSITION, float(position)),
        )
        effort = float(effort)

        self._require_running()
        publisher = self._ensure_gripper_command_publisher()

        payload = GripperCommandPayload()
        payload.timestamp_ns = time.time_ns()
        payload.priority = int(priority)
        payload.position = position
        payload.effort = effort

        sample = publisher.loan_uninit()
        sample = sample.write_payload(payload)
        sample.send()

    def trigger_estop(self) -> None:
        """Latch the control loop into ESTOPPED via the e-stop event service.

        The loop will keep commanding the current pose (so the arm holds
        position) and ignore inbound setpoints until :meth:`clear_estop`
        is called. Requires an active control loop.
        """
        import iceoryx2 as iox2

        self._require_running()
        notifier = self._ensure_estop_notifier()
        notifier.notify_with_custom_event_id(iox2.EventId.new(ESTOP_TRIGGER_EVENT_ID))
        logger.error("E-stop trigger published.")

    def clear_estop(self) -> None:
        """Release a previously-latched e-stop. Requires an active control loop."""
        import iceoryx2 as iox2

        self._require_running()
        notifier = self._ensure_estop_notifier()
        notifier.notify_with_custom_event_id(iox2.EventId.new(ESTOP_CLEAR_EVENT_ID))
        logger.warning("E-stop clear published.")

    def get_heartbeat(self, timeout: float | None = None) -> HeartbeatPayload:
        """Block briefly for and return the latest heartbeat sample."""
        return self._require_running(timeout=timeout)

    # ----------------------------------------------------------- State reads

    def _receive_latest(self, subscriber, payload_type, timeout: float):
        """Wait up to ``timeout`` seconds for the latest sample of ``payload_type``."""
        deadline = time.monotonic() + timeout
        latest = None
        # Drain the backlog first so we return the most recent value.
        while True:
            sample = subscriber.receive()
            if sample is None:
                break
            p = sample.payload().contents
            latest = payload_type()
            ctypes.memmove(
                ctypes.byref(latest), ctypes.byref(p), ctypes.sizeof(payload_type),
            )
        if latest is not None:
            return latest

        while time.monotonic() < deadline:
            time.sleep(0.01)
            sample = subscriber.receive()
            if sample is not None:
                p = sample.payload().contents
                latest = payload_type()
                ctypes.memmove(
                    ctypes.byref(latest), ctypes.byref(p), ctypes.sizeof(payload_type),
                )
                return latest
        return None

    def get_joint_positions(
        self,
        timeout: float | None = None,
    ) -> tuple[float, ...]:
        """Return the latest joint positions published by the control loop.

        Subscribes to the feedback service on first call (lazily cached
        thereafter). Raises :class:`ArmNotRunningError` if the loop is
        not alive.
        """
        if timeout is None:
            timeout = self.DEFAULT_READ_TIMEOUT_S

        self._require_running(timeout=timeout)
        sub = self._ensure_feedback_subscriber()
        latest = self._receive_latest(sub, JointPositionsPayload, timeout=timeout)
        if latest is None:
            raise ArmNotRunningError(
                f"No joint feedback received on '{self.JOINT_POSITIONS_SERVICE_NAME}' "
                f"within {timeout:.1f}s."
            )
        n = min(int(latest.num_joints), 6)
        return tuple(float(latest.positions[i]) for i in range(n))

    def get_gripper_state(
        self,
        timeout: float | None = None,
    ) -> tuple[float, float]:
        """Return the latest ``(angle, effort)`` from the gripper-state service.

        ``angle`` is in wrapper units (``0..GRIPPER_OPEN_POSITION``), so it
        is directly comparable to the values accepted by :meth:`set_gripper`.
        ``effort`` is in N·m as reported by the SDK.
        """
        if timeout is None:
            timeout = self.DEFAULT_READ_TIMEOUT_S

        self._require_running(timeout=timeout)
        sub = self._ensure_gripper_state_subscriber()
        latest = self._receive_latest(sub, GripperStatePayload, timeout=timeout)
        if latest is None:
            raise ArmNotRunningError(
                f"No gripper state received on '{self.GRIPPER_STATE_SERVICE_NAME}' "
                f"within {timeout:.1f}s."
            )
        return float(latest.angle), float(latest.effort)

    # ---------------------------------------------------------- High-level moves
    #
    # All ``go_to_*`` / gripper helpers below publish through iceoryx2 and
    # then optionally sleep ``settle_time`` to let the controller drive the
    # arm to the target. The watchdog in the control loop will keep
    # commanding the latest target even after these methods return, so the
    # arm holds the pose until the next command is published.

    # ``settle_time`` here is an *upper bound* — stream_command exits as
    # soon as the arm is within tolerance of the target, so most moves
    # finish well before this. Generous defaults make worst-case sweeps
    # (e.g. zero → pre-grasp) robust without slowing down typical moves.
    DEFAULT_GO_TO_SETTLE_TIME_S = 5.0

    def _log_arrival(self, name: str, arrived: bool, duration: float) -> None:
        if arrived:
            logger.info("Arm reached %s pose.", name)
        else:
            logger.warning(
                "Arm did NOT reach %s pose within %.1fs; HOLDING in place.",
                name, duration,
            )

    def go_to_zero(self, settle_time: float | None = None) -> None:
        """Command the arm to its zero pose, waiting up to ``settle_time`` to arrive."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to zero position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(self.JOINT_POSITIONS_ZERO, duration=settle_time)
        self._log_arrival("zero", arrived, settle_time)

    def go_to_ready(self, settle_time: float | None = None) -> None:
        """Command the arm to the ready pose (gripper stays at its current state)."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to ready position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(self.JOINT_POSITIONS_READY, duration=settle_time)
        self._log_arrival("ready", arrived, settle_time)

    def go_to_pregrasp(self, settle_time: float | None = None) -> None:
        """Command the arm to the pre-grasp pose (gripper stays at its current state)."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to pre-grasp position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(
            self.JOINT_POSITIONS_PREGRASP, duration=settle_time,
        )
        self._log_arrival("pre-grasp", arrived, settle_time)

    def go_to_grasp(self, settle_time: float | None = None) -> None:
        """Command the arm to the grasp pose (gripper stays at its current state)."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to grasp position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(
            self.JOINT_POSITIONS_GRASP, duration=settle_time,
        )
        self._log_arrival("grasp", arrived, settle_time)

    def go_to_scan(self, settle_time: float | None = None) -> None:
        """Command the arm to the scan pose."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to scan position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(self.JOINT_POSITIONS_SCAN, duration=settle_time)
        self._log_arrival("scan", arrived, settle_time)

    def go_to_good_bin(self, settle_time: float | None = None) -> None:
        """Command the arm to the good-bin drop-off pose."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to good bin position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(
            self.JOINT_POSITIONS_GOOD_BIN, duration=settle_time,
        )
        self._log_arrival("good bin", arrived, settle_time)

    def go_to_bad_bin(self, settle_time: float | None = None) -> None:
        """Command the arm to the bad-bin drop-off pose."""
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info("Going to bad bin position (timeout=%.1fs)", settle_time)
        arrived = self.stream_command(
            self.JOINT_POSITIONS_BAD_BIN, duration=settle_time,
        )
        self._log_arrival("bad bin", arrived, settle_time)

    def go_to_joint_positions(self, positions, settle_time: float | None = None) -> None:
        """Command the arm to a custom joint pose (radians)."""
        positions = tuple(float(p) for p in positions)
        if len(positions) != 6:
            raise ValueError(f"Expected 6 joint positions, got {len(positions)}")
        if settle_time is None:
            settle_time = self.DEFAULT_GO_TO_SETTLE_TIME_S
        logger.info(
            "Going to custom joint positions (timeout=%.1fs): %s",
            settle_time, positions,
        )
        arrived = self.stream_command(positions, duration=settle_time)
        self._log_arrival("custom", arrived, settle_time)

    def open_gripper(self) -> None:
        """Fully open the gripper."""
        logger.info("Opening gripper")
        self.send_gripper_command(
            position=self.GRIPPER_OPEN_POSITION,
            effort=self.GRIPPER_DEFAULT_EFFORT,
        )

    def close_gripper(self) -> None:
        """Fully close the gripper."""
        logger.info("Closing gripper")
        self.send_gripper_command(
            position=self.GRIPPER_CLOSED_POSITION,
            effort=self.GRIPPER_DEFAULT_EFFORT,
        )

    def set_gripper(self, position: float, effort: float | None = None) -> None:
        """Command the gripper to a specific position (0 closed .. 10 open)."""
        logger.info("Commanding gripper position: %s", position)
        self.send_gripper_command(position=position, effort=effort)

    # ----------------------------------------------------------------- IK

    @staticmethod
    def _load_chain() -> Chain:
        """Load the Piper kinematic chain from the packaged URDF."""
        urdf_resource = files("printq.assets").joinpath("piper_description.urdf")
        with as_file(urdf_resource) as urdf_path:
            return Chain.from_urdf_file(str(urdf_path))

    def _ensure_chain(self) -> Chain:
        if self.chain is None:
            self.chain = self._load_chain()
        return self.chain

    def move_ik(
        self,
        end_off,
        settle_time: float | None = None,
    ) -> tuple[float, ...]:
        """Solve position-only IK to ``end_off`` and command the arm there.

        Reads current joints via the feedback service (so the loop must
        be running), seeds ikpy with them, validates bounds, and streams
        the resulting joint command until the arm arrives (or
        ``settle_time`` elapses).

        Args:
            end_off: Target end-effector position ``(x, y, z)`` in meters.
            settle_time: Upper-bound time to wait for the arm to settle
                at the IK solution. Forwards to
                :meth:`go_to_joint_positions`.

        Returns:
            The 6 joint values (radians) commanded to the arm.
        """
        chain = self._ensure_chain()
        robot_joints = np.asarray(self.get_joint_positions(), dtype=float)

        # ikpy's initial_position must have one entry per chain link
        # (including fixed links). The Piper URDF maps robot joints 1..6
        # to chain link indices 1..6, sandwiched between a fixed base
        # link and fixed gripper links, so we slot the robot's joints in
        # there and leave the fixed slots at zero.
        initial_position = np.zeros(len(chain.links))
        initial_position[1:7] = robot_joints

        ik_solution = chain.inverse_kinematics(
            target_position=end_off,
            initial_position=initial_position,
        )
        target_joints = ik_solution[1:7]

        tolerance = 1e-5
        for link, joint_angle in zip(chain.links[1:7], target_joints):
            if link.bounds is None or len(link.bounds) != 2:
                continue
            lower_limit, upper_limit = link.bounds
            if (
                joint_angle < lower_limit - tolerance
                or joint_angle > upper_limit + tolerance
            ):
                logger.warning(
                    "IK solution for %s out of bounds: %.4f not in [%.4f, %.4f]",
                    link.name, joint_angle, lower_limit, upper_limit,
                )

        logger.info("IK solution joints: %s", target_joints)
        self.go_to_joint_positions(target_joints, settle_time=settle_time)
        return tuple(float(j) for j in target_joints)

    # ------------------------------------------------------------- Orchestration

    def run_print_cycle(
        self,
        get_ik_grasp_joints,
        get_bambu_gripper_close_value,
        get_vlm_decision,
        move_settle_time: float | None = None,
    ) -> str:
        """Run the full PrintQ arm cycle by publishing commands to the loop.

        Sequence:
            1. Go to zero.
            2. Go to ready.
            3. Go to pre-grasp.
            4. Use IK to move to grasp pose.
            5. Use Bambu-derived value to close gripper.
            6. Go to scan pose.
            7. Use VLM decision.
            8. Drop in good/bad bin.
            9. Return to ready.

        Callback expectations:
            get_ik_grasp_joints() -> (x, y, z) target for the gripper.
            get_bambu_gripper_close_value() -> number from 0 to 10
            get_vlm_decision() -> "good" or "bad"
        """
        logger.info("Starting full print cycle.")

        self._require_running()

        logger.info("Step 1: Go to zero.")
        self.go_to_zero(settle_time=move_settle_time)

        logger.info("Step 2: Go to ready.")
        self.go_to_ready(settle_time=move_settle_time)

        logger.info("Step 3: Go to pre-grasp.")
        self.go_to_pregrasp(settle_time=move_settle_time)

        logger.info("Step 4: Get IK grasp target.")
        grasp_target = get_ik_grasp_joints()
        if grasp_target is None:
            raise RuntimeError("IK callback returned no target.")

        logger.info("Step 5: Move to IK grasp pose.")
        self.move_ik(grasp_target, settle_time=move_settle_time)

        logger.info("Step 6: Get Bambu gripper close amount.")
        close_value = get_bambu_gripper_close_value()

        logger.info("Step 7: Close gripper to %s.", close_value)
        self.set_gripper(close_value)
        time.sleep(1.0)

        logger.info("Step 8: Move to scan pose.")
        self.go_to_scan(settle_time=move_settle_time)

        logger.info("Step 9: Get VLM quality decision.")
        decision = str(get_vlm_decision()).strip().lower()
        if decision not in ("good", "bad"):
            raise RuntimeError(f"VLM returned invalid decision: {decision}")

        if decision == "good":
            logger.info("Step 10: Move to good bin.")
            self.go_to_good_bin(settle_time=move_settle_time)
        else:
            logger.info("Step 10: Move to bad bin.")
            self.go_to_bad_bin(settle_time=move_settle_time)

        logger.info("Step 11: Release print.")
        self.open_gripper()
        time.sleep(1.0)

        logger.info("Step 12: Return to ready.")
        self.go_to_ready(settle_time=move_settle_time)

        logger.info("Print cycle finished. Decision: %s", decision)
        return decision

    # ----------------------------------------------------------- Calibration
    #
    # Calibration disables individual motors and pokes the SDK directly,
    # so it cannot share the bus with the control loop. Each routine
    # opens its own short-lived PiperInterface and refuses to run if a
    # control loop is detected.

    def _require_loop_stopped(self) -> None:
        if self._is_loop_running():
            raise RuntimeError(
                "Calibration requires the control loop to be stopped. "
                "Exit 'printq arm start' first, then re-run."
            )

    def calibrate_joints(self) -> None:
        """Calibrate every joint sequentially by setting its current pose as zero.

        Requires the control loop to be STOPPED (this opens its own
        ``PiperInterface`` for the duration).

        For each joint from 1 through 6, the routine:
          1. Disables that single motor so it can be moved by hand.
          2. Prompts the operator to physically move the joint to its zero pose.
          3. On Enter, sets the joint's current position as zero.
          4. Re-enables the motor before moving on to the next joint.

        Enter ``q`` at any prompt to abort calibration early. The current motor
        will be re-enabled before the routine returns.
        """
        self._require_loop_stopped()

        logger.warning(
            "Calibration disables motors one at a time. "
            "Support the arm so it does not fall."
        )

        piper = piper_interface.PiperInterface(can_port=self.can_port)
        # The wrapper exposes only whole-arm enable/disable, so reach through
        # to the underlying SDK for per-motor control.
        raw_sdk = piper.piper

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

            piper.set_joint_zero_positions([joint_num - 1])
            raw_sdk.EnableArm(joint_num)
            logger.info(f"Joint {joint_num} zero set and re-enabled.")

        logger.info("All joints calibrated.")

    def calibrate_gripper(self) -> None:
        """Set the gripper's current position as its zero.

        Requires the control loop to be STOPPED (this opens its own
        ``PiperInterface`` for the duration).

        Manually move the gripper to the desired zero pose before calling
        this method. A confirmation prompt is shown before the zero is
        committed; respond with ``y``/``yes`` to proceed, anything else to
        abort.

        The routine mirrors the SDK ``piper_set_gripper_zero`` demo:
        it first sends a disable/settle command to the gripper, waits
        briefly for it to stabilize, then commits the current position
        as the new zero.
        """
        self._require_loop_stopped()

        logger.warning(
            "Manually move the gripper to its zero position before continuing."
        )
        answer = input("Set gripper zero at current position? [y/N]: ")
        if answer.strip().lower() not in ("y", "yes"):
            logger.warning("Gripper calibration aborted by user.")
            return

        piper = piper_interface.PiperInterface(can_port=self.can_port)

        # Step 1: disable + settle. The wrapper's set_gripper_zero_position()
        # only performs the final commit, so we send this directly via the
        # raw SDK to match the official demo.
        piper.piper.GripperCtrl(0, 1000, 0x00, 0)
        time.sleep(1.5)

        # Step 2: commit current position as zero.
        piper.set_gripper_zero_position()
        logger.info("Gripper zero position set.")