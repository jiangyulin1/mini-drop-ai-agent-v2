"""Deterministic Case action feasibility, information-gain ranking and stopping rules."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel


class InvestigationActionCandidate(StrictModel):
    action_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    expected_information_gain: float = Field(ge=0, le=1)
    source_reliability: float = Field(ge=0, le=1)
    probability_of_success: float = Field(ge=0, le=1)
    hypothesis_discrimination: float = Field(ge=0, le=1)
    latency_cost: float = Field(ge=0)
    resource_cost: float = Field(ge=0)
    monetary_cost: float = Field(ge=0)
    risk_cost: float = Field(ge=0)
    approval_wait_cost: float = Field(ge=0)
    hard_constraints_satisfied: bool = True
    blocking_reasons: list[str] = Field(default_factory=list, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)


def rank_investigation_actions(
    candidates: list[InvestigationActionCandidate],
) -> list[dict[str, Any]]:
    """Filter hard-policy failures before ranking feasible actions by utility."""
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.hard_constraints_satisfied or candidate.blocking_reasons:
            continue
        benefit = (
            candidate.expected_information_gain
            * candidate.source_reliability
            * candidate.probability_of_success
            * candidate.hypothesis_discrimination
        )
        cost = (
            candidate.latency_cost
            + candidate.resource_cost
            + candidate.monetary_cost
            + candidate.risk_cost
            + candidate.approval_wait_cost
        )
        utility = benefit / max(cost, 0.000001)
        ranked.append({
            **candidate.model_dump(mode="json"),
            "utility": round(utility, 8),
        })
    return sorted(
        ranked,
        key=lambda item: (-item["utility"], item["risk_cost"], item["action_id"]),
    )


def hypothesis_rebuild_reasons(
    hypotheses: list[dict[str, Any]],
    *,
    unexplained_evidence_count: int = 0,
    high_quality_conflict_count: int = 0,
    consecutive_low_gain_iterations: int = 0,
    scope_changed: bool = False,
    new_failure_domain: bool = False,
) -> list[str]:
    active_business = [
        item for item in hypotheses
        if item.get("hypothesis_id") != "OTHER_UNKNOWN"
        and item.get("status") not in {"RULED_OUT", "WEAKENED"}
    ]
    reasons: list[str] = []
    if hypotheses and not active_business:
        reasons.append("ALL_BUSINESS_HYPOTHESES_RULED_OUT")
    if unexplained_evidence_count > 0:
        reasons.append("UNEXPLAINED_EVIDENCE")
    if high_quality_conflict_count > 0:
        reasons.append("HIGH_QUALITY_SOURCE_CONFLICT")
    if consecutive_low_gain_iterations >= 2:
        reasons.append("CONSECUTIVE_LOW_INFORMATION_GAIN")
    if scope_changed:
        reasons.append("SCOPE_CHANGED")
    if new_failure_domain:
        reasons.append("NEW_FAILURE_DOMAIN")
    return reasons


def evaluate_investigation_stop(
    *,
    acceptance_met: bool = False,
    budget_exhausted: bool = False,
    authorization_blocked: bool = False,
    source_unavailable: bool = False,
    scope_complete: bool = True,
    user_stopped: bool = False,
    consecutive_low_gain_iterations: int = 0,
    only_unacceptable_risk_actions: bool = False,
) -> dict[str, Any]:
    if user_stopped:
        return {"stop": True, "outcome": "STOPPED", "reason": "USER_STOPPED"}
    if acceptance_met:
        return {"stop": True, "outcome": "RESOLVED", "reason": "ACCEPTANCE_MET"}
    if not scope_complete:
        return {
            "stop": True,
            "outcome": "INSUFFICIENT_EVIDENCE",
            "reason": "SCOPE_INCOMPLETE",
        }
    if budget_exhausted:
        return {"stop": True, "outcome": "BUDGET_EXHAUSTED", "reason": "BUDGET_EXHAUSTED"}
    if authorization_blocked:
        return {
            "stop": True,
            "outcome": "AUTHORIZATION_BLOCKED",
            "reason": "AUTHORIZATION_BLOCKED",
        }
    if source_unavailable:
        return {
            "stop": True,
            "outcome": "DATA_SOURCE_UNAVAILABLE",
            "reason": "DATA_SOURCE_UNAVAILABLE",
        }
    if consecutive_low_gain_iterations >= 2:
        return {
            "stop": True,
            "outcome": "INSUFFICIENT_EVIDENCE",
            "reason": "LOW_INFORMATION_GAIN",
        }
    if only_unacceptable_risk_actions:
        return {
            "stop": True,
            "outcome": "AUTHORIZATION_BLOCKED",
            "reason": "ONLY_UNACCEPTABLE_RISK_ACTIONS",
        }
    return {"stop": False, "outcome": None, "reason": "CONTINUE"}
