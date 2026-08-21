"""Execution-time protection for discovery-authorized process targets."""

from __future__ import annotations

from pathlib import Path

from agent.mini_drop_agent.collectors.base import CollectorResult
from agent.mini_drop_agent.collectors.network_discovery import NetworkDiscoveryCollector
from agent.mini_drop_agent.main import COLLECTORS, _collector_error_code, _run_collector
from agent.mini_drop_agent.process_incarnation import (
    TargetIncarnationCheck,
    check_target_incarnation,
)


def _options(*, pid: int = 43210, start_time: int = 200) -> dict:
    agent_id = "agent-remote"
    boot_id = "boot-remote"
    return {
        "agent_id": agent_id,
        "discovery_followup_authority": True,
        "expected_boot_id": boot_id,
        "expected_process_start_time": start_time,
        "expected_entity_id": (
            f"process:{agent_id}:{boot_id}:{pid}:{start_time}"
        ),
    }


def _write_proc_identity(
    root: Path,
    *,
    pid: int = 43210,
    boot_id: str = "boot-remote",
    start_time: int = 200,
) -> None:
    (root / "sys/kernel/random").mkdir(parents=True)
    (root / "sys/kernel/random/boot_id").write_text(boot_id, encoding="utf-8")
    process_dir = root / str(pid)
    process_dir.mkdir(parents=True)
    # NetworkDiscoveryCollector parses fields after the closing ')'; index 19
    # is Linux /proc stat field 22 (process start time in clock ticks).
    fields = ["S", *("0" for _ in range(18)), str(start_time)]
    (process_dir / "stat").write_text(
        f"{pid} (worker with spaces) {' '.join(fields)}\n",
        encoding="utf-8",
    )


def test_linux_matching_incarnation_is_verified(tmp_path, monkeypatch):
    proc_root = tmp_path / "proc"
    _write_proc_identity(proc_root)
    monkeypatch.setattr(NetworkDiscoveryCollector, "PROC_ROOT", str(proc_root))
    monkeypatch.setattr(
        "agent.mini_drop_agent.process_incarnation.platform.system",
        lambda: "Linux",
    )

    result = check_target_incarnation(43210, _options())

    assert result.allowed is True
    assert result.status == "verified"
    assert result.actual_entity_id == _options()["expected_entity_id"]


def test_linux_reused_pid_is_rejected_before_collection(tmp_path, monkeypatch):
    proc_root = tmp_path / "proc"
    _write_proc_identity(proc_root, start_time=201)
    monkeypatch.setattr(NetworkDiscoveryCollector, "PROC_ROOT", str(proc_root))
    monkeypatch.setattr(
        "agent.mini_drop_agent.process_incarnation.platform.system",
        lambda: "Linux",
    )

    result = check_target_incarnation(43210, _options(start_time=200))

    assert result.allowed is False
    assert result.status == "changed"
    assert result.message.startswith("TARGET_INCARCATION_CHANGED")
    assert result.actual_entity_id.endswith(":43210:201")


def test_macos_incomplete_lsof_identity_is_explicitly_limited(monkeypatch):
    monkeypatch.setattr(
        "agent.mini_drop_agent.process_incarnation.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        NetworkDiscoveryCollector, "_darwin_pid_exists", staticmethod(lambda _pid: True),
    )
    monkeypatch.setattr(
        NetworkDiscoveryCollector,
        "_darwin_boot_identity",
        staticmethod(lambda: ("darwin-incomplete", None)),
    )
    monkeypatch.setattr(
        NetworkDiscoveryCollector,
        "_darwin_processes",
        lambda self, pids, *, comm_by_pid, boot_id: {
            pids[0]: {"process_start_time": None},
        },
    )

    result = check_target_incarnation(43210, _options())

    assert result.allowed is True
    assert result.status == "limited"
    assert result.message.startswith("TARGET_INCARCATION_VALIDATION_LIMITED")
    assert "macOS lsof/ps" in result.message


def test_run_collector_stops_before_collector_when_incarnation_changed(monkeypatch):
    class StubCollector:
        called = False

        def collect(self, _task):
            self.called = True
            return CollectorResult(ok=True, reason="should not run", artifacts=[])

    stub = StubCollector()
    expected = _options()["expected_entity_id"]
    monkeypatch.setitem(COLLECTORS, "sys_metrics", stub)
    monkeypatch.setattr(
        "agent.mini_drop_agent.main.check_target_incarnation",
        lambda _pid, _values: TargetIncarnationCheck(
            allowed=False,
            status="changed",
            message="TARGET_INCARCATION_CHANGED: PID was reused",
            expected_entity_id=expected,
        ),
    )

    ok, reason, artifacts = _run_collector({
        "id": "task-incarnation-changed",
        "collector_type": "sys_metrics",
        "target_pid": 43210,
        "sample_rate": 1,
        "duration_sec": 1,
        "request_params": {"options": _options()},
    })

    assert ok is False
    assert reason.startswith("TARGET_INCARCATION_CHANGED")
    assert artifacts == []
    assert stub.called is False
    assert _collector_error_code(ok, reason) == "TARGET_INCARCATION_CHANGED"


def test_run_collector_records_macos_validation_limitation(monkeypatch):
    class StubCollector:
        def collect(self, _task):
            return CollectorResult(
                ok=True,
                reason="collection complete",
                artifacts=[{
                    "artifact_type": "sys_metrics",
                    "filename": "metrics.json",
                    "metadata": {"schema_version": "test.v1"},
                }],
            )

    expected = _options()["expected_entity_id"]
    monkeypatch.setitem(COLLECTORS, "sys_metrics", StubCollector())
    monkeypatch.setattr(
        "agent.mini_drop_agent.main.check_target_incarnation",
        lambda _pid, _values: TargetIncarnationCheck(
            allowed=True,
            status="limited",
            message=(
                "TARGET_INCARCATION_VALIDATION_LIMITED: macOS lsof/ps identity "
                "is incomplete"
            ),
            expected_entity_id=expected,
        ),
    )

    ok, reason, artifacts = _run_collector({
        "id": "task-incarnation-limited",
        "collector_type": "sys_metrics",
        "target_pid": 43210,
        "sample_rate": 1,
        "duration_sec": 1,
        "request_params": {"options": _options()},
    })

    assert ok is True
    assert "TARGET_INCARCATION_VALIDATION_LIMITED" in reason
    metadata = artifacts[0]["metadata"]
    assert metadata["target_incarnation_validation"] == "limited"
    assert metadata["expected_entity_id"] == expected
    assert "macOS lsof/ps" in metadata["target_incarnation_limitation"]
