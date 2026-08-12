from __future__ import annotations

from copy import deepcopy

from server.app.diagnosis.audit_trace import (
    build_audit_bundle,
    build_trace_step,
    verify_trace_chain,
)


def test_trace_hash_chain_detects_modified_decision() -> None:
    first = build_trace_step(
        diagnosis_id="diag-1", sequence=1, stage="intent", component="parser",
        decision="normalize", summary="CPU saturation", output_refs=["intent"],
    )
    second = build_trace_step(
        diagnosis_id="diag-1", sequence=2, stage="scope", component="resolver",
        decision="select_instance", summary="Selected one instance",
        input_refs=["intent"], output_refs=["instance-1"], previous_hash=first["step_hash"],
    )

    assert verify_trace_chain([first, second])["status"] == "passed"
    modified = deepcopy(second)
    modified["summary"] = "Changed after the run"
    result = verify_trace_chain([first, modified])
    assert result["status"] == "failed"
    assert any("step hash mismatch" in issue for issue in result["issues"])


def test_legacy_bundle_is_explicitly_reconstructed_and_oracle_is_private() -> None:
    detail = {
        "diagnosis_id": "diag-old",
        "status": "COMPLETED",
        "updated_at": "2026-08-11T00:00:00+00:00",
        "normalized_intent": {"symptom": "cpu_saturation"},
        "target_scope": {"instances": [{"instance_id": "svc-1"}]},
        "hypothesis_graph": {"hypotheses": []},
        "probes": [],
        "evidence": [],
        "events": [],
        "latest_conclusion": {
            "summary": "CPU pressure",
            "findings": [],
            "actions": [],
            "root_location": {"type": "self", "target_ref": "svc-1"},
            "domain_cause": {"type": "cpu"},
            "cluster_assessment": {"classification": "self_code_or_process_pressure"},
            "verification": {"status": "passed"},
            "evaluation": {"checks": [{"expected": "self", "actual": "self"}]},
        },
        "evaluation_oracle": {"case_id": "hidden-case"},
    }

    public = build_audit_bundle(detail)
    private = build_audit_bundle(detail, include_oracle=True)

    assert public["trace_verification"]["status"] == "passed"
    assert public["trace_verification"]["runtime_step_count"] == 0
    assert public["trace_verification"]["reconstructed_step_count"] > 0
    assert "evaluation_oracle" not in public
    assert "evaluation" not in public["conclusion"]
    assert private["evaluation_oracle"]["case_id"] == "hidden-case"
    assert private["conclusion"]["evaluation"]["checks"][0]["expected"] == "self"
