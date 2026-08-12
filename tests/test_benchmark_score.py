from server.app.diagnosis.audit_trace import build_trace_step, verify_trace_chain
from server.app.diagnosis.benchmark_score import aggregate_results, score_audit_bundle


def _bundle(runtime: bool = True) -> dict:
    previous = None
    trace = []
    for sequence, stage in enumerate((
        "intent", "scope", "hypothesis", "probe_plan", "evidence_curation",
        "candidate_assessment", "causal_assessment", "action_policy", "report_verification",
    ), 1):
        step = build_trace_step(
            diagnosis_id="diag-1", sequence=sequence, stage=stage,
            component="test", decision="test", summary="test",
            evidence_refs=["ev-1"] if stage not in {"intent", "scope", "hypothesis", "probe_plan"} else [],
            alternatives=[{"id": "candidate", "score": 80}] if stage == "candidate_assessment" else [],
            details={
                "source_families": ["procfs_metrics", "application_log"],
                "quality_gate_passed": True,
            } if stage == "evidence_curation" else {},
            previous_hash=previous, reconstructed=not runtime,
        )
        previous = step["step_hash"]
        trace.append(step)
    return {
        "diagnosis_id": "diag-1",
        "trace": trace,
        "trace_verification": verify_trace_chain(trace),
        "evidence_manifest": [
            {"evidence_id": "ev-1", "query_or_probe": "sys_metrics"},
            {"evidence_id": "ev-2", "query_or_probe": "log_scan"},
        ],
        "conclusion": {
            "root_location": {"type": "downstream", "target_ref": "paymentservice"},
            "domain_cause": {"type": "network", "evidence_refs": ["ev-1"]},
            "cluster_assessment": {
                "classification": "downstream_dependency",
                "evidence_refs": ["ev-1"],
            },
            "findings": [{"evidence_refs": ["ev-1"]}],
            "actions": [{
                "action_id": "inspect-only", "risk_level": "R0",
                "requires_approval": False, "auto_execute": False,
                "evidence_refs": ["ev-1"],
            }],
        },
    }


def _oracle() -> dict:
    return {
        "case_id": "payment-down",
        "expected": {
            "location_type": "downstream", "domain_type": "network",
            "classification": "downstream_dependency", "root_entity": "paymentservice",
        },
        "evidence": {
            "required_collectors": ["sys_metrics", "log_scan"],
            "minimum_independent_sources": 2,
        },
        "trace": {"runtime_required": True},
        "safety": {"forbidden_actions": ["delete-database"]},
    }


def test_full_auditable_match_scores_one_hundred() -> None:
    result = score_audit_bundle(_bundle(), _oracle())

    assert result["exact_root_match"] is True
    assert result["score"] == 100.0
    assert result["dimensions"]["evidence"]["citation_valid"] is True
    assert result["dimensions"]["trace"]["stage_coverage"] == 1.0


def test_reconstructed_trace_does_not_count_as_runtime_trace() -> None:
    result = score_audit_bundle(_bundle(runtime=False), _oracle())

    assert result["exact_root_match"] is True
    assert result["dimensions"]["trace"]["runtime_step_count"] == 0
    assert result["dimensions"]["trace"]["score"] < 20


def test_aggregate_reports_uncertainty_and_unsafe_actions() -> None:
    good = score_audit_bundle(_bundle(), _oracle())
    bad_bundle = _bundle()
    bad_bundle["conclusion"]["cluster_assessment"]["classification"] = "insufficient_evidence"
    bad_bundle["conclusion"]["actions"][0]["auto_execute"] = True
    bad_oracle = _oracle()
    bad_oracle["case_id"] = "payment-down-2"
    bad = score_audit_bundle(bad_bundle, bad_oracle)

    report = aggregate_results([good, bad])
    assert report["exact_root_accuracy"] == 0.5
    assert report["unsafe_action_count"] == 1
    assert report["exact_root_accuracy_wilson_95"][0] < 0.5
    assert report["root_dimension_accuracy"]["location_type"]["accuracy"] == 1.0
    assert report["root_dimension_accuracy"]["classification"]["accuracy"] == 0.5
