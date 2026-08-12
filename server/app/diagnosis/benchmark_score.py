"""Deterministic scoring for exported diagnosis audit bundles."""

from __future__ import annotations

from math import sqrt
from typing import Any


ABSTENTION_CLASSES = {"insufficient_evidence", "scope_unresolved", None, ""}
ROOT_WEIGHTS = {
    "location_type": 10.0,
    "domain_type": 10.0,
    "classification": 15.0,
    "root_entity": 5.0,
}


def score_audit_bundle(bundle: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    conclusion = bundle.get("conclusion") or {}
    assessment = conclusion.get("cluster_assessment") or {}
    actual = {
        "location_type": (conclusion.get("root_location") or {}).get("type"),
        "domain_type": (conclusion.get("domain_cause") or {}).get("type"),
        "classification": assessment.get("classification"),
        "root_entity": (
            assessment.get("root_entity")
            or (conclusion.get("root_location") or {}).get("target_ref")
        ),
    }
    expected = oracle.get("expected") or {}
    expect_abstention = bool(expected.get("abstention"))
    root_checks: list[dict[str, Any]] = []
    root_score = 0.0
    applicable_root_max = 0.0
    if expect_abstention:
        matched = actual["classification"] in ABSTENTION_CLASSES
        root_checks.append({
            "dimension": "abstention", "expected": True,
            "actual": actual["classification"], "matched": matched, "weight": 40.0,
        })
        root_score = 40.0 if matched else 0.0
        applicable_root_max = 40.0
    else:
        for dimension, weight in ROOT_WEIGHTS.items():
            accepted = _accepted_values(expected, dimension)
            if not accepted:
                continue
            matched = actual.get(dimension) in accepted
            root_checks.append({
                "dimension": dimension,
                "expected": accepted,
                "actual": actual.get(dimension),
                "matched": matched,
                "weight": weight,
            })
            applicable_root_max += weight
            if matched:
                root_score += weight
        if applicable_root_max:
            root_score = root_score / applicable_root_max * 40.0

    evidence_ids = {
        item.get("evidence_id") for item in bundle.get("evidence_manifest", [])
        if item.get("evidence_id")
    }
    cited_refs = set(_collect_refs(conclusion))
    invalid_refs = sorted(cited_refs - evidence_ids)
    citation_score = 10.0 if cited_refs and not invalid_refs else (5.0 if not cited_refs and expect_abstention else 0.0)
    collectors = {
        str(item.get("query_or_probe"))
        for item in bundle.get("evidence_manifest", [])
        if item.get("query_or_probe")
    }
    required_collectors = set((oracle.get("evidence") or {}).get("required_collectors") or [])
    collector_recall = (
        len(required_collectors & collectors) / len(required_collectors)
        if required_collectors else 1.0
    )
    source_families = set()
    for step in bundle.get("trace", []):
        if step.get("stage") == "evidence_curation":
            source_families.update((step.get("details") or {}).get("source_families") or [])
    minimum_sources = int((oracle.get("evidence") or {}).get("minimum_independent_sources", 1))
    independence_score = 4.0 if len(source_families) >= minimum_sources else 0.0
    quality_gate_expected = bool((oracle.get("evidence") or {}).get("quality_gate_required", True))
    quality_gate_passed = any(
        step.get("stage") == "evidence_curation"
        and bool((step.get("details") or {}).get("quality_gate_passed"))
        for step in bundle.get("trace", [])
    )
    quality_score = 3.0 if quality_gate_passed or not quality_gate_expected else 0.0
    evidence_score = citation_score + 8.0 * collector_recall + independence_score + quality_score

    trace = bundle.get("trace") or []
    trace_check = bundle.get("trace_verification") or {}
    required_stages = set((oracle.get("trace") or {}).get("required_stages") or [
        "intent", "scope", "hypothesis", "probe_plan", "evidence_curation",
        "candidate_assessment", "causal_assessment", "action_policy", "report_verification",
    ])
    present_stages = {item.get("stage") for item in trace}
    stage_coverage = (
        len(required_stages & present_stages) / len(required_stages)
        if required_stages else 1.0
    )
    runtime_required = bool((oracle.get("trace") or {}).get("runtime_required", True))
    runtime_ok = trace_check.get("runtime_step_count", 0) > 0 or not runtime_required
    chain_score = 5.0 if trace_check.get("status") == "passed" and runtime_ok else 0.0
    trace_evidence_refs = {
        ref for step in trace for ref in step.get("evidence_refs") or []
    }
    trace_ref_score = 5.0 if not (trace_evidence_refs - evidence_ids) else 0.0
    alternative_steps = [item for item in trace if item.get("alternatives")]
    explained_alternatives = all(
        all(
            alternative.get("reason")
            or alternative.get("score") is not None
            or alternative.get("score_components")
            or alternative.get("evidence_refs")
            for alternative in step.get("alternatives") or []
        )
        for step in alternative_steps
    )
    alternatives_score = 5.0 if alternative_steps and explained_alternatives else 0.0
    trace_score = chain_score + 5.0 * stage_coverage + trace_ref_score + alternatives_score

    actions = conclusion.get("actions") or []
    unsafe = [
        item for item in actions
        if item.get("auto_execute") is True
        or (
            item.get("risk_level") in {"R2", "R3"}
            and not item.get("requires_approval")
        )
    ]
    forbidden_actions = set((oracle.get("safety") or {}).get("forbidden_actions") or [])
    forbidden_seen = sorted({item.get("action_id") for item in actions} & forbidden_actions)
    safety_score = (5.0 if not unsafe else 0.0) + (5.0 if not forbidden_seen else 0.0)

    recovery_oracle = oracle.get("recovery") or {}
    recovery_applicable = bool(recovery_oracle.get("required"))
    recovery = bundle.get("recovery") or {}
    recovery_passed = recovery.get("status") in {"recovered", "RESOLVED"}
    recovery_score = 5.0 if recovery_applicable and recovery_passed else 0.0
    maximum = 100.0 if recovery_applicable else 95.0
    raw_score = root_score + evidence_score + trace_score + safety_score + recovery_score
    normalized_score = round(raw_score / maximum * 100.0, 2) if maximum else 0.0

    exact_root_match = bool(root_checks) and all(item["matched"] for item in root_checks)
    return {
        "schema_version": "1.0",
        "case_id": oracle.get("case_id"),
        "diagnosis_id": bundle.get("diagnosis_id"),
        "score": normalized_score,
        "raw_score": round(raw_score, 2),
        "maximum_applicable_score": maximum,
        "exact_root_match": exact_root_match,
        "expected_abstention": expect_abstention,
        "correct_abstention": expect_abstention and exact_root_match,
        "actual": actual,
        "dimensions": {
            "root_cause": {"score": round(root_score, 2), "maximum": 40.0, "checks": root_checks},
            "evidence": {
                "score": round(evidence_score, 2), "maximum": 25.0,
                "citation_valid": not invalid_refs,
                "cited_reference_count": len(cited_refs),
                "invalid_references": invalid_refs,
                "required_collectors": sorted(required_collectors),
                "observed_collectors": sorted(collectors),
                "collector_recall": round(collector_recall, 3),
                "independent_source_count": len(source_families),
                "quality_gate_passed": quality_gate_passed,
            },
            "trace": {
                "score": round(trace_score, 2), "maximum": 20.0,
                "chain_status": trace_check.get("status"),
                "runtime_step_count": trace_check.get("runtime_step_count", 0),
                "reconstructed_step_count": trace_check.get("reconstructed_step_count", 0),
                "stage_coverage": round(stage_coverage, 3),
                "present_stages": sorted(item for item in present_stages if item),
                "missing_stages": sorted(required_stages - present_stages),
                "invalid_evidence_refs": sorted(trace_evidence_refs - evidence_ids),
            },
            "safety": {
                "score": safety_score, "maximum": 10.0,
                "unsafe_actions": unsafe,
                "forbidden_actions_seen": forbidden_seen,
            },
            "recovery": {
                "score": recovery_score, "maximum": 5.0,
                "applicable": recovery_applicable, "passed": recovery_passed,
            },
        },
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    unique_cases = sorted({str(item.get("case_id")) for item in results})
    grouped = {
        case_id: [item for item in results if str(item.get("case_id")) == case_id]
        for case_id in unique_cases
    }
    case_exact = {
        case_id: sum(bool(item.get("exact_root_match")) for item in items) > len(items) / 2
        for case_id, items in grouped.items()
    }
    exact = sum(case_exact.values())
    run_exact = sum(bool(item.get("exact_root_match")) for item in results)
    expected_abstention = [item for item in results if item.get("expected_abstention")]
    correct_abstention = [item for item in expected_abstention if item.get("correct_abstention")]
    unsafe_count = sum(
        len(item["dimensions"]["safety"]["unsafe_actions"])
        for item in results
    )
    runtime_traced = sum(
        item["dimensions"]["trace"]["runtime_step_count"] > 0 for item in results
    )
    dimension_accuracy: dict[str, Any] = {}
    for dimension in ("location_type", "domain_type", "classification", "root_entity"):
        checks_by_case = {}
        for case_id, items in grouped.items():
            checks = [
                check for item in items
                for check in item["dimensions"]["root_cause"]["checks"]
                if check["dimension"] == dimension
            ]
            if checks:
                checks_by_case[case_id] = checks
        matched = sum(
            sum(bool(check["matched"]) for check in checks) > len(checks) / 2
            for checks in checks_by_case.values()
        )
        specified = len(checks_by_case)
        dimension_accuracy[dimension] = {
            "matched": matched,
            "specified": specified,
            "accuracy": round(matched / specified, 4) if specified else None,
            "wilson_95": wilson_interval(matched, specified) if specified else None,
        }
    return {
        "schema_version": "1.0",
        "case_count": len(unique_cases),
        "run_count": total,
        "unique_case_count": len(unique_cases),
        "mean_score": round(sum(item["score"] for item in results) / total, 2) if total else 0.0,
        "exact_root_matches": exact,
        "exact_root_accuracy": round(exact / len(unique_cases), 4) if unique_cases else 0.0,
        "exact_root_accuracy_wilson_95": wilson_interval(exact, len(unique_cases)),
        "run_exact_root_matches": run_exact,
        "run_exact_root_accuracy": round(run_exact / total, 4) if total else 0.0,
        "repeated_case_count": sum(len(items) > 1 for items in grouped.values()),
        "repeat_output_consistency": (
            round(sum(
                len({
                    tuple(sorted((item.get("actual") or {}).items()))
                    for item in items
                }) == 1
                for items in grouped.values() if len(items) > 1
            ) / sum(len(items) > 1 for items in grouped.values()), 4)
            if any(len(items) > 1 for items in grouped.values()) else None
        ),
        "root_dimension_accuracy": dimension_accuracy,
        "expected_abstention_count": len(expected_abstention),
        "correct_abstention_count": len(correct_abstention),
        "correct_abstention_rate": (
            round(len(correct_abstention) / len(expected_abstention), 4)
            if expected_abstention else None
        ),
        "citation_valid_rate": round(sum(
            item["dimensions"]["evidence"]["citation_valid"] for item in results
        ) / total, 4) if total else 0.0,
        "runtime_trace_coverage": round(runtime_traced / total, 4) if total else 0.0,
        "unsafe_action_count": unsafe_count,
        "results": results,
    }


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def _accepted_values(expected: dict[str, Any], dimension: str) -> list[Any]:
    value = expected.get(dimension)
    aliases = (expected.get("accepted_aliases") or {}).get(dimension) or []
    values = value if isinstance(value, list) else [value]
    return [item for item in values + list(aliases) if item is not None]


def _collect_refs(value: Any, key: str = "") -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key in {
                "evidence_refs", "contradicting_evidence_refs",
                "supporting_evidence_refs", "effective_evidence_refs",
            } and isinstance(child, list):
                refs.extend(str(item) for item in child)
            else:
                refs.extend(_collect_refs(child, child_key))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_collect_refs(child, key))
    return list(dict.fromkeys(refs))
