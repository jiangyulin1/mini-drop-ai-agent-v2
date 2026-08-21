"""Unit tests for the live Pi strategy-matrix report helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "run_agent_strategy_matrix",
    ROOT / "scripts" / "run_agent_strategy_matrix.py",
)
matrix_script = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(matrix_script)

_PI_SPEC = importlib.util.spec_from_file_location(
    "run_pi_agent_eval",
    ROOT / "scripts" / "run_pi_agent_eval.py",
)
pi_script = importlib.util.module_from_spec(_PI_SPEC)
assert _PI_SPEC.loader is not None
_PI_SPEC.loader.exec_module(pi_script)


def test_sum_model_attempts_aggregates_tokens_cost_and_latency():
    attempts = [
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 5,
            "cache_write_tokens": 2,
            "cost": 0.001,
            "latency_ms": 300,
        },
        {
            "input_tokens": 200,
            "output_tokens": 40,
            "cache_read_tokens": 10,
            "cache_write_tokens": 4,
            "cost": 0.002,
            "latency_ms": 700,
        },
    ]
    summary = matrix_script._sum_model_attempts(attempts)
    assert summary["attempt_count"] == 2
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 60
    assert summary["cache_read_tokens"] == 15
    assert summary["cache_write_tokens"] == 6
    assert summary["cost"] == 0.003
    assert summary["latency_ms"] == 1000


def test_offline_matrix_does_not_claim_strategy_accuracy():
    matrix, errors = matrix_script.load_and_validate(
        ROOT / "benchmarks" / "agent_experiments" / "matrix.json",
    )
    assert errors == []
    report = matrix_script.run_matrix(
        matrix,
        ROOT / "benchmarks" / "agent_experiments" / "matrix.json",
    )
    assert report["adapter"] == "offline_rules_control_only"
    assert all(row["strategy_applied"] is False for row in report["conditions"])
    assert all(row["scenario_pass_rate"] is None for row in report["conditions"])
    assert all(row["root_cause_accuracy"] is None for row in report["conditions"])
    assert all(row["evidence_citation_validity"] is None for row in report["conditions"])
    assert all(row["tool_call_count"] is None for row in report["conditions"])
    assert all(row["control_group_root_cause_accuracy"] is not None for row in report["conditions"])
    assert all(
        row["runtime_options"]["runtime_support"]["strategy"] == "not_applied_offline_control"
        for row in report["conditions"]
    )


def test_blind_eval_prompt_does_not_expose_private_fault_label():
    symptom = pi_script.symptom_for_fault("cpu-hotspot")
    assert "cpu-hotspot" not in symptom
    assert "CPU" in symptom
    with pytest.raises(ValueError, match="unsupported blind-evaluation fault"):
        pi_script.symptom_for_fault("private-new-fault")


def test_start_fault_hides_private_label_from_target_cmdline(monkeypatch):
    commands = []
    monkeypatch.setattr(pi_script, "ssh_run", lambda *args, **kwargs: commands.append(args[3]) or "4321")
    monkeypatch.setattr(pi_script.time, "sleep", lambda _seconds: None)

    assert pi_script.start_fault("worker", "root", "secret", "cpu-hotspot", 30) == 4321
    launch = commands[1]
    assert "--setenv=MINI_DROP_EVAL_SCENARIO=cpu-hotspot" in launch
    target_argv = launch.split("exec python3", 1)[1]
    assert "cpu-hotspot" not in target_argv
    assert "--inject-fault-env MINI_DROP_EVAL_SCENARIO" in target_argv


@pytest.mark.parametrize(
    ("fault", "answer", "expected"),
    [
        ("cpu-hotspot", "CPU 使用率很高，但尚未定位原因", False),
        ("cpu-hotspot", "根因是用户态忙循环造成 CPU 热点", True),
        ("memory-leak", "RSS 持续增长，根因是内存泄漏", True),
        ("memory-leak", "RSS 持续增长，但尚未定位原因", False),
        ("io-write", "根因是应用持续写入造成 I/O 压力", True),
        ("io-write", "磁盘写入和 I/O 等待明显升高", False),
        ("lock-contend", "线程阻塞源于锁竞争", True),
    ],
)
def test_fault_specific_root_cause_score(fault, answer, expected):
    result = pi_script.score([], answer, fault)
    assert result["final_answer_mentions_fault"] is expected


def test_evidence_citations_require_active_projected_refs_and_server_verifier():
    conclusion = {
        "evidence_refs": ["ev-1"],
        "verifier": "causal-report-verifier.v1",
    }
    evidence = [{"evidence_id": "ev-1", "status": "ACTIVE", "projection_hash": "abc"}]
    assert pi_script.score_evidence_citations(conclusion, evidence)["score"] == 1.0

    invalid = pi_script.score_evidence_citations(
        {**conclusion, "evidence_refs": ["ev-missing"]}, evidence,
    )
    assert invalid["score"] == 0.0
    assert invalid["invalid_refs"] == ["ev-missing"]


def test_live_score_uses_dispatched_collectors_not_pi_gateway_tool_names():
    result = pi_script.score(
        ["process_scan", "runtime_snapshot", "sys_metrics"],
        "根因是用户态忙循环造成 CPU 热点",
        "cpu-hotspot",
    )
    assert result["tool_recall"] == 0.667
    assert result["used_relevant_tools"] == ["process_scan", "sys_metrics"]
