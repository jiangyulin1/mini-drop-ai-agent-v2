from server.app.diagnosis.confidence_engine import (
    CALCULATION_VERSION,
    calculate_chain_confidence,
    evidence_contribution,
)


def test_excluded_evidence_has_zero_contribution():
    item = evidence_contribution({
        "evidence_id": "ev-1",
        "lifecycle_status": "EXCLUDED",
        "review_trust_state": "TRUSTED",
    })
    assert item["eligibility"] == "INACTIVE"
    assert item["effective_weight"] == 0.0


def test_chain_confidence_is_explainable_and_bounded():
    result = calculate_chain_confidence(
        [
            {"evidence_id": "ev-a", "lifecycle_status": "ACTIVE", "review_trust_state": "TRUSTED"},
            {"evidence_id": "ev-b", "lifecycle_status": "ACTIVE", "review_trust_state": "LOW_TRUST"},
            {"evidence_id": "ev-x", "lifecycle_status": "EXCLUDED", "review_trust_state": "TRUSTED"},
        ],
        [
            {"source_id": "ev-a", "relation": "SUPPORTS", "support_weight": 1.0},
            {"source_id": "ev-b", "relation": "CONTRADICTS", "support_weight": 0.5},
            {"source_id": "ev-x", "relation": "SUPPORTS", "support_weight": 1.0},
        ],
    )
    assert result["calculation_version"] == CALCULATION_VERSION
    assert 0 < result["computed_confidence"] < 1
    assert result["invalidated_evidence_refs"] == ["ev-x"]
    assert result["remaining_active_support"] == ["ev-a"]
    assert len(result["ledger"]) == 3


def test_operator_cannot_raise_chain_without_active_support():
    result = calculate_chain_confidence(
        [{"evidence_id": "ev-x", "lifecycle_status": "EXCLUDED"}],
        [{"source_id": "ev-x", "relation": "SUPPORTS"}],
        operator_requested_confidence=0.99,
        operator_reason="operator assertion",
    )
    assert result["confidence_cap"] == 0.0
    assert result["effective_confidence"] == 0.0


def test_low_trust_only_chain_is_capped_for_operator_adjustment():
    result = calculate_chain_confidence(
        [{"evidence_id": "ev-low", "lifecycle_status": "ACTIVE", "review_trust_state": "LOW_TRUST"}],
        [{"source_id": "ev-low", "relation": "SUPPORTS"}],
        operator_requested_confidence=0.9,
        operator_reason="reviewed by operator",
    )
    assert result["confidence_cap"] == 0.5
    assert result["effective_confidence"] == 0.5
