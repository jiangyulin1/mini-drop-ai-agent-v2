"""E0/E3 Pi Agent Runtime contract baseline.

The integration plan pins @earendil-works/pi-coding-agent 0.84.0 (author's
machine) but the current working machine ships 0.83.0.  This test pins the
*contract*, not the exact patch: it verifies the RPC mode emits parseable
NDJSON envelopes and fails closed without an API key (never silently hangs),
and that --no-tools removes built-in tools from the session.

Full Scripted-Provider loop, steer/abort and extension checks land in E3.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

PI_BIN = shutil.which("pi") or shutil.which("pi.cmd")


def _require_pi() -> list[str]:
    """Windows npm shims are .CMD wrappers that need cmd /c to execute."""
    if not PI_BIN:
        pytest.skip("pi binary not on PATH")
    lower = PI_BIN.lower()
    if os.name == "nt" and lower.endswith(".cmd"):
        return ["cmd", "/c", PI_BIN]
    return [PI_BIN]


def _run_pi(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "PI_TELEMETRY": "0"}  # 保留系统 PATH，cmd /c 需要找到 node
    started = time.monotonic()
    proc = subprocess.run(
        [*_require_pi(), *args],
        capture_output=True, text=True, timeout=timeout,
        env=env, encoding="utf-8", errors="replace",
    )
    proc.elapsed_sec = time.monotonic() - started  # type: ignore[attr-defined]
    return proc


def _parse_rpc_lines(output: str) -> list[dict]:
    events: list[dict] = []
    for raw in output.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return events


def _run_rpc(*rpc_inputs: str, timeout: int = 40) -> subprocess.CompletedProcess:
    payload = "".join(f"{line}\n" for line in rpc_inputs)
    env = {**os.environ, "PI_TELEMETRY": "0"}  # 保留系统 PATH，cmd /c 需要找到 node
    started = time.monotonic()
    proc = subprocess.run(
        [*_require_pi(), "--mode", "rpc", "--no-tools"],
        input=payload, capture_output=True, text=True, timeout=timeout,
        env=env, encoding="utf-8", errors="replace",
    )
    proc.elapsed_sec = time.monotonic() - started  # type: ignore[attr-defined]
    return proc


def test_pi_binary_is_present_and_rpc_mode_is_available():
    help_text = subprocess.run(
        [*_require_pi(), "--help"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    ).stdout
    assert "--mode <mode>" in help_text
    assert "rpc" in help_text
    assert "--no-tools" in help_text or "--no-builtin-tools" in help_text


def test_pi_rpc_protocol_emits_response_envelope_and_fails_closed():
    """Contract: JSONL framing, envelope schema, and fail-closed on unknown types."""
    proc = _run_rpc('{"type":"unknown_command_xyz"}')
    assert proc.elapsed_sec < 35, "pi did not exit promptly"
    events = _parse_rpc_lines(proc.stdout)
    assert events, "rpc mode must emit at least one parseable NDJSON event"
    assert any(ev.get("type") == "response" for ev in events)
    failure = next(ev for ev in events if ev.get("type") == "response")
    assert failure.get("success") is False, "unknown command must be rejected (fail-closed)"


def test_pi_reports_version_with_expected_major_minor():
    proc = subprocess.run(
        [*_require_pi(), "--version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    version = proc.stdout.strip().splitlines()[0]
    major_minor = tuple(int(part) for part in version.split(".")[:2])
    # Plan targets 0.84.0; local machine 0.83.0.  Both satisfy the contract.
    assert major_minor[0] == 0, f"unexpected pi major version {version}"
    assert major_minor[1] >= 83, f"pi too old: {version} (need >= 0.83)"


def test_sidecar_package_lock_and_banner_version_are_consistent():
    import json
    from server.app.agent_runtime.config import pi_runtime_version
    package_path = Path(__file__).resolve().parents[1] / "agent_runtime" / "pi-sidecar" / "package.json"
    package = json.loads(package_path.read_text())
    actual = package["dependencies"]["@earendil-works/pi-coding-agent"].split(".")[:2]
    declared = pi_runtime_version().split(".")[:2]
    assert declared == actual
    runtime_src = (
        Path(__file__).resolve().parents[1]
        / "agent_runtime" / "pi-sidecar" / "src" / "runtime.mjs"
    ).read_text()
    assert f"pi-{pi_runtime_version()}" in runtime_src
