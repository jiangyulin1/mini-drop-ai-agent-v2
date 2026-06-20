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
