"""Execution-time guard against collecting from a reused process ID.

Discovery authorizes a stable process incarnation, not a bare PID.  The
Control plane pins that identity into Task options and this module rechecks it
on the Agent immediately before a collector starts.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import platform
from typing import Any

from agent.mini_drop_agent.collectors.network_discovery import NetworkDiscoveryCollector


@dataclass(frozen=True)
class TargetIncarnationCheck:
    """Result of one execution-time process-incarnation check."""

    allowed: bool
    status: str
    message: str = ""
    expected_entity_id: str = ""
    actual_entity_id: str = ""


def check_target_incarnation(
    target_pid: int,
    options: dict[str, Any] | None,
) -> TargetIncarnationCheck:
    """Validate the current process against a discovery-pinned identity.

    Linux procfs and a complete macOS ``ps`` observation provide an exact
    boot-ID + PID + start-time comparison.  macOS may be running through the
    lsof fallback without enough information to reconstruct that tuple; in
    that case collection remains possible but is explicitly marked limited
    instead of being reported as strongly verified.
    """

    values = options if isinstance(options, dict) else {}
    guard_requested = bool(
        values.get("discovery_followup_authority") is True
        or any(
            key in values
            for key in (
                "expected_boot_id",
                "expected_process_start_time",
                "expected_entity_id",
            )
        )
    )
    if not guard_requested:
        return TargetIncarnationCheck(allowed=True, status="not_requested")

    agent_id = str(values.get("agent_id") or "").strip()
    expected_boot_id = str(values.get("expected_boot_id") or "").strip()
    expected_entity_id = str(values.get("expected_entity_id") or "").strip()
    try:
        expected_start_time = int(values.get("expected_process_start_time") or 0)
    except (TypeError, ValueError):
        expected_start_time = 0

    if (
        not agent_id
        or not expected_boot_id
        or expected_start_time <= 0
        or not expected_entity_id
    ):
        return TargetIncarnationCheck(
            allowed=False,
            status="invalid_expectation",
            message=(
                "TARGET_INCARCATION_EXPECTATION_INVALID: discovery follow-up "
                "requires agent_id, expected_boot_id, "
                "expected_process_start_time and expected_entity_id"
            ),
            expected_entity_id=expected_entity_id,
        )

    canonical_expected = (
        f"process:{agent_id}:{expected_boot_id}:{target_pid}:{expected_start_time}"
    )
    if expected_entity_id != canonical_expected:
        return TargetIncarnationCheck(
            allowed=False,
            status="invalid_expectation",
            message=(
                "TARGET_INCARCATION_EXPECTATION_INVALID: expected_entity_id "
                "does not match the pinned boot/PID/start-time tuple"
            ),
            expected_entity_id=expected_entity_id,
        )

    current, limitation = _read_current_target_incarnation(target_pid)
    if current is None:
        if limitation.startswith("TARGET_INCARCATION_VALIDATION_LIMITED"):
            return TargetIncarnationCheck(
                allowed=True,
                status="limited",
                message=limitation,
                expected_entity_id=expected_entity_id,
            )
        if limitation.startswith("TARGET_INCARCATION_CHANGED"):
            return TargetIncarnationCheck(
                allowed=False,
                status="changed",
                message=limitation,
                expected_entity_id=expected_entity_id,
            )
        return TargetIncarnationCheck(
            allowed=False,
            status="unverifiable",
            message=limitation or (
                "TARGET_INCARCATION_UNVERIFIABLE: current process identity "
                "could not be read"
            ),
            expected_entity_id=expected_entity_id,
        )

    current_boot_id = str(current.get("boot_id") or "")
    try:
        current_start_time = int(current.get("process_start_time") or 0)
    except (TypeError, ValueError):
        current_start_time = 0
    actual_entity_id = (
        f"process:{agent_id}:{current_boot_id}:{target_pid}:{current_start_time}"
    )
    if (
        current_boot_id != expected_boot_id
        or current_start_time != expected_start_time
        or actual_entity_id != expected_entity_id
    ):
        return TargetIncarnationCheck(
            allowed=False,
            status="changed",
            message=(
                "TARGET_INCARCATION_CHANGED: the discovered process incarnation "
                "no longer matches the process currently owning this PID"
            ),
            expected_entity_id=expected_entity_id,
            actual_entity_id=actual_entity_id,
        )

    return TargetIncarnationCheck(
        allowed=True,
        status="verified",
        expected_entity_id=expected_entity_id,
        actual_entity_id=actual_entity_id,
    )


def _read_current_target_incarnation(
    target_pid: int,
) -> tuple[dict[str, Any] | None, str]:
    """Read the strongest process identity available on the local platform."""

    system = platform.system()
    collector = NetworkDiscoveryCollector()

    if system == "Linux":
        proc_root = collector.PROC_ROOT
        boot_id = collector._read_text(  # noqa: SLF001 - shared collector identity contract
            "sys/kernel/random/boot_id", max_bytes=128,
        ).strip()
        stat_path = os.path.join(proc_root, str(target_pid), "stat")
        stat_value = collector._read_text(  # noqa: SLF001
            f"{target_pid}/stat", max_bytes=65536,
        )
        start_time = collector._process_start_ticks(stat_value)  # noqa: SLF001
        if not os.path.exists(os.path.join(proc_root, str(target_pid))):
            return None, (
                "TARGET_INCARCATION_CHANGED: the discovered process no longer exists"
            )
        if not boot_id:
            return None, (
                "TARGET_INCARCATION_UNVERIFIABLE: Linux boot_id is unavailable; "
                "collection was stopped"
            )
        if not os.path.exists(stat_path) or start_time is None:
            return None, (
                "TARGET_INCARCATION_UNVERIFIABLE: Linux process start time is "
                "unavailable; collection was stopped"
            )
        return {
            "boot_id": boot_id,
            "process_start_time": int(start_time),
        }, ""

    if system == "Darwin":
        if not collector._darwin_pid_exists(target_pid):  # noqa: SLF001
            return None, (
                "TARGET_INCARCATION_CHANGED: the discovered process no longer exists"
            )
        boot_id, boot_time = collector._darwin_boot_identity()  # noqa: SLF001
        processes = collector._darwin_processes(  # noqa: SLF001
            [target_pid], comm_by_pid={}, boot_id=boot_id,
        )
        process = processes.get(target_pid) or {}
        start_time = process.get("process_start_time")
        # Without kern.boottime or a precise lstart value, the lsof fallback
        # cannot prove a stable incarnation.  Do not turn hostname/None hashes
        # into a fake strong identity check.
        if boot_time is None or start_time is None:
            return None, (
                "TARGET_INCARCATION_VALIDATION_LIMITED: macOS lsof/ps could not "
                "reconstruct the complete boot/PID/start-time tuple"
            )
        return {
            "boot_id": boot_id,
            "process_start_time": int(start_time),
        }, ""

    try:
        os.kill(target_pid, 0)
    except PermissionError:
        pass
    except (ProcessLookupError, OSError):
        return None, (
            "TARGET_INCARCATION_CHANGED: the discovered process no longer exists"
        )
    return None, (
        f"TARGET_INCARCATION_VALIDATION_LIMITED: {system or 'unknown platform'} "
        "does not expose a supported stable process-incarnation identity"
    )
