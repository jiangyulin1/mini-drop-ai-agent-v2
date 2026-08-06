"""Deterministic Case information-gain planner and stopping-rule tests."""

from server.app.diagnosis.investigation_planner import (
    InvestigationActionCandidate,
    evaluate_investigation_stop,
    hypothesis_rebuild_reasons,
    rank_investigation_actions,
)


def _candidate(action_id: str, *, gain: float, risk: float = 0.1, allowed: bool = True):
    return InvestigationActionCandidate(
        action_id=action_id,
        source_id="metrics",
        operation="query",
        expected_information_gain=gain,
        source_reliability=1,
        probability_of_success=1,
        hypothesis_discrimination=1,
        latency_cost=1,
        resource_cost=0,
        monetary_cost=0,
        risk_cost=risk,
        approval_wait_cost=0,
        hard_constraints_satisfied=allowed,
        blocking_reasons=[] if allowed else ["OUT_OF_SCOPE"],
    )


def test_hard_constraints_are_filtered_before_utility_ranking():
    ranked = rank_investigation_actions([
        _candidate("blocked-high-gain", gain=1, allowed=False),
        _candidate("safe-medium-gain", gain=0.6, risk=0.1),
        _candidate("safe-low-gain", gain=0.3, risk=0.1),
    ])
    assert [item["action_id"] for item in ranked] == [
        "safe-medium-gain", "safe-low-gain",
    ]
    assert ranked[0]["utility"] > ranked[1]["utility"]


def test_rebuild_reasons_cover_open_set_conflict_and_scope_change():
    reasons = hypothesis_rebuild_reasons(
        [
            {"hypothesis_id": "cpu", "status": "RULED_OUT"},
            {"hypothesis_id": "OTHER_UNKNOWN", "status": "UNKNOWN"},
        ],
        unexplained_evidence_count=1,
        high_quality_conflict_count=1,
        consecutive_low_gain_iterations=2,
        scope_changed=True,
    )
    assert reasons == [
        "ALL_BUSINESS_HYPOTHESES_RULED_OUT",
        "UNEXPLAINED_EVIDENCE",
        "HIGH_QUALITY_SOURCE_CONFLICT",
        "CONSECUTIVE_LOW_INFORMATION_GAIN",
        "SCOPE_CHANGED",
    ]


def test_stopping_rules_return_typed_outcome():
    assert evaluate_investigation_stop(user_stopped=True)["outcome"] == "STOPPED"
    assert evaluate_investigation_stop(budget_exhausted=True)["outcome"] == "BUDGET_EXHAUSTED"
    assert evaluate_investigation_stop(scope_complete=False) == {
        "stop": True,
        "outcome": "INSUFFICIENT_EVIDENCE",
        "reason": "SCOPE_INCOMPLETE",
    }
    assert evaluate_investigation_stop()["stop"] is False
