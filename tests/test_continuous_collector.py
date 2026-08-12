"""Continuous Profiling 采集器单元测试。"""

import os
import signal
import subprocess
from unittest import mock

import pytest

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.continuous import ContinuousCollector


@pytest.fixture(name="collector")
def collector_fixture() -> ContinuousCollector:
    c = ContinuousCollector()
    c.WINDOW_DURATION_SEC = 5
    c.WINDOW_INTERVAL_SEC = 10
    c.WINDOW_SAMPLE_RATE = 11
    return c


@pytest.fixture(name="task")
def task_fixture() -> CollectorTask:
    return CollectorTask(
        id="continuous_test",
        collector_type="continuous_perf",
        target_pid=1234,
        sample_rate=11,
        duration_sec=15,
    )


class TestContinuousAvailability:
    """可用性检查。"""

    def test_perf_not_installed(self, collector, task):
        with mock.patch("shutil.which", return_value=None):
            result = collector.collect(task)
        assert result.ok is False
        assert "perf" in result.reason

    def test_pid_not_exists(self, collector, task):
        with mock.patch("shutil.which", return_value="/usr/bin/perf"), \
             mock.patch.object(collector, "_pid_exists", return_value=False):
            result = collector.collect(task)
        assert result.ok is False
        assert "不存在" in result.reason


class TestContinuousExecution:
    """窗口执行路径。"""

    def test_single_window_no_time_remaining(self, collector, task, tmp_path):
        """仅一轮采集 → 1 个窗口 → 返回 ok 且带 window summary。"""
        collector.OUTPUT_BASE = str(tmp_path)
        task_ = CollectorTask(
            id="t1", collector_type="continuous_perf",
            target_pid=1234, sample_rate=11, duration_sec=1,
        )

        base = tmp_path / task_.id / "window_000"
        base.mkdir(parents=True)
        (base / "perf.data").write_text("perf")

        mock_proc = mock.MagicMock(returncode=0, stdout=mock.MagicMock(), stderr=None)
        mock_proc.communicate.return_value = (b"", b"")

        with mock.patch("shutil.which", return_value="/usr/bin/perf"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch("subprocess.Popen", return_value=mock_proc) as popen_mock, \
             mock.patch("os.setpgrp", create=True), \
             mock.patch("time.sleep") as sleep_mock:
            result = collector.collect(task_)

        assert result.ok is True
        has_window = any(a["artifact_type"] == "continuous_window" for a in result.artifacts)
        summary = next(a for a in result.artifacts if a["artifact_type"] == "continuous_summary")
        assert has_window
        assert os.path.isfile(summary["local_path"])
        assert summary["size_bytes"] > 0
        sleep_mock.assert_not_called()
        command = popen_mock.call_args_list[0].args[0]
        assert command[-2:] == ["sleep", "1"]

    def test_zero_ok_windows_returns_failure(self, collector, task, tmp_path):
        """所有窗口都失败 → 返回 False。"""
        collector.OUTPUT_BASE = str(tmp_path)
        task_ = CollectorTask(
            id="t2", collector_type="continuous_perf",
            target_pid=1234, sample_rate=11, duration_sec=1,
        )
        (tmp_path / task_.id).mkdir(parents=True)

        mock_proc = mock.MagicMock(returncode=1, stdout=mock.MagicMock(), stderr=None)
        mock_proc.communicate.return_value = (b"", b"")

        with mock.patch("shutil.which", return_value="/usr/bin/perf"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch("subprocess.Popen", return_value=mock_proc), \
             mock.patch("os.setpgrp", create=True), \
             mock.patch("time.sleep"):
            result = collector.collect(task_)

        assert result.ok is False

    def test_perf_process_start_failure_is_reported_not_crashed(self, collector, tmp_path):
        collector.OUTPUT_BASE = str(tmp_path)
        task = CollectorTask(
            id="t-start-failure", collector_type="continuous_perf",
            target_pid=1234, sample_rate=11, duration_sec=1,
        )
        with mock.patch("shutil.which", return_value="/usr/bin/perf"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch("subprocess.Popen", side_effect=OSError("cannot fork")):
            result = collector.collect(task)

        assert result.ok is False
        summary = next(a for a in result.artifacts if a["artifact_type"] == "continuous_summary")
        assert summary["metadata"]["windows"][0]["reason"] == "cannot fork"

    def test_timeout_terminates_perf_and_returns_failed_window(self, collector, tmp_path):
        collector.OUTPUT_BASE = str(tmp_path)
        task = CollectorTask(
            id="t-timeout", collector_type="continuous_perf",
            target_pid=1234, sample_rate=11, duration_sec=1,
        )
        proc = mock.MagicMock(returncode=None)
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="perf", timeout=31),
            (b"", b""),
        ]
        with mock.patch("shutil.which", return_value="/usr/bin/perf"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch("subprocess.Popen", return_value=proc):
            result = collector.collect(task)

        assert result.ok is False
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=5)


class TestPidCheck:
    def test_pid_exists_on_linux(self, collector):
        with mock.patch("os.path.isdir", return_value=True) as m:
            assert collector._pid_exists(42) is True
            m.assert_called_with("/proc/42")
