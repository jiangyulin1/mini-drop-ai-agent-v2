"""Tests for Go pprof collector."""

from __future__ import annotations

import os
from unittest import mock

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.pprof import PprofCollector


class TestPprofCollector:
    @staticmethod
    def _task(**kwargs) -> CollectorTask:
        return CollectorTask(
            id="pprof_test_001",
            collector_type="go_pprof",
            target_pid=1234,  # not used for HTTP-based pprof
            sample_rate=99,
            duration_sec=10,
            options=kwargs.get("options", {}),
        )

    def test_http_connection_error(self, tmp_path):
        collector = PprofCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "连接失败" in result.reason or "pprof" in result.reason.lower()

    def test_http_404_returns_failure(self, tmp_path):
        collector = PprofCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        mock_error = mock.MagicMock()
        mock_error.code = 404
        mock_error.read.return_value = b"not found"
        with mock.patch("urllib.request.urlopen", side_effect=Exception("HTTP 404")):
            result = collector.collect(self._task())
        assert result.ok is False

    def test_empty_response(self, tmp_path):
        collector = PprofCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        mock_resp.read.return_value = b""
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = collector.collect(self._task())
        assert result.ok is False
        assert "空数据" in result.reason

    def test_successful_collection(self, tmp_path):
        collector = PprofCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        os.makedirs(os.path.join(str(tmp_path), "pprof_test_001"), exist_ok=True)
        pprof_data = b"mock pprof gzip data" * 100

        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        mock_resp.read.return_value = pprof_data

        with mock.patch("urllib.request.urlopen", return_value=mock_resp), \
             mock.patch.object(PprofCollector, "_pprof_to_svg", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is True
        assert len(result.artifacts) >= 1
        raw = [a for a in result.artifacts if a["artifact_type"] == "pprof_raw"]
        assert len(raw) == 1

    def test_go_not_installed_skips_svg(self, tmp_path):
        collector = PprofCollector()
        collector.OUTPUT_BASE = str(tmp_path)
        os.makedirs(os.path.join(str(tmp_path), "pprof_test_001"), exist_ok=True)

        mock_resp = mock.MagicMock()
        mock_resp.__enter__ = mock.MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = mock.MagicMock(return_value=False)
        mock_resp.read.return_value = b"pprof data"

        with mock.patch("urllib.request.urlopen", return_value=mock_resp), \
             mock.patch.object(PprofCollector, "_pprof_to_svg", return_value=False):
            result = collector.collect(self._task())
        assert result.ok is True
        assert "go 未安装" in result.reason or "跳过" in result.reason
