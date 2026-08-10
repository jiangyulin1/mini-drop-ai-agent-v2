"""Deterministic, budgeted context preparation for AI calls."""

import json

from server.app.ai_context import (
    CASE_CONTEXT_FIELDS,
    ContextBudget,
    optimize_case_context_packet,
    optimize_evidence_context,
)
from server.app.rca.evidence import evidence_to_json
from server.app.rca.models import EvidenceInput


def test_complex_metrics_are_compacted_without_mutating_raw_input():
    raw = {
        "task_metadata": {"task_id": "t1", "api_key": "secret-value"},
        "sys_metrics": {
            "summary": {"avg_cpu_user_pct": 80.0},
            "samples": [
                {"ts": index, "host": {"cpu": float(index), "rss": 100 + index}}
                for index in range(100)
            ],
        },
    }

    optimized = optimize_evidence_context(raw)

    assert len(raw["sys_metrics"]["samples"]) == 100
    assert optimized.payload["task_metadata"]["api_key"] == "[REDACTED]"
    projection = optimized.payload["sys_metrics"]["sample_projection"]
    assert projection["original_sample_count"] == 100
    assert projection["numeric_series"]["host.cpu"] == {
        "count": 100,
        "min": 0,
        "max": 99,
        "avg": 49.5,
        "last": 99,
        "slope_per_sample": 1,
    }
    assert "samples" not in optimized.payload["sys_metrics"]
    assert optimized.stats.metric_samples_compacted == 100


def test_events_are_deduplicated_and_failure_signal_is_prioritized():
    events = ["heartbeat ok"] * 20 + ["request completed"] * 20 + ["FATAL database timeout"]
    result = optimize_evidence_context(
        {"failure_events": events},
        budget=ContextBudget(max_chars=4_000, log_events=2),
    )

    assert result.payload["failure_events"][0] == "FATAL database timeout"
    assert len(result.payload["failure_events"]) == 2
    assert result.stats.duplicate_items_removed == 38


def test_context_respects_hard_budget_and_reports_projection_metadata():
    raw = {
        "task_metadata": {"task_id": "t1"},
        "tool_results": [
            {"tool_name": f"tool-{index}", "output": {"log": "x" * 2_000}}
            for index in range(50)
        ],
        "suggestions": ["y" * 2_000 for _ in range(30)],
    }
    result = optimize_evidence_context(raw, budget=ContextBudget(max_chars=4_000))
    encoded = json.dumps(result.payload, ensure_ascii=False, separators=(",", ":"))

    assert len(encoded) <= 4_000
    assert result.payload["_context_meta"]["projection_only"] is True
    assert result.payload["_context_meta"]["raw_evidence_unchanged"] is True
    assert result.stats.original_chars > result.stats.optimized_chars


def test_rca_evidence_serialization_uses_optimizer_and_keeps_reference_paths():
    evidence = EvidenceInput(
        task_metadata={"task_id": "t1", "password": "do-not-send"},
        top_functions=[
            {"name": f"fn-{index}", "percent": float(index)}
            for index in range(30)
        ],
        sys_metrics={"samples": [{"cpu": index} for index in range(50)]},
    )
    payload = json.loads(evidence_to_json(evidence))

    assert payload["task_metadata"]["password"] == "[REDACTED]"
    assert payload["top_functions"][0]["name"] == "fn-29"
    assert len(payload["top_functions"]) == 12
    assert payload["sys_metrics"]["sample_projection"]["original_sample_count"] == 50
    assert payload["_context_meta"]["do_not_cite_as_evidence"] is True


def test_case_context_preserves_contract_fields_redacts_and_fits_budget():
    payload = {
        "schema_version": "case-context.v1",
        "case_goal": {
            "problem_description": "checkout timeout",
            "recovery_goal": "recover",
            "run_mode": "COLLABORATE",
        },
        "scope": {"service_id": "checkout", "api_key": "must-not-leak"},
        "current_iteration": 3,
        "active_hypotheses": [{"id": index, "text": "h" * 300} for index in range(30)],
        "evidence_manifest": [{"id": index, "quality": "high"} for index in range(100)],
        "signal_projection": [{"id": index, "log": "x" * 1000} for index in range(100)],
        "contradictions": [],
        "missing_evidence": [],
        "knowledge_refs": [],
        "current_understanding": {
            "target": "", "symptom": "", "understanding": "不可判断",
            "confirmed": [], "contradictions": [], "missing": [],
            "missing_domains": [], "candidate_gap_proposals": [],
            "next": "", "source": "programmatic", "updated_at": "",
        },
        "recent_decisions": [],
        "recent_changes": [],
        "policy_capabilities": [],
        "budget_remaining": {"model_calls": 2},
        "required_output_schema": "next-investigation-action.v1",
    }
    result = optimize_case_context_packet(payload, budget=ContextBudget(max_chars=4_000))
    encoded = json.dumps(result.payload, ensure_ascii=False, separators=(",", ":"))

    assert tuple(result.payload) == CASE_CONTEXT_FIELDS
    assert result.payload["scope"]["api_key"] == "[REDACTED]"
    assert len(encoded) <= 4_000
    assert result.stats.original_chars > result.stats.optimized_chars
