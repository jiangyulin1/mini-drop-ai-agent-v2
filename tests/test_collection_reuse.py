"""Pure invariants for the physical collection reuse contract."""

from __future__ import annotations

from server.app.diagnosis.collection_reuse import (
    canonical_probe_key_identity,
    normalize_probe_request,
    evaluate_reuse_candidate,
    probe_key_fingerprint,
    select_reuse_candidate,
)


def _probe(*, target: dict | None = None, parameters: dict | None = None) -> str:
    return probe_key_fingerprint(
        case_id="case-1",
        tenant_id="tenant-a",
        collector_id="sys_metrics",
        collector_spec_version="1.0",
        collector_implementation_version="native.v1",
        target=target or {
            "agent_id": "agent-a",
            "target_pid": 42,
            "boot_id": "boot-a",
            "process_start_time": "100",
        },
        parameters=parameters or {"duration_sec": 1},
    )


def test_probe_identity_normalizes_numeric_and_selector_aliases():
    base = _probe(parameters={"duration_sec": 1})
    equivalent = _probe(parameters={"duration_sec": 1.0})
    assert base == equivalent

    target_with_both_aliases = {
        "agent_id": "agent-a",
        "target_pid": 42,
        "pid": 42,
        "boot_id": "boot-a",
        "process_start_time": "100",
    }
    assert _probe(target=target_with_both_aliases) == base


def test_unknown_review_trust_state_is_not_reusable():
    decision = evaluate_reuse_candidate(
        {
            "collection_request_id": "req-1",
            "task_id": "task-1",
            "status": "COMPLETED",
            "reuse_metadata": {
                "probe_fingerprint": "probe-1",
                "result_fingerprint": "result-1",
                "evidence_status": "ACTIVE",
                "evidence_lifecycle_status": "ACTIVE",
                "review_trust_state": "EXCLUDED",
            },
        },
        requested_probe_fingerprint="probe-1",
        requested_result_fingerprint="result-1",
    )
    assert decision["reusable"] is False
    assert decision["reason_codes"] == ["EVIDENCE_TRUST_STATE_NOT_REUSABLE"]


def test_legacy_candidate_without_governance_snapshot_fails_closed():
    decision = evaluate_reuse_candidate(
        {
            "collection_request_id": "req-legacy",
            "task_id": "task-legacy",
            "status": "COMPLETED",
            "reuse_metadata": {
                "probe_fingerprint": "probe-legacy",
                "result_fingerprint": "result-legacy",
            },
        },
        requested_probe_fingerprint="probe-legacy",
        requested_result_fingerprint="result-legacy",
    )
    assert decision["reusable"] is False
    assert {
        "EVIDENCE_NOT_ACTIVE",
        "EVIDENCE_STATUS_NOT_REUSABLE",
        "EVIDENCE_TRUST_STATE_NOT_REUSABLE",
    }.issubset(decision["reason_codes"])


def test_target_alias_normalization_does_not_change_identity_shape():
    identity = canonical_probe_key_identity(
        case_id="case-1",
        tenant_id="tenant-a",
        collector_id="sys_metrics",
        collector_spec_version="1.0",
        collector_implementation_version="native.v1",
        target={"agent_id": "agent-a", "target_pid": 42, "pid": 42},
        parameters={"duration_sec": 1},
    )
    assert identity["target"] == {"agent_id": "agent-a", "target_pid": 42}


def _normalized_request() -> dict:
    return normalize_probe_request(
        case_id="case-1",
        tenant_id="tenant-a",
        collector_id="sys_metrics",
        collector_spec_version="1.0",
        collector_implementation_version="native.v1",
        target={
            "agent_id": "agent-a", "target_pid": 42,
            "boot_id": "boot-a", "process_start_time": "100",
        },
        parameters={"duration_sec": 1},
        time_window={"start": "2026-01-01T00:00:00Z"},
        scope_revision=1,
    )


def _candidate(request: dict, *, evidence_id: str, trust: str = "TRUSTED") -> dict:
    return {
        "collection_request_id": f"req-{evidence_id}",
        "evidence_id": evidence_id,
        "status": "COMPLETED",
        "reuse_metadata": {
            "probe_key": request["probe_key"],
            "probe_fingerprint": request["probe_fingerprint"],
            "result_fingerprint": "result-1",
            "evidence_status": "ACTIVE",
            "evidence_lifecycle_status": "ACTIVE",
            "review_trust_state": trust,
            "freshness": "FRESH",
            "completeness": "COMPLETE",
        },
    }


def test_selection_hard_gates_before_score_and_is_deterministic():
    request = _normalized_request()
    candidate = _candidate(request, evidence_id="ev-1")
    selected = select_reuse_candidate(request, [candidate])
    assert selected["decision"] == "REUSED"
    assert selected["selected"]["candidate"]["evidence_id"] == "ev-1"
    assert selected["selected"]["hard_gate"] == "PASS"

    mismatched = _candidate(request, evidence_id="ev-2")
    mismatched["reuse_metadata"]["probe_key"] = "other-key"
    result = select_reuse_candidate(request, [mismatched])
    assert result["decision"] == "RECOLLECT_REQUIRED"
    assert "PROBE_KEY_MISMATCH" in result["reason_codes"]


def test_selection_reports_close_valid_candidates_as_ambiguous():
    request = _normalized_request()
    first = _candidate(request, evidence_id="ev-a", trust="TRUSTED")
    second = _candidate(request, evidence_id="ev-b", trust="TRUSTED")
    result = select_reuse_candidate(request, [second, first], tie_delta=0.03)
    assert result["decision"] == "REUSE_AMBIGUOUS"
    assert result["selected"] is None
