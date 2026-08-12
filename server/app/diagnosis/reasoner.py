"""Replaceable diagnosis reasoning strategies with a deterministic baseline."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from server.app.diagnosis.domain_analyzers import assess_cluster
from server.app.diagnosis.schemas import StrictModel


class ReasonerInput(StrictModel):
    """Versioned, provider-neutral input shared by every reasoning strategy."""

    schema_version: Literal["reasoner-input.v1"] = "reasoner-input.v1"
    intent: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any]
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    normalized_evidence: list[dict[str, Any]] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    remaining_budget: dict[str, int] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)


class ReasonerDecision(StrictModel):
    """A conclusion, bounded next-probe request, or explicit abstention."""

    schema_version: Literal["reasoner-decision.v1"] = "reasoner-decision.v1"
    strategy_id: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(min_length=1, max_length=64)
    decision_type: Literal["conclusion", "next_probe", "abstain"]
    assessment: dict[str, Any] | None = None
    ranked_causes: list[dict[str, Any]] = Field(default_factory=list)
    next_probe_request: dict[str, Any] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale_summary: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.decision_type in {"conclusion", "abstain"} and self.assessment is None:
            raise ValueError("conclusion/abstain decision requires assessment")
        if self.decision_type == "next_probe" and self.next_probe_request is None:
            raise ValueError("next_probe decision requires next_probe_request")
        return self


class Reasoner(Protocol):
    strategy_id: str
    strategy_version: str

    def decide(self, reasoner_input: ReasonerInput) -> ReasonerDecision: ...


class RulesOnlyReasoner:
    """Permanent deterministic control group for every AI experiment."""

    strategy_id = "rules_only"
    strategy_version = "rules-only.v1"

    def decide(self, reasoner_input: ReasonerInput) -> ReasonerDecision:
        assessment = assess_cluster(
            reasoner_input.scope,
            reasoner_input.normalized_evidence,
        )
        abstained = assessment.get("classification") == "insufficient_evidence"
        return ReasonerDecision(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            decision_type="abstain" if abstained else "conclusion",
            assessment=assessment,
            evidence_refs=list(assessment.get("evidence_refs") or []),
            uncertainty=max(0.0, min(1.0, 1.0 - float(assessment.get("confidence") or 0.0))),
            rationale_summary=str(assessment.get("summary") or ""),
        )


DEFAULT_REASONER: Reasoner = RulesOnlyReasoner()


def assess_with_reasoner(
    scope: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    reasoner: Reasoner = DEFAULT_REASONER,
    intent: dict[str, Any] | None = None,
    hypotheses: list[dict[str, Any]] | None = None,
    missing_facts: list[str] | None = None,
    policy: dict[str, Any] | None = None,
    remaining_budget: dict[str, int] | None = None,
    versions: dict[str, str] | None = None,
) -> ReasonerDecision:
    return reasoner.decide(ReasonerInput(
        intent=intent or {},
        scope=scope,
        hypotheses=hypotheses or [],
        normalized_evidence=observations,
        missing_facts=missing_facts or [],
        policy=policy or {},
        remaining_budget=remaining_budget or {},
        versions=versions or {},
    ))
