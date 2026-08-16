"""Acquisition Campaign input contracts (G4).

A Campaign is compiled by CaseSupervisor into one InvestigationPlan revision.
The raw matrix is deliberately kept as the API input; the durable compiled
truth remains InvestigationPlan/Step so there is no second scheduler state
machine.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from server.app.diagnosis.investigation_plan import PlanStepInput, PlanUpdateInput
from server.app.diagnosis.schemas import StrictModel


class CampaignAssignmentInput(StrictModel):
    role: str = Field(min_length=1, max_length=64)
    collector_id: str = Field(min_length=1, max_length=64)
    target_refs: list[str] = Field(default_factory=list, max_length=128)
    selection_strategy: str | None = Field(default=None, max_length=40)
    purpose: str = Field(default="", max_length=500)
    priority: int = Field(default=60, ge=0, le=1000)
    risk: str = "READ_LOW"


class CampaignCreateInput(StrictModel):
    goal: str = Field(default="定位根因", max_length=200)
    common_baseline: CampaignAssignmentInput
    assignments: list[CampaignAssignmentInput] = Field(min_length=1, max_length=32)
    expected_case_row_version: int = 0
    expected_scope_revision: int = 0
    expected_plan_revision: int = 0


def build_campaign_plan(payload: CampaignCreateInput) -> PlanUpdateInput:
    """Compile a Campaign matrix into one deterministic Plan revision."""
    steps: list[PlanStepInput] = []
    baseline = payload.common_baseline
    steps.append(PlanStepInput(
        kind="COLLECTION",
        collector_id=baseline.collector_id,
        target_refs=baseline.target_refs,
        purpose=f"共同基线：{baseline.role} {baseline.purpose}".strip() or "共同基线采集",
        priority=max(baseline.priority, 70),
        risk=baseline.risk,
        selection_strategy=baseline.selection_strategy,
        status="QUEUED",
    ))
    for item in payload.assignments:
        steps.append(PlanStepInput(
            kind="COLLECTION",
            collector_id=item.collector_id,
            target_refs=item.target_refs,
            purpose=f"{item.role}: {item.purpose}".strip(),
            priority=item.priority,
            risk=item.risk,
            selection_strategy=item.selection_strategy,
            status="QUEUED",
        ))
    return PlanUpdateInput(
        goal=payload.goal,
        steps=steps,
        expected_case_row_version=payload.expected_case_row_version,
        expected_scope_revision=payload.expected_scope_revision,
        expected_plan_revision=payload.expected_plan_revision,
        source="campaign",
    )


def campaign_matrix(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Read-only projection of the last Campaign compilation from a Plan."""
    if not plan:
        return {"present": False}
    return {
        "present": True,
        "plan_revision": plan.get("plan_revision"),
        "steps": [
            {
                "step_id": step.get("step_id"),
                "collector_id": step.get("collector_id"),
                "target_refs": step.get("target_refs"),
                "selection_strategy": step.get("selection_strategy"),
                "purpose": step.get("purpose"),
                "status": step.get("status"),
            }
            for step in (plan.get("steps") or [])
        ],
    }
