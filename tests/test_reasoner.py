from __future__ import annotations

import json

import pytest

from scripts.run_lightweight_ai_eval import DATASET
from server.app.diagnosis.eval_harness import load_scenarios
from server.app.diagnosis.reasoner import (
    ReasonerDecision,
    ReasonerInput,
    RulesOnlyReasoner,
    assess_with_reasoner,
)


def _scenario(scenario_id: str) -> dict:
    return next(
        item
        for item in load_scenarios(DATASET / "scenarios")
        if item["scenario_id"] == scenario_id
    )


def test_rules_only_reasoner_is_the_deterministic_control_group():
    scenario = _scenario("otel_ad_high_cpu")

    decision = assess_with_reasoner(
        scenario["scope"],
        scenario["observations"],
        reasoner=RulesOnlyReasoner(),
    )

    assert decision.strategy_id == "rules_only"
    assert decision.decision_type == "conclusion"
    assert decision.assessment["classification"] == scenario["expected"]["classification"]
    assert decision.evidence_refs


def test_rules_only_reasoner_abstains_without_discriminating_evidence():
    scenario = _scenario("healthy_baseline_no_fault")

    decision = assess_with_reasoner(scenario["scope"], scenario["observations"])

    assert decision.decision_type == "abstain"
    assert decision.assessment["classification"] == "insufficient_evidence"


def test_reasoner_contract_rejects_unknown_fields_and_incomplete_decisions():
    with pytest.raises(ValueError):
        ReasonerInput(scope={}, surprise=True)
    with pytest.raises(ValueError):
        ReasonerDecision(
            strategy_id="test",
            strategy_version="v1",
            decision_type="next_probe",
        )


def test_reasoner_input_does_not_require_oracle():
    scenario = _scenario("otel_payment_unreachable")
    payload = ReasonerInput(
        intent={"query": scenario["query"]},
        scope=scenario["scope"],
        normalized_evidence=scenario["observations"],
    ).model_dump(mode="json")

    assert "expected" not in json.dumps(payload)
    assert "oracle" not in json.dumps(payload).lower()
