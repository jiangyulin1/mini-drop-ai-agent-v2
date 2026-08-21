"""Darwin fallback coverage for the portable sys_metrics collector."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

import pytest

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.sys_metrics import SysMetricsCollector


def test_parse_darwin_cpu_clock():
    assert SysMetricsCollector._parse_cpu_time("0:01.25") == 1.25
    assert SysMetricsCollector._parse_cpu_time("2:03:04.5") == 7384.5
    assert SysMetricsCollector._parse_cpu_time("1-02:03:04.5") == 93784.5
    assert SysMetricsCollector._parse_cpu_time("not-a-clock") == 0.0


def test_darwin_network_reader_counts_each_link_once(monkeypatch):
    fixture = """Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll
lo0 16384 <Link#1> 10 0 100 20 0 200 0
lo0 16384 127 127.0.0.1 10 - 100 20 - 200 -
en0 1500 <Link#2> aa:bb:cc:dd:ee:ff 30 0 300 40 0 400 0
"""
    monkeypatch.setattr(
        SysMetricsCollector,
        "_run_readonly",
        staticmethod(lambda command, timeout=3.0: fixture),
    )

    assert SysMetricsCollector._read_darwin_network_dev() == {
        "rx_bytes": 400,
        "tx_bytes": 600,
    }


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS fallback only")
def test_real_darwin_snapshot_is_partial_and_truthful(tmp_path):
    collector = SysMetricsCollector()
    collector.OUTPUT_BASE = str(tmp_path)
    task = CollectorTask(
        id="darwin-real-snapshot",
        collector_type="sys_metrics",
        target_pid=os.getpid(),
        sample_rate=1,
        duration_sec=1,
        options={"mode": "snapshot"},
    )

    result = collector.collect(task)

    assert result.ok, result.reason
    payload = json.loads(
        Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8")
    )
    assert payload["coverage"]["platform"] == "darwin"
    assert payload["coverage"]["level"] == "partial"
    assert "linux_psi" in payload["coverage"]["unavailable"]
    assert payload["process"]["pid"] == os.getpid()
    assert payload["process"]["memory"]["rss_bytes"] > 0
    assert payload["process"]["threads"]["count"] >= 1
    assert payload["host"]["memory"]["total_bytes"] > 0
    assert payload["host"]["psi"] == {}
    assert payload["container"] == {
        "memory_event_deltas": {},
        "memory_usage_ratio": None,
        "cpu_core_usage": None,
    }
    assert result.artifacts[0]["metadata"]["coverage_level"] == "partial"
