"""日志探针（log_scan 采集器 + log_analyzer Finding）测试。"""

import json
import os
from pathlib import Path

import pytest

from agent.mini_drop_agent.collectors.log_scan import LogScanCollector
from server.app.diagnosis.domain_analyzers import analyze_observations
from server.app.diagnosis.orchestrator import _log_summary
from server.app.diagnosis.probe_registry import choose_probe_ids, get_probe, list_probes


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_probe_registered():
    probe = get_probe("process_log_scan")
    assert probe.risk_level == "R1"
    assert probe.requires_approval is False
    assert probe.runner_task_kind == "log_scan"
    assert "log_scan" in {item.runner_task_kind for item in list_probes()}


def test_choose_probe_ids_includes_log_for_errors():
    ids = choose_probe_ids("error_increase")
    assert "process_log_scan" in ids
    assert ids[0] == "process_log_scan"
    ids2 = choose_probe_ids("connection_failure")
    assert "process_log_scan" in ids2


def test_parse_log_extracts_levels_patterns_errors(tmp_path):
    log_file = tmp_path / "service.log"
    _write_log(log_file, [
        "2026-08-05T10:00:00+08:00 INFO  starting service",
        "2026-08-05T10:00:05+08:00 WARN  slow query 1.2s",
        "2026-08-05T10:00:10+08:00 ERROR connection refused: 192.168.10.20:3306",
        "2026-08-05T10:00:11+08:00 ERROR connection refused: 192.168.10.20:3306",
        "2026-08-05T10:00:12+08:00 ERROR timeout after 3000ms",
        "noise line without level",
    ])
    parsed = LogScanCollector()._parse_log(str(log_file))
    assert parsed["level_counts"]["ERROR"] == 3
    assert parsed["level_counts"]["WARN"] == 1
    assert parsed["level_counts"]["INFO"] == 1
    assert parsed["patterns"]["connection_refused"] == 2
    assert parsed["patterns"]["timeout"] >= 1
    assert len(parsed["error_lines"]) >= 3
    assert any("connection refused" in item["text"] for item in parsed["error_lines"])
    assert parsed["error_lines"][0]["ts"].startswith("2026-08-05")


def test_parse_log_skips_binary_or_empty(tmp_path):
    binary = tmp_path / "data.bin"
    binary.write_bytes(bytes(range(256)) * 64)  # 16KB 二进制
    parsed = LogScanCollector()._parse_log(str(binary))
    assert parsed["error_lines"] == []


def test_log_summary_aggregates(tmp_path):
    files = [
        {
            "path": "a.log",
            "level_counts": {"ERROR": 2, "INFO": 10},
            "patterns": {"connection_refused": 2, "timeout": 1},
            "error_lines": [{"text": "connection refused", "ts": "2026-08-05T10:00:00+08:00"}],
        },
        {
            "path": "b.log",
            "level_counts": {"ERROR": 1, "WARN": 5},
            "patterns": {"timeout": 1},
            "error_lines": [{"text": "timeout", "ts": ""}],
        },
    ]
    summary = _log_summary({"log_files": files})
    assert summary["error_count"] == 2
    assert summary["patterns"]["connection_refused"] == 2
    assert summary["patterns"]["timeout"] == 2
    assert summary["levels"]["ERROR"] == 3
    assert len(summary["top_errors"]) == 2

    empty = _log_summary({"log_files": []})
    assert empty["error_count"] == 0
    assert _log_summary(None) is None


def test_log_analyzer_generates_findings():
    observation = {
        "task_id": "task_1",
        "target": {"instance_id": "svc-1", "service_id": "svc", "pid": 123},
        "evidence_refs": ["ev-1"],
        "log": {
            "log_files": 2,
            "error_count": 3,
            "patterns": {"connection_refused": 2, "timeout": 1},
            "levels": {"ERROR": 3},
            "top_errors": [{"text": "connection refused: x:3306", "ts": "2026-08-05T10:00:00+08:00"}],
        },
    }
    findings = analyze_observations([observation])
    finding_types = [item["finding_type"] for item in findings]
    assert "error_pattern" in finding_types
    assert "connectivity_errors" in finding_types
    assert "timeout_errors" in finding_types
    error_finding = next(item for item in findings if item["finding_type"] == "error_pattern")
    assert error_finding["analyzer_id"] == "log_analyzer.v1"
    assert "connection_refused" in error_finding["summary"]
    assert "ev-1" in error_finding["evidence_refs"]


def test_log_analyzer_silent_without_logs():
    observation = {
        "task_id": "task_1",
        "target": {"instance_id": "svc-1"},
        "evidence_refs": [],
        "log": {"log_files": 0, "error_count": 0, "patterns": {}, "levels": {}},
    }
    assert analyze_observations([observation]) == []


def test_collector_no_logs_returns_empty_artifact(tmp_path, monkeypatch):
    # 不存在的 PID → 发现不到日志 → 返回空 artifact（不算失败）
    collector = LogScanCollector()
    result = collector.collect(type("T", (), {
        "id": "scan-log-1", "target_pid": 999999, "sample_rate": 1,
        "duration_sec": 2, "options": {},
    })())
    assert result.ok
    assert result.artifacts[0]["artifact_type"] == "log_scan"
    payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "log_scan.v1"
    assert payload["log_files"] == []
