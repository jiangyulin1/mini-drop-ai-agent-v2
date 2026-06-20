"""Tests for Linux memory smaps collector."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.memory import MemoryCollector


class TestMemoryCollector:
    @staticmethod
    def _task(**kwargs) -> CollectorTask:
        return CollectorTask(
            id="mem_test_001",
            collector_type="memory_smaps",
            target_pid=1234,
            sample_rate=99,
            duration_sec=5,
            options=kwargs.get("options", {}),
        )

    def test_pid_not_exists(self):
        collector = MemoryCollector()
        with mock.patch.object(collector, "_pid_exists", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "PID" in result.reason

    def test_no_proc_access(self, tmp_path):
        collector = MemoryCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        with mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch("os.path.isfile", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "权限" in result.reason or "读取" in result.reason or "退出" in result.reason

    def test_memory_sampling_with_status(self, tmp_path):
        collector = MemoryCollector()
        collector.OUTPUT_BASE = str(tmp_path)

        rss_values = [100, 102, 105, 108, 112]  # increasing trend

        def mock_status(path):
            return path.endswith("status")

        def mock_parse_status(path):
            idx = min(len(rss_values) - 1, int((len(rss_values) - 1) * 0.75))
            return {"rss_mb": float(rss_values[idx]), "vmsize_mb": 256.0}

        def mock_parse_smaps(path):
            return {"rss_mb": 105.0, "pss_mb": 95.0, "swap_mb": 0.0}

        def mock_pid_exists(pid):
            return True

        with mock.patch.object(collector, "_pid_exists", side_effect=mock_pid_exists), \
             mock.patch.object(collector, "_parse_smaps", side_effect=mock_parse_smaps), \
             mock.patch.object(collector, "_parse_status", side_effect=mock_parse_status), \
             mock.patch("os.path.isfile", side_effect=mock_status):
            result = collector.collect(self._task(duration_sec=2))

        assert result.ok is True
        assert len(result.artifacts) == 1
        assert result.artifacts[0]["artifact_type"] == "memory_json"
        assert os.path.isfile(result.artifacts[0]["local_path"])

    def test_pid_dies_during_collection(self, tmp_path):
        collector = MemoryCollector()
        collector.OUTPUT_BASE = str(tmp_path)

        call_count = [0]

        def pid_exists_dies(pid):
            call_count[0] += 1
            return call_count[0] <= 2  # dies after 2 checks

        with mock.patch.object(collector, "_pid_exists", side_effect=pid_exists_dies), \
             mock.patch("os.path.isfile", return_value=True):
            result = collector.collect(self._task(duration_sec=10))

        # May have some samples before PID dies
        if result.ok:
            assert result.artifacts[0]["artifact_type"] == "memory_json"
        else:
            assert "未能采集" in result.reason or "退出" in result.reason

    def test_smaps_parser(self, tmp_path):
        # Use values large enough to not be rounded to 0.0 after /1024 and round(_, 2)
        smaps_content = """00400000-00401000 r-xp 00000000 08:01 12345  /usr/bin/cat
Size:              10240 kB
KernelPageSize:        4 kB
MMUPageSize:           4 kB
Rss:                5120 kB
Pss:                4096 kB
Pss_Dirty:             0 kB
Shared_Clean:          0 kB
Shared_Dirty:          0 kB
Private_Clean:      2048 kB
Private_Dirty:       512 kB
Referenced:         3072 kB
Swap:               1024 kB
SwapPss:               0 kB
"""
        smaps_path = tmp_path / "smaps"
        smaps_path.write_text(smaps_content)

        result = MemoryCollector._parse_smaps(str(smaps_path))
        assert result["rss_mb"] == 5.0  # 5120/1024
        assert result["pss_mb"] == 4.0  # 4096/1024
        assert result["swap_mb"] == 1.0  # 1024/1024

    def test_status_parser(self, tmp_path):
        status_content = """Name:	bash
VmPeak:	  123456 kB
VmSize:	  100000 kB
VmRSS:	   50000 kB
VmSwap:	    1000 kB
"""
        status_path = tmp_path / "status"
        status_path.write_text(status_content)

        result = MemoryCollector._parse_status(str(status_path))
        assert result["rss_mb"] == pytest.approx(50000 / 1024, abs=1)
        assert result["vmsize_mb"] == pytest.approx(100000 / 1024, abs=1)
        assert result["swap_mb"] == pytest.approx(1000 / 1024, abs=0.1)
