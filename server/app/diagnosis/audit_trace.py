"""Auditable diagnosis decisions without storing private model chain-of-thought.

The trace records observable inputs, selected/rejected candidates, evidence
references, policy decisions and outputs.  Each persisted step is linked to the
previous step with a SHA-256 hash so an exported run can be checked for missing
or modified records.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


TRACE_EVENT_TYPE = "diagnosis_decision_trace"
TRACE_SCHEMA_VERSION = "1.0"

TRACE_STAGES = (
    "intent",
    "scope",
    "hypothesis",
    "probe_plan",
    "evidence_curation",
    "candidate_assessment",
    "causal_assessment",
    "action_policy",
    "report_verification",
    "recovery_verification",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def build_trace_step(
    *,
    diagnosis_id: str,
    sequence: int,
    stage: str,
    component: str,
    decision: str,
    summary: str,
    input_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    alternatives: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    previous_hash: str | None = None,
    recorded_at: datetime | str | None = None,
    reconstructed: bool = False,
) -> dict[str, Any]:
    if stage not in TRACE_STAGES:
        raise ValueError(f"unsupported trace stage: {stage}")
    timestamp = recorded_at or datetime.now(timezone.utc)
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    step = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "diagnosis_id": diagnosis_id,
        "sequence": int(sequence),
        "stage": stage,
        "component": str(component),
        "decision": str(decision),
        "summary": str(summary),
        "input_refs": list(dict.fromkeys(input_refs or [])),
        "output_refs": list(dict.fromkeys(output_refs or [])),
        "evidence_refs": list(dict.fromkeys(evidence_refs or [])),
        "alternatives": alternatives or [],
        "details": details or {},
        "previous_hash": previous_hash,
        "recorded_at": timestamp,
        "reconstructed": bool(reconstructed),
    }
    step["step_hash"] = content_hash(step)
    return step


def verify_trace_chain(steps: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[str] = []
    expected_previous = None
    expected_sequence = 1
    for step in steps:
        supplied_hash = step.get("step_hash")
        material = dict(step)
        material.pop("step_hash", None)
        calculated = content_hash(material)
        if supplied_hash != calculated:
            issues.append(f"sequence {step.get('sequence')}: step hash mismatch")
        if step.get("sequence") != expected_sequence:
            issues.append(
                f"sequence {step.get('sequence')}: expected sequence {expected_sequence}"
            )
        if step.get("previous_hash") != expected_previous:
            issues.append(f"sequence {step.get('sequence')}: previous hash mismatch")
        expected_previous = supplied_hash
        expected_sequence += 1
    return {
        "status": "passed" if not issues else "failed",
        "step_count": len(steps),
        "runtime_step_count": sum(not item.get("reconstructed", False) for item in steps),
        "reconstructed_step_count": sum(bool(item.get("reconstructed")) for item in steps),
        "issues": issues,
    }


def trace_steps_from_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    persisted = [
        event.get("payload") or {}
        for event in detail.get("events", [])
        if event.get("event_type") == TRACE_EVENT_TYPE
    ]
    if persisted:
        return sorted(persisted, key=lambda item: int(item.get("sequence", 0)))
    return _reconstruct_legacy_trace(detail)


def build_audit_bundle(detail: dict[str, Any], *, include_oracle: bool = False) -> dict[str, Any]:
    steps = trace_steps_from_detail(detail)
    conclusion = dict(detail.get("latest_conclusion") or {})
    if not include_oracle:
        conclusion.pop("evaluation", None)
    evidence = detail.get("evidence") or []
    bundle = {
        "schema_version": "1.0",
        "diagnosis_id": detail.get("diagnosis_id"),
        "run": {
            "status": detail.get("status"),
            "created_at": detail.get("created_at"),
            "updated_at": detail.get("updated_at"),
            "model_version": detail.get("model_version"),
            "planner_version": detail.get("planner_version"),
            "policy_profile": detail.get("policy_profile"),
            "normalized_intent": detail.get("normalized_intent") or {},
            "target_scope": detail.get("target_scope") or {},
        },
        "trace": steps,
        "trace_verification": verify_trace_chain(steps),
        "evidence_manifest": [
            {
                "evidence_id": item.get("evidence_id"),
                "source_type": item.get("source_type"),
                "source_system": item.get("source_system"),
                "query_or_probe": item.get("query_or_probe"),
                "evidence_role": item.get("evidence_role"),
                "target": item.get("target") or {},
                "data_quality": item.get("data_quality") or {},
                "integrity_hash": item.get("integrity_hash"),
            }
            for item in evidence
        ],
        "hypothesis_graph": detail.get("hypothesis_graph") or {},
        "probes": detail.get("probes") or [],
        "pipeline_nodes": detail.get("pipeline_nodes") or [],
        "conclusion": conclusion,
        "bundle_hash": "",
    }
    if include_oracle:
        bundle["evaluation_oracle"] = detail.get("evaluation_oracle") or {}
    bundle["bundle_hash"] = content_hash({key: value for key, value in bundle.items() if key != "bundle_hash"})
    return bundle


def _reconstruct_legacy_trace(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an explicitly marked view for sessions created before trace persistence."""
    diagnosis_id = str(detail.get("diagnosis_id") or "unknown")
    timestamp = detail.get("updated_at") or detail.get("created_at")
    steps: list[dict[str, Any]] = []

    def add(stage: str, component: str, decision: str, summary: str, **kwargs: Any) -> None:
        steps.append(build_trace_step(
            diagnosis_id=diagnosis_id,
            sequence=len(steps) + 1,
            stage=stage,
            component=component,
            decision=decision,
            summary=summary,
            previous_hash=steps[-1]["step_hash"] if steps else None,
            recorded_at=timestamp,
            reconstructed=True,
            **kwargs,
        ))

    intent = detail.get("normalized_intent") or {}
    add(
        "intent", "legacy_reconstruction", "normalized_intent",
        "Reconstructed from the persisted normalized intent.",
        output_refs=["normalized_intent"], details={"intent": intent},
    )
    scope = detail.get("target_scope") or {}
    add(
        "scope", "legacy_reconstruction", "resolved_scope",
        "Reconstructed from the persisted target scope.",
        input_refs=["normalized_intent"], output_refs=["target_scope"],
        details={"scope_completeness": scope.get("scope_completeness"),
                 "instance_count": len(scope.get("instances") or [])},
    )
    hypotheses = (detail.get("hypothesis_graph") or {}).get("hypotheses") or []
    add(
        "hypothesis", "legacy_reconstruction", "candidate_update",
        "Reconstructed from the latest persisted hypothesis graph.",
        output_refs=[item.get("hypothesis_id") for item in hypotheses if item.get("hypothesis_id")],
        alternatives=[{
            "id": item.get("hypothesis_id"), "status": item.get("status"),
            "score": item.get("evidence_score"),
            "evidence_refs": item.get("supporting_evidence_refs") or [],
            "contradicting_evidence_refs": item.get("contradicting_evidence_refs") or [],
        } for item in hypotheses],
    )
    probes = detail.get("probes") or []
    add(
        "probe_plan", "legacy_reconstruction", "planned_probes",
        "Reconstructed from persisted probe executions.",
        output_refs=[item.get("step_id") for item in probes if item.get("step_id")],
        alternatives=[{
            "id": item.get("step_id"), "probe_id": item.get("probe_id"),
            "status": item.get("status"), "reason": item.get("reason"),
            "risk_level": item.get("risk_level"),
        } for item in probes],
    )
    conclusion = detail.get("latest_conclusion") or {}
    review = conclusion.get("evidence_review") or {}
    add(
        "evidence_curation", "legacy_reconstruction", "curated_evidence",
        "Reconstructed from the final evidence review; original intermediate state was not recorded.",
        evidence_refs=review.get("effective_evidence_refs") or [], details=review,
    )
    findings = conclusion.get("findings") or []
    add(
        "candidate_assessment", "legacy_reconstruction", "derived_findings",
        "Reconstructed from final findings and candidates.",
        output_refs=[item.get("finding_id") for item in findings if item.get("finding_id")],
        evidence_refs=list(dict.fromkeys(
            ref for item in findings for ref in item.get("evidence_refs") or []
        )),
        alternatives=conclusion.get("root_cause_candidates") or [],
    )
    assessment = conclusion.get("cluster_assessment") or {}
    add(
        "causal_assessment", "legacy_reconstruction", "selected_root_cause",
        str(conclusion.get("summary") or "No final conclusion was persisted."),
        evidence_refs=assessment.get("evidence_refs") or [], details={
            "classification": assessment.get("classification"),
            "confidence": assessment.get("confidence"),
            "confidence_level": assessment.get("confidence_level"),
            "root_location": conclusion.get("root_location") or {},
            "domain_cause": conclusion.get("domain_cause") or {},
            "ruled_out": conclusion.get("ruled_out") or [],
        },
    )
    actions = conclusion.get("actions") or []
    add(
        "action_policy", "legacy_reconstruction", "rendered_actions",
        "Reconstructed from the final validated action list.",
        input_refs=list(dict.fromkeys(ref for item in actions for ref in item.get("evidence_refs") or [])),
        output_refs=[item.get("action_id") for item in actions if item.get("action_id")],
        details={"actions": actions},
    )
    verification = conclusion.get("verification") or {}
    add(
        "report_verification", "legacy_reconstruction", "verified_report",
        "Reconstructed from final report verification.",
        details=verification,
    )
    return steps
