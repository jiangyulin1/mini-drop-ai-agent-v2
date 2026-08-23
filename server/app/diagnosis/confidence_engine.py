"""Deterministic, explainable confidence calculation for Evidence chains."""

from __future__ import annotations

from math import prod
from typing import Any, Iterable


CALCULATION_VERSION = "evidence-weighted-v1"
INACTIVE_LIFECYCLES = {"EXCLUDED", "SUPERSEDED", "INVALID"}
TRUST_FACTORS = {
    "TRUSTED": 1.0,
    "UNREVIEWED": 0.75,
    "LOW_TRUST": 0.5,
}
ASSESSMENT_FACTORS = {
    "freshness": {"CURRENT_WINDOW": 1.0, "HISTORICAL": 0.65, "EXPIRED": 0.25},
    "scope_match": {"CORRECT": 1.0, "PARTIAL": 0.65, "WRONG_SCOPE": 0.0},
    "directness": {"DIRECT": 1.0, "INDIRECT": 0.7, "INFERRED": 0.45},
    "independence": {"INDEPENDENT_SUPPORT": 1.0, "NONE": 0.85, "CONFLICT": 0.65},
}


def _bounded(value: Any, default: float = 1.0) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 6)
    except (TypeError, ValueError):
        return default


def _factor(assessment: dict[str, Any], name: str, default: float) -> float:
    explicit = assessment.get(f"{name}_factor")
    if explicit is not None:
        return _bounded(explicit, default)
    value = str(assessment.get(name) or "").upper()
    return ASSESSMENT_FACTORS.get(name, {}).get(value, default)


def evidence_contribution(
    evidence: dict[str, Any],
    *,
    relation: str = "SUPPORTS",
    base_weight: float = 1.0,
) -> dict[str, Any]:
    """Return a complete contribution ledger for one Evidence item."""
    evidence_id = str(evidence.get("evidence_id") or evidence.get("id") or "")
    lifecycle = str(evidence.get("lifecycle_status") or evidence.get("status") or "ACTIVE").upper()
    trust_state = str(evidence.get("review_trust_state") or "UNREVIEWED").upper()
    assessment = dict(evidence.get("assessment") or evidence.get("latest_assessment") or {})
    eligibility = "INACTIVE" if lifecycle in INACTIVE_LIFECYCLES else "ACTIVE"
    trust_factor = 0.0 if eligibility == "INACTIVE" else TRUST_FACTORS.get(trust_state, 0.75)
    freshness_factor = _factor(assessment, "freshness", 1.0)
    scope_match_factor = _factor(assessment, "scope_match", 1.0)
    directness_factor = _factor(assessment, "directness", 0.8)
    independence_factor = _factor(assessment, "independence", 0.85)
    normalized_weight = _bounded(base_weight)
    effective_weight = round(
        normalized_weight
        * trust_factor
        * freshness_factor
        * scope_match_factor
        * directness_factor
        * independence_factor,
        6,
    )
    reason_parts = [f"lifecycle={lifecycle}", f"trust={trust_state}"]
    if eligibility == "INACTIVE":
        reason_parts.append("inactive Evidence contributes zero")
    return {
        "evidence_id": evidence_id,
        "relation": str(relation or "SUPPORTS").upper(),
        "eligibility": eligibility,
        "lifecycle_status": lifecycle,
        "trust_state": trust_state,
        "review_revision": int(evidence.get("review_revision") or 0),
        "base_weight": normalized_weight,
        "trust_factor": trust_factor,
        "freshness_factor": freshness_factor,
        "scope_match_factor": scope_match_factor,
        "directness_factor": directness_factor,
        "independence_factor": independence_factor,
        "effective_weight": effective_weight,
        "contribution": effective_weight,
        "reason": "; ".join(reason_parts),
    }


def calculate_chain_confidence(
    evidence_rows: Iterable[dict[str, Any]],
    dependencies: Iterable[dict[str, Any]],
    *,
    operator_requested_confidence: float | None = None,
    operator_reason: str | None = None,
) -> dict[str, Any]:
    """Aggregate support and contradiction with a stable bounded formula.

    Independent contributions use ``1 - product(1 - weight)``. Contradiction
    scales the support score, so no amount of operator input can create support
    when every dependency is inactive.
    """
    by_id = {
        str(row.get("evidence_id") or row.get("id") or ""): row
        for row in evidence_rows
    }
    ledger = []
    for dependency in dependencies:
        evidence_id = str(dependency.get("evidence_id") or dependency.get("source_id") or "")
        row = by_id.get(evidence_id, {"evidence_id": evidence_id, "lifecycle_status": "INVALID"})
        ledger.append(evidence_contribution(
            row,
            relation=str(dependency.get("relation") or "SUPPORTS"),
            base_weight=dependency.get("support_weight", 1.0),
        ))
    support = [item["contribution"] for item in ledger if item["relation"] == "SUPPORTS"]
    contradiction = [item["contribution"] for item in ledger if item["relation"] == "CONTRADICTS"]
    support_score = round(1.0 - prod(1.0 - item for item in support), 6) if support else 0.0
    contradiction_score = round(1.0 - prod(1.0 - item for item in contradiction), 6) if contradiction else 0.0
    computed = round(support_score * (1.0 - contradiction_score), 6)
    active_support = sorted({
        item["evidence_id"] for item in ledger
        if item["relation"] == "SUPPORTS" and item["eligibility"] == "ACTIVE"
    })
    invalidated = sorted({
        item["evidence_id"] for item in ledger if item["eligibility"] == "INACTIVE"
    })
    low_trust_only = bool(active_support) and all(
        item["trust_state"] == "LOW_TRUST"
        for item in ledger
        if item["relation"] == "SUPPORTS" and item["eligibility"] == "ACTIVE"
    )
    requested = None if operator_requested_confidence is None else _bounded(operator_requested_confidence, computed)
    cap = 0.5 if low_trust_only else (1.0 if active_support else 0.0)
    effective = computed if requested is None else max(computed, min(requested, cap))
    status = "INVALIDATED" if not active_support else "RECHECK_REQUIRED" if invalidated or contradiction_score else "ACTIVE"
    reason = (
        f"{len(active_support)} active support item(s), {len(invalidated)} inactive item(s); "
        f"support={support_score:.4f}, contradiction={contradiction_score:.4f}"
    )
    if requested is not None:
        reason += f"; operator requested {requested:.4f}: {operator_reason or 'no reason'}"
    return {
        "calculation_version": CALCULATION_VERSION,
        "status": status,
        "computed_confidence": computed,
        "operator_requested_confidence": requested,
        "effective_confidence": round(effective, 6),
        "confidence_cap": cap,
        "confidence_reason": reason,
        "support_score": support_score,
        "contradiction_score": contradiction_score,
        "invalidated_evidence_refs": invalidated,
        "remaining_active_support": active_support,
        "ledger": ledger,
    }
