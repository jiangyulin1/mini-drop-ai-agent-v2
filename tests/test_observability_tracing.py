"""Regression tests for optional cross-process OpenTelemetry propagation."""

from __future__ import annotations

import os
import subprocess
import sys

from mini_drop_observability.tracing import start_span


def test_tracing_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("MINI_DROP_TRACING_ENABLED", raising=False)

    with start_span("disabled") as span:
        assert span is None


def test_w3c_parent_is_preserved_by_enabled_tracing(tmp_path):
    trace_id = "11111111111111111111111111111111"
    parent_id = "2222222222222222"
    script = tmp_path / "trace_probe.py"
    script.write_text(
        "\n".join(
            [
                "from mini_drop_observability.tracing import (",
                "    configure_tracing, shutdown_tracing, start_span,",
                "    trace_id_from_current, traceparent_from_current,",
                ")",
                "configure_tracing('mini-drop-test')",
                f"with start_span('probe', traceparent='00-{trace_id}-{parent_id}-01'):",
                "    print('TRACE_ID=' + trace_id_from_current())",
                "    print('TRACEPARENT=' + traceparent_from_current())",
                "shutdown_tracing()",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "MINI_DROP_TRACING_ENABLED": "1",
            "MINI_DROP_TRACE_EXPORTER": "console",
            "PYTHONPATH": os.pathsep.join(
                part for part in [os.getcwd(), env.get("PYTHONPATH", "")] if part
            ),
        }
    )

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert f"TRACE_ID={trace_id}" in result.stdout
    assert f"TRACEPARENT=00-{trace_id}-" in result.stdout
    assert '"name": "probe"' in result.stdout
    assert f'"trace_id": "0x{trace_id}"' in result.stdout
