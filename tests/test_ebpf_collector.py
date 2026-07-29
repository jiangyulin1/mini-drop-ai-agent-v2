"""eBPF 采集器单元测试。"""

import os
import signal
import subprocess
from unittest import mock

import pytest

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.ebpf import EBPFCollector


@pytest.fixture(name="collector")
def collector_fixture() -> EBPFCollector:
    return EBPFCollector()


@pytest.fixture(name="task")
def task_fixture() -> CollectorTask:
    return CollectorTask(
        id="ebpf_test_001",
        collector_type="ebpf_io",
        target_pid=1234,
        sample_rate=99,
        duration_sec=10,
    )


class TestEBPFAvailability:
    """bpftrace 可用性检查。"""

    def test_bpftrace_not_installed(self, collector, task):
        with mock.patch("shutil.which", return_value=None):
            result = collector.collect(task)
        assert result.ok is False
        assert "bpftrace" in result.reason

    def test_script_not_found(self, collector, task, tmp_path):
        collector.OUTPUT_BASE = str(tmp_path)
        with mock.patch("shutil.which", return_value="/usr/bin/bpftrace"), \
             mock.patch("os.path.isfile", return_value=False):
            result = collector.collect(task)
        assert result.ok is False
        assert "未找到" in result.reason


class TestEBPFExecution:
    """bpftrace 子进程执行路径。"""

    def test_execution_success(self, collector, task, tmp_path):
        collector.OUTPUT_BASE = str(tmp_path)
        output_dir = tmp_path / task.id
        output_dir.mkdir(parents=True)
        (output_dir / "io_latency.txt").write_text(
            "@latency_us:\n[64, 128)     10 |@@@@\n")
        (output_dir / "ebpf_metrics.json").write_text("{}")

        mock_proc = mock.MagicMock(returncode=0, stdout=mock.MagicMock(), stderr=None)
        mock_proc.communicate.return_value = (b"", b"")

        with mock.patch("shutil.which", return_value="/usr/bin/bpftrace"), \
             mock.patch("os.path.isfile", return_value=True), \
             mock.patch("subprocess.Popen", return_value=mock_proc) as popen_mock:
            result = collector.collect(task)

        assert result.ok is True
        assert "10 个样本" in result.reason
        assert result.artifacts[0]["artifact_type"] == "ebpf_metrics"
        cmd = popen_mock.call_args.args[0]
        assert cmd == [
            "/usr/bin/bpftrace",
            "-o",
            str(output_dir / "io_latency.txt"),
            mock.ANY,
        ]

    def test_timeout_sends_sigint(self, collector, task, tmp_path):
        collector.OUTPUT_BASE = str(tmp_path)
        output_dir = tmp_path / task.id
        output_dir.mkdir(parents=True)
        (output_dir / "io_latency.txt").write_text("@latency_us:\n")

        mock_proc = mock.MagicMock(returncode=-2, stdout=mock.MagicMock(), stderr=None)
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd=["bpftrace"], timeout=10)
        mock_proc.communicate.return_value = (b"", b"")

        with mock.patch("shutil.which", return_value="/usr/bin/bpftrace"), \
             mock.patch("os.path.isfile", return_value=True), \
             mock.patch("subprocess.Popen", return_value=mock_proc):
            result = collector.collect(task)

        mock_proc.send_signal.assert_called_with(signal.SIGINT)
        mock_proc.wait.assert_called_once_with(timeout=task.duration_sec)
        assert result.ok is True

    def test_missing_output_file_fails(self, collector, task, tmp_path):
        collector.OUTPUT_BASE = str(tmp_path)
        (tmp_path / task.id).mkdir(parents=True)

        mock_proc = mock.MagicMock(returncode=0, stdout=mock.MagicMock(), stderr=None)
        mock_proc.communicate.return_value = (b"", b"")

        def script_only_isfile(path):
            return str(path).endswith("io_latency.bt")

        with mock.patch("shutil.which", return_value="/usr/bin/bpftrace"), \
             mock.patch("os.path.isfile", side_effect=script_only_isfile), \
             mock.patch("subprocess.Popen", return_value=mock_proc):
            result = collector.collect(task)

        assert result.ok is False
        assert "未产出" in result.reason


class TestHistogramParsing:
    """bpftrace histogram 解析。"""

    def test_parse_standard_histogram(self, collector, tmp_path):
        path = tmp_path / "hist.txt"
        path.write_text(
            "@latency_us:\n"
            "[128, 256)      10 |@@@@|\n"
            "[256, 512)        5 |@@  |\n"
            "[512, 1K)         2 |@   |\n"
        )
        result = collector._parse_histogram(str(path))
        assert result["[128, 256)"] == 10
        assert result["[256, 512)"] == 5
        assert "[512, 1000)" in result
        assert result["[512, 1000)"] == 2

    def test_parse_with_k_m_suffixes(self, collector, tmp_path):
        path = tmp_path / "hist.txt"
        path.write_text(
            "@latency_us:\n"
            "[1K, 2K)     3 |@   |\n"
            "[2K, 4K)     1 |    |\n"
            "[1m, 2m)     1 |    |\n"
        )
        result = collector._parse_histogram(str(path))
        assert result["[1000, 2000)"] == 3
        assert result["[1000000, 2000000)"] == 1

    def test_parse_empty_file(self, collector, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("")
        result = collector._parse_histogram(str(path))
        assert result == {}

    def test_parse_no_histogram_lines(self, collector, tmp_path):
        path = tmp_path / "no_hist.txt"
        path.write_text("some other output\nno histogram here\n")
        result = collector._parse_histogram(str(path))
        assert result == {}
