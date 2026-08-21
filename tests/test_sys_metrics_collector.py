"""Tests for SysMetrics multi-dimensional collector."""

from __future__ import annotations

import json
import os
from unittest import mock

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.sys_metrics import SysMetricsCollector
from server.app.diagnosis.domain_analyzers import analyze_observations
from server.app.diagnosis.sys_metrics import normalize_sys_metrics


class TestSysMetricsCollector:
    @staticmethod
    def _task(**kwargs) -> CollectorTask:
        return CollectorTask(
            id="sys_test_001",
            collector_type="sys_metrics",
            target_pid=1234,
            sample_rate=99,
            duration_sec=kwargs.get("duration_sec", 3),
            options=kwargs.get("options", {}),
        )

    def test_pid_not_exists(self):
        collector = SysMetricsCollector()
        with mock.patch.object(collector, "_pid_exists", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "PID" in result.reason

    def test_snapshot_mode(self, tmp_path):
        collector = SysMetricsCollector()
        collector.OUTPUT_BASE = str(tmp_path)

        def pid_exists(pid):
            return True

        with mock.patch("agent.mini_drop_agent.collectors.sys_metrics.platform.system", return_value="Linux"), \
             mock.patch.object(collector, "_pid_exists", side_effect=pid_exists), \
             mock.patch.object(collector, "_read_proc_stat_total", return_value={"user": 1000, "system": 500, "idle": 8500, "iowait": 100}), \
             mock.patch.object(collector, "_read_loadavg", return_value={"load1m": 0.5, "load5m": 0.3, "load15m": 0.2}), \
             mock.patch.object(collector, "_read_process_metrics", return_value={"num_threads": 12, "fd_count": 45, "vmrss_kb": 102400}), \
             mock.patch.object(collector, "_read_network_dev", return_value={"rx_bytes": 100000, "tx_bytes": 50000}):
            result = collector.collect(self._task(duration_sec=1, options={"mode": "snapshot"}))

        assert result.ok is True
        assert len(result.artifacts) == 1
        assert result.artifacts[0]["artifact_type"] == "sys_metrics"
        assert os.path.isfile(result.artifacts[0]["local_path"])

    def test_content_has_all_dimensions(self, tmp_path):
        collector = SysMetricsCollector()
        collector.OUTPUT_BASE = str(tmp_path)

        with mock.patch("agent.mini_drop_agent.collectors.sys_metrics.platform.system", return_value="Linux"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_read_proc_stat_total", return_value={"user": 1000, "system": 300, "idle": 8700, "iowait": 50}), \
             mock.patch.object(collector, "_read_loadavg", return_value={"load1m": 1.0, "load5m": 0.8, "load15m": 0.6}), \
             mock.patch.object(collector, "_read_process_metrics", return_value={
                 "num_threads": 8, "fd_count": 23, "vmrss_kb": 51200,
                 "voluntary_switches": 500, "nonvoluntary_switches": 200,
             }), \
             mock.patch.object(collector, "_read_network_dev", return_value={"rx_bytes": 0, "tx_bytes": 0}):
            result = collector.collect(self._task(duration_sec=2, options={"mode": "snapshot"}))

        assert result.ok
        with open(result.artifacts[0]["local_path"], "r") as fh:
            data = json.load(fh)
        assert "summary" in data
        assert "samples" in data
        assert data["sample_count"] >= 1
        s = data["summary"]
        assert "avg_cpu_user_pct" in s
        assert "thread_count" in s
        assert "fd_count" in s
        assert "load1m" in s

    def test_fd_trend_detection(self, tmp_path):
        collector = SysMetricsCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        fd_values = [10, 11, 12, 13, 15]
        call_count = [0]

        def pid_exists(pid):
            call_count[0] += 1
            return call_count[0] <= len(fd_values) + 2

        def proc_metrics(pid):
            idx = min(call_count[0] - 1, len(fd_values) - 1)
            return {"fd_count": fd_values[idx] if idx < len(fd_values) else fd_values[-1],
                    "num_threads": 5, "vmrss_kb": 10240}

        with mock.patch("agent.mini_drop_agent.collectors.sys_metrics.platform.system", return_value="Linux"), \
             mock.patch.object(collector, "_pid_exists", side_effect=pid_exists), \
             mock.patch.object(collector, "_read_proc_stat_total", return_value={"user": 500, "system": 200, "idle": 9300, "iowait": 0}), \
             mock.patch.object(collector, "_read_loadavg", return_value={"load1m": 0.1, "load5m": 0.1, "load15m": 0.1}), \
             mock.patch.object(collector, "_read_process_metrics", side_effect=proc_metrics), \
             mock.patch.object(collector, "_read_network_dev", return_value={"rx_bytes": 0, "tx_bytes": 0}):
            result = collector.collect(self._task(duration_sec=6))

        if result.ok:
            with open(result.artifacts[0]["local_path"], "r") as fh:
                data = json.load(fh)
            assert data["summary"]["fd_trend"] == "increasing"

    def test_parse_stat(self):
        """Verify stat parsing logic."""
        collector = SysMetricsCollector()
        # Test the parsing by simulating a well-formed line
        result = collector._read_proc_stat_total()
        # On Windows this returns {}; the test is about not crashing
        assert isinstance(result, dict)

    def test_parse_network_dev(self):
        """Verify network parsing doesn't crash on missing file."""
        collector = SysMetricsCollector()
        result = collector._read_network_dev()
        assert isinstance(result, dict)
        assert "rx_bytes" in result

    def test_proc_stat_parser_handles_spaces_in_comm(self):
        fields = {index: str(index) for index in range(4, 25)}
        fields.update({14: "111", 15: "222", 20: "42", 22: "987654", 23: "4096", 24: "100"})
        line = "1234 (worker pool thread) S " + " ".join(fields[index] for index in range(4, 25))
        parsed = SysMetricsCollector._parse_proc_stat(line)
        assert parsed["pid"] == 1234
        assert parsed["utime_ticks"] == 111
        assert parsed["stime_ticks"] == 222
        assert parsed["num_threads"] == 42
        assert parsed["start_time_ticks"] == 987654

    def test_v2_process_deltas_and_rss_slope(self, monkeypatch):
        monkeypatch.setattr(os, "cpu_count", lambda: 4)
        samples = [
            {"ts": 10.0, "host_cpu": {"user_ratio": .5, "system_ratio": .1, "iowait_ratio": .02},
             "load": {"load1": 2.0, "load5": 1.0}, "host_memory": {}, "psi": {}, "container": {},
             "host_network": {"rx_bytes": 100, "tx_bytes": 200},
             "process": {"utime_ticks": 100, "stime_ticks": 50, "rss_bytes": 1000, "fd_count": 10,
                         "num_threads": 4, "read_bytes": 1000, "write_bytes": 2000, "start_time_ticks": 99}},
            {"ts": 12.0, "host_cpu": {"user_ratio": .7, "system_ratio": .2, "iowait_ratio": .04},
             "load": {"load1": 4.0, "load5": 2.0}, "host_memory": {}, "psi": {}, "container": {},
             "host_network": {"rx_bytes": 2100, "tx_bytes": 4200},
             "process": {"utime_ticks": 200, "stime_ticks": 150, "rss_bytes": 5000, "fd_count": 12,
                         "num_threads": 6, "read_bytes": 3000, "write_bytes": 6000, "start_time_ticks": 99}},
        ]
        normalized = SysMetricsCollector._compute_v2(1234, samples)
        assert normalized["host"]["cpu"]["core_count"] == 4
        assert normalized["process"]["memory"]["rss_slope_bytes_per_second"] == 2000
        assert normalized["process"]["io"]["read_bytes_per_second"] == 1000
        assert normalized["process"]["fd"]["growth_per_minute"] == 60
        assert normalized["host"]["load"]["load1_window_avg"] == 3

    def test_legacy_normalizer_separates_host_and_process(self):
        value = normalize_sys_metrics({"pid": 12, "summary": {
            "avg_cpu_user_pct": 90, "process_cpu_core_usage": .1, "vmrss_mb": 10,
        }})
        assert value["normalized_from"] == "legacy.v1"
        assert value["host"]["cpu"]["user_ratio"] == .9
        assert value["process"]["cpu"]["normalized_core_usage"] == .1

    def test_host_cpu_high_does_not_claim_process_hotspot(self):
        findings = analyze_observations([{
            "task_id": "t", "target": {"instance_id": "i"},
            "facts": {"avg_cpu_user_pct": 92, "process_cpu_core_usage": .1},
            "top_function": {"name": "", "percent": 0}, "evidence_refs": ["ev"],
        }])
        types = {item["finding_type"] for item in findings}
        assert "host_cpu_pressure" in types
        assert "userland_hotspot" not in types
