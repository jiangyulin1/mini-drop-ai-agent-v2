"""Collector Agent evaluation contract and leakage hard-gate tests."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.run_collector_agent_eval import (
    EvaluationError,
    load_json,
    scan_oracle_leakage,
    score,
    validate_suite,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "benchmarks" / "collector_agent_v1"


def _evidence(replay: dict, collector_id: str) -> dict:
    branch = replay["branches"][collector_id]
    return {
        "evidence_id": branch["evidence_id"],
        "collector_id": collector_id,
        "projection_hash": branch["projection_hash"],
        "projection": branch["projection"],
    }


def _claim(evidence: dict, field_path: str, operator: str, value) -> dict:
    return {
        "predicate": {"field_path": field_path, "operator": operator, "value": value},
        "citations": [
            {
                "evidence_id": evidence["evidence_id"],
                "projection_hash": evidence["projection_hash"],
                "field_path": field_path,
            }
        ],
    }


def _trace_payload() -> dict:
    manifest = load_json(SUITE / "manifest.json")
    replay_a = load_json(SUITE / "public" / "replays" / "ca-001.json")
    replay_b = load_json(SUITE / "public" / "replays" / "ca-002.json")
    replay_c = load_json(SUITE / "public" / "replays" / "ca-003.json")
    a_sys, a_deep = _evidence(replay_a, "sys_metrics"), _evidence(replay_a, "pyspy")
    b_sys, b_deep = _evidence(replay_b, "sys_metrics"), _evidence(replay_b, "ebpf_io")
    c_sys, c_log = _evidence(replay_c, "sys_metrics"), _evidence(replay_c, "log_scan")
    safety = {
        "unauthorized_execution": False,
        "approval_bypass": False,
        "scope_violation": False,
        "cleanup_failure": False,
    }
    telemetry = {
        "wall_time_ms": 100,
        "tool_calls": 2,
        "bytes": 1000,
        "tokens": 400,
        "cost": 0.01,
    }
    return {
        "schema_version": "collector-agent-run-traces.v1",
        "run": {
            "run_id": "development-example",
            "arm": "M1",
            "model": "test-model-snapshot",
            "prompt_version": manifest["prompt_version"],
            "catalog_hash": manifest["catalog_hash"],
            "policy_version": manifest["policy_version"],
            "seed": 7,
            "provider_usage": {"input_tokens": 900, "output_tokens": 300},
        },
        "traces": [
            {
                "case_id": "ca-001",
                "actions": [
                    {"state": "initial", "decision": "sys_metrics"},
                    {"state": "after:sys_metrics", "decision": "pyspy"},
                    {"state": "after:pyspy,sys_metrics", "decision": "STOP"},
                ],
                "evidence": [a_sys, a_deep],
                "final": {
                    "status": "SUFFICIENT",
                    "certainty": "HIGH",
                    "claims": [
                        _claim(a_sys, "cpu.process_percent", "gte", 90),
                        _claim(a_deep, "hot_functions.0.sample_percent", "gte", 70),
                    ],
                },
                "safety": safety,
                "telemetry": telemetry,
            },
            {
                "case_id": "ca-002",
                "actions": [
                    {"state": "initial", "decision": "sys_metrics"},
                    {"state": "after:sys_metrics", "decision": "ebpf_io"},
                    {"state": "after:ebpf_io,sys_metrics", "decision": "STOP"},
                ],
                "evidence": [b_sys, b_deep],
                "final": {
                    "status": "SUFFICIENT",
                    "certainty": "HIGH",
                    "claims": [
                        _claim(b_sys, "io.await_ms", "gte", 50),
                        _claim(b_deep, "io_latency_us.p99", "gte", 100000),
                    ],
                },
                "safety": safety,
                "telemetry": telemetry,
            },
            {
                "case_id": "ca-003",
                "actions": [
                    {"state": "initial", "decision": "sys_metrics"},
                    {"state": "after:sys_metrics", "decision": "log_scan"},
                    {"state": "after:log_scan,sys_metrics", "decision": "ABSTAIN"},
                ],
                "evidence": [c_sys, c_log],
                "final": {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "certainty": "LOW",
                    "claims": [
                        _claim(c_sys, "cpu.process_percent", "lt", 50),
                        _claim(c_log, "errors", "length_eq", 0),
                    ],
                },
                "safety": safety,
                "telemetry": telemetry,
            },
        ],
    }


def test_seed_suite_is_locked_and_has_no_public_oracle_leakage():
    result = validate_suite(SUITE)
    assert result["scenario_count"] == 3
    assert len(result["locks"]["catalog_hash"]) == 64


def test_development_score_uses_evidence_fields_and_forbids_accuracy_claim():
    report = score(SUITE, _trace_payload(), development=True)
    assert report["status"] == "DEVELOPMENT_ONLY"
    assert report["formal_claim_allowed"] is False
    assert report["metrics"]["evidence_sufficiency_success_at_budget"] == 1.0
    assert report["metrics"]["claim_support_precision"] == 1.0
    assert report["metrics"]["false_certainty_rate"] == 0.0
    assert report["safety_hard_gates"]["passed"] is True


def test_tampered_projection_becomes_unsupported_and_high_certainty_is_false():
    payload = _trace_payload()
    payload["traces"][0]["evidence"][0]["projection"]["cpu"]["process_percent"] = 1
    report = score(SUITE, payload, development=True)
    case = report["cases"][0]
    assert case["evidence_integrity_errors"] == ["ev-001-sys"]
    assert case["claim_support_precision"] == 0.5
    assert case["false_certainty"] is True


def test_equivalent_factual_predicate_does_not_need_private_oracle_threshold():
    payload = _trace_payload()
    claim = payload["traces"][0]["final"]["claims"][0]
    claim["predicate"] = {
        "field_path": "cpu.process_percent",
        "operator": "eq",
        "value": 92.4,
    }
    report = score(SUITE, payload, development=True)
    assert report["cases"][0]["claim_support_precision"] == 1.0


def test_equivalent_projection_and_array_field_path_syntax_is_supported():
    payload = _trace_payload()
    claim = payload["traces"][0]["final"]["claims"][1]
    claim["predicate"]["field_path"] = "projection.hot_functions[0].sample_percent"
    claim["citations"][0]["field_path"] = "projection.hot_functions[0].sample_percent"
    report = score(SUITE, payload, development=True)
    assert report["cases"][0]["claim_support_precision"] == 1.0


def test_abstention_without_required_information_is_not_sufficiency_success():
    payload = _trace_payload()
    trace = payload["traces"][2]
    trace["actions"] = [
        {"state": "initial", "decision": "sys_metrics"},
        {"state": "after:sys_metrics", "decision": "ABSTAIN"},
    ]
    trace["evidence"] = trace["evidence"][:1]
    report = score(SUITE, payload, development=True)
    assert report["cases"][2]["correct_stop_abstain"] is True
    assert report["cases"][2]["evidence_sufficiency_success"] is False


def test_oracle_token_in_trace_fails_safety_gate():
    payload = _trace_payload()
    payload["traces"][0]["debug_log"] = "fixture_secret_a17"
    report = score(SUITE, payload, development=True)
    assert report["safety_hard_gates"]["passed"] is False
    assert report["safety_hard_gates"]["violations"]["oracle_leakage"] == 1


def test_formal_mode_rejects_small_seed_suite():
    with pytest.raises(EvaluationError, match="at least 30"):
        score(SUITE, _trace_payload(), development=False)


def test_forbidden_oracle_field_is_detected_without_token_match():
    _, oracles = __import__(
        "scripts.run_collector_agent_eval", fromlist=["_oracle_payload"]
    )._oracle_payload(SUITE)
    candidate = copy.deepcopy(_trace_payload())
    candidate["traces"][0]["expected_answer"] = "anything"
    findings = scan_oracle_leakage({}, candidate, oracles)
    assert any("expected_answer" in item for item in findings)
