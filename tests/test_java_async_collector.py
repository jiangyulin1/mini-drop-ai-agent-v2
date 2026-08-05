"""Tests for Java async-profiler collector."""

from __future__ import annotations

from unittest import mock

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.java_async import JavaAsyncProfilerCollector


class TestJavaAsyncProfiler:
    @staticmethod
    def _task(**kwargs) -> CollectorTask:
        return CollectorTask(
            id="java_test_001",
            collector_type="java_async",
            target_pid=1234,
            sample_rate=99,
            duration_sec=10,
            options=kwargs.get("options", {}),
        )

    def test_profiler_not_installed(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch.object(collector, "_find_profiler", return_value=None):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "不可用" in result.reason

    def test_pid_not_exists(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/profiler.sh"), \
             mock.patch.object(collector, "_pid_exists", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "PID" in result.reason and "不存在" in result.reason

    def test_not_java_process(self):
        collector = JavaAsyncProfilerCollector()
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/profiler.sh"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_is_java_process", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "JVM" in result.reason

    def test_invalid_event(self):
        collector = JavaAsyncProfilerCollector()
        task = self._task(options={"event": "invalid"})
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/profiler.sh"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_is_java_process", return_value=True):
            result = collector.collect(task)
        assert result.ok is False
        assert "不支持的 event" in result.reason

    def test_valid_events_are_accepted(self):
        assert "cpu" in JavaAsyncProfilerCollector.VALID_EVENTS
        assert "alloc" in JavaAsyncProfilerCollector.VALID_EVENTS
        assert "lock" in JavaAsyncProfilerCollector.VALID_EVENTS

    def test_asprof_html_success_uses_executable_directly(self, tmp_path, monkeypatch):
        collector = JavaAsyncProfilerCollector()
        monkeypatch.setattr(collector, "OUTPUT_BASE", str(tmp_path))

        class Process:
            returncode = 0
            pid = 4321

            def communicate(self, timeout):
                output = tmp_path / "java_test_001" / "java_flamegraph.html"
                output.write_text("<html>flamegraph</html>", encoding="utf-8")
                return b"", b""

            def poll(self):
                return 0

        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/bin/asprof"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_is_java_process", return_value=True), \
             mock.patch("subprocess.Popen", return_value=Process()) as popen:
            result = collector.collect(self._task())

        assert result.ok is True
        assert result.artifacts[0]["artifact_type"] == "java_flamegraph_html"
        command = popen.call_args.args[0]
        assert command[0] == "/opt/async-profiler/bin/asprof"
        assert command[-1] == "1234"

    def test_jfr_output_is_persisted(self, tmp_path, monkeypatch):
        collector = JavaAsyncProfilerCollector()
        monkeypatch.setattr(collector, "OUTPUT_BASE", str(tmp_path))

        class Process:
            returncode = 0
            pid = 4321

            def communicate(self, timeout):
                output = tmp_path / "java_test_001" / "java_profile.jfr"
                output.write_bytes(b"FLR\x00test")
                return b"", b""

            def poll(self):
                return 0

        task = self._task(options={"event": "cpu", "output_format": "jfr"})
        with mock.patch.object(collector, "_find_profiler", return_value="/opt/async-profiler/bin/asprof"), \
             mock.patch.object(collector, "_pid_exists", return_value=True), \
             mock.patch.object(collector, "_is_java_process", return_value=True), \
             mock.patch("subprocess.Popen", return_value=Process()) as popen:
            result = collector.collect(task)

        assert result.ok is True
        assert result.artifacts[0]["artifact_type"] == "java_profile_jfr"
        command = popen.call_args.args[0]
        assert command[1:3] == ["-o", "jfr"]

    def test_find_profiler_prefers_current_asprof_layout(self, tmp_path, monkeypatch):
        home = tmp_path / "async-profiler"
        (home / "bin").mkdir(parents=True)
        asprof = home / "bin" / "asprof"
        asprof.write_text("launcher", encoding="utf-8")
        (home / "profiler.sh").write_text("legacy", encoding="utf-8")
        monkeypatch.setenv("ASYNC_PROFILER_HOME", str(home))

        assert JavaAsyncProfilerCollector._find_profiler() == str(asprof)

    def test_root_agent_assigns_output_directory_to_target_jvm(self, tmp_path):
        output_dir = tmp_path / "task"
        output_dir.mkdir()
        target_stat = mock.Mock(st_uid=1001, st_gid=1002)

        with mock.patch("os.geteuid", return_value=0, create=True), \
             mock.patch("os.stat", return_value=target_stat), \
             mock.patch("os.chown", create=True) as chown, \
             mock.patch("os.chmod") as chmod:
            JavaAsyncProfilerCollector._prepare_output_dir(str(output_dir), 4321)

        chown.assert_called_once_with(str(output_dir), 1001, 1002)
        assert chmod.call_args_list == [
            mock.call(str(tmp_path), 0o711),
            mock.call(str(output_dir), 0o750),
        ]
