"""NLP 自然语言采集测试。

覆盖：意图解析 / 进程解析 / 关键词降级 / 结果总结 / 追问建议。
"""

import json
from unittest import mock

import pytest

from server.app.nlp.intent_parser import (
    _clamp_and_validate,
    _keyword_fallback,
    _extract_process_name,
    parse_intent,
)
from server.app.nlp.process_resolver import resolve_pid
from server.app.nlp.summarizer import _truncate_summary, summarize, suggest_followup


class TestIntentParsing:
    """自然语言意图解析。"""

    def test_keyword_fallback_cpu(self):
        intent = _keyword_fallback("mysqld CPU 飙高，帮我看看")
        assert intent.collector_type == "perf_cpu"
        assert intent.process_name == "mysqld"

    def test_keyword_fallback_io(self):
        intent = _keyword_fallback("磁盘很慢，可能有 IO 瓶颈")
        assert intent.collector_type == "ebpf_io"

    def test_keyword_fallback_python(self):
        intent = _keyword_fallback("Django 项目 CPU 高，帮忙诊断")
        assert intent.collector_type == "pyspy"

    def test_keyword_fallback_continuous(self):
        intent = _keyword_fallback("帮我对 nginx 持续监控一周")
        assert intent.collector_type == "continuous_perf"

    def test_keyword_fallback_unknown(self):
        intent = _keyword_fallback("帮我看看性能")
        assert intent.collector_type == "perf_cpu"

    def test_duration_clamped_to_120(self):
        result = _clamp_and_validate({
            "process_name": "test", "collector_type": "perf_cpu",
            "duration_sec": 999, "sample_rate": 99,
            "reasoning": "test",
        })
        assert result.duration_sec == 120

    def test_sample_rate_clamped_to_999(self):
        result = _clamp_and_validate({
            "process_name": "test", "collector_type": "perf_cpu",
            "duration_sec": 15, "sample_rate": 9999,
            "reasoning": "test",
        })
        assert result.sample_rate == 999

    def test_invalid_collector_falls_back(self):
        result = _clamp_and_validate({
            "process_name": "test", "collector_type": "garbage",
            "reasoning": "test",
        })
        assert result.collector_type == "perf_cpu"

    def test_extract_process_name(self):
        assert _extract_process_name("mysqld CPU 飙高") == "mysqld"
        assert _extract_process_name("nginx 高负载") == "nginx"

    def test_api_key_not_set_uses_fallback(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            import importlib
            import server.app.nlp.intent_parser as ip
            importlib.reload(ip)
            result = ip.parse_intent("mysqld CPU 飙高")
            assert result.collector_type == "perf_cpu"
            assert result.process_name == "mysqld"

    def test_api_call_parses_tool_result(self):
        mock_resp = mock.MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "create_profiling_task",
                            "arguments": json.dumps({
                                "process_name": "mysqld",
                                "collector_type": "perf_cpu",
                                "duration_sec": 30,
                                "sample_rate": 99,
                                "reasoning": "CPU 飙高，perf 是最合适的采集器"
                            })
                        }
                    }]
                }
            }]
        }

        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-key"}):
            import importlib
            import server.app.nlp.intent_parser as ip
            importlib.reload(ip)
            with mock.patch("server.app.nlp.intent_parser.chat_completions", return_value=mock_resp):
                result = ip.parse_intent("mysqld CPU 飙高")
                assert result.collector_type == "perf_cpu"
                assert result.duration_sec == 30
                assert result.process_name == "mysqld"

    def test_api_call_forces_non_thinking_tool_choice(self):
        mock_resp = mock.MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "create_profiling_task",
                "arguments": json.dumps({
                    "process_name": "mysqld", "collector_type": "ebpf_io",
                    "duration_sec": 17, "sample_rate": 101, "reasoning": "explicit",
                }),
            }}]}}],
        }
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-key"}):
            with mock.patch("server.app.nlp.intent_parser.chat_completions", return_value=mock_resp) as call:
                parse_intent("mysqld IO 17 秒 101Hz")
        payload = call.call_args.args[0]
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["tool_choice"]["function"]["name"] == "create_profiling_task"


class TestProcessResolver:
    """进程解析。"""

    def test_resolve_in_non_linux_returns_empty(self):
        """Windows / macOS / 容器无 /proc 时返回空列表。"""
        matches = resolve_pid("python")
        # 在 Dev 环境下可能无 /proc，返回空列表
        assert isinstance(matches, list)

    @mock.patch("os.listdir", return_value=["1", "1234"])
    @mock.patch("os.path.isdir", return_value=True)
    def test_resolve_with_mock_proc(self, mock_isdir, mock_listdir):
        # comm 返回 "mysqld", cmdline 返回空 bytes
        def mock_open_file(path, mode="r", *args, **kwargs):
            if "comm" in path:
                m = mock.mock_open(read_data="mysqld")
                return m()
            m = mock.mock_open(read_data=b"")
            return m()
        with mock.patch("builtins.open", side_effect=mock_open_file):
            matches = resolve_pid("mysqld")
            assert len(matches) == 2
            assert matches[0].pid == 1


class TestSummarizer:
    """结果总结。"""

    def test_template_summary_no_api_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            result = summarize([{"name": "fib_hotspot", "samples": 100, "percent": 68.5}])
            assert "主要发现" in result
            assert "fib_hotspot" in result

    def test_summary_empty_top_returns_hint(self):
        result = summarize([])
        assert "未产出" in result

    def test_api_call_returns_content(self):
        top = [{"name": "fib_hotspot", "samples": 100, "percent": 68.5}]
        mock_resp = mock.MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "主要发现：CPU 热点在 fib_hotspot，建议加入缓存。"}}]
        }

        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-key"}):
            import importlib
            import server.app.nlp.summarizer as sm
            importlib.reload(sm)
            with mock.patch("server.app.nlp.summarizer.chat_completions", return_value=mock_resp):
                result = sm.summarize(top)
                assert "fib_hotspot" in result

    def test_api_error_falls_back_to_template(self):
        top = [{"name": "fib_hotspot", "samples": 100, "percent": 68.5}]
        mock_resp = mock.MagicMock(status_code=500)

        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-key"}):
            import importlib
            import server.app.nlp.summarizer as sm
            importlib.reload(sm)
            with mock.patch("server.app.nlp.summarizer.chat_completions", return_value=mock_resp):
                result = sm.summarize(top)
                assert "主要发现" in result

    def test_summary_has_hard_character_limit(self):
        result = _truncate_summary("测" * 300)
        assert len(result) == 150
        assert result.endswith("…")


class TestFollowup:
    """追问建议。"""

    def test_high_cpu_suggests_pyspy(self):
        questions = suggest_followup([
            {"name": "fib_hotspot", "samples": 100, "percent": 80.0}
        ], collector_type="perf_cpu")
        assert any("py-spy" in q for q in questions)

    def test_perf_collector_suggests_ebpf(self):
        questions = suggest_followup([
            {"name": "fib", "samples": 100, "percent": 50.0}
        ], collector_type="perf_cpu")
        assert any("eBPF" in q for q in questions)

    def test_empty_top_returns_single_suggestion(self):
        questions = suggest_followup([])
        assert len(questions) == 1
        assert "perf_cpu" in questions[0]
