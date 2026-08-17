"""v6 Policy Context for the Agent Runtime.

The model must choose the next action from actual Evidence/Gap/Skill state.
Mini-Drop supplies scope, risk, budget, reusable Evidence, Skill candidates and
forbidden directions; it never pre-selects a fixed evidence_order or the only
next Collector.  Determinism is preserved for the policy input, not for the
model decision.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel


class InvestigationDirective(StrictModel):
    directive_key: str
    strategy_id: str = "hybrid"
    strategy_version: str = "hybrid.v1"
    strategy_guidance: str = ""
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    evidence_order: list[str] = Field(default_factory=list)
    collected_evidence_types: list[str] = Field(default_factory=list)
    missing_evidence_types: list[str] = Field(default_factory=list)
    next_action: Optional[str] = None
    answer_policy: str = "evidence_driven_free_within_policy"
    forbidden_directions: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    rationale: str = ""


def normalize_goal(goal: str) -> str:
    text = goal.lower().strip()
    text = re.sub(
        r"\d{4}-\d{2}-\d{2}[t\s]\d{2}:\d{2}(:\d{2})?(z|[+-]\d{2}:?\d{2})?",
        " <time> ", text,
    )
    text = re.sub(r"\b\d{1,2}[:：]\d{2}\b", " <time> ", text)
    text = re.sub(r"task_[a-z0-9_.-]+", " <task> ", text)
    text = re.sub(r"case_[a-z0-9_.-]+", " <case> ", text)
    text = re.sub(r"pid\s*\d+", " <pid> ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def directive_key(
    goal: str,
    target_scope: dict[str, Any] | None,
    evidence_types: list[str],
    skill_ids: list[str],
) -> str:
    scope = dict(target_scope or {})
    normalized_scope = {
        key: scope[key] for key in sorted(scope)
        if key in {"service_id", "cluster_id", "workload", "environment"}
    }
    raw = "|".join([
        normalize_goal(goal),
        repr(normalized_scope),
        ",".join(sorted(set(evidence_types))),
        ",".join(sorted(set(skill_ids))),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_directive(
    *,
    goal: str,
    target_scope: dict[str, Any] | None,
    evidence_summary: list[dict[str, Any]] | None = None,
    skill_context: list[dict[str, Any]] | None = None,
    missing_facts: list[str] | None = None,
) -> InvestigationDirective:
    skills = skill_context or []
    skill_ids = [str(item.get("skill_id") or "") for item in skills if item.get("skill_id")]
    evidence_types = sorted({
        str(item.get("artifact_type") or "")
        for item in (evidence_summary or [])
        if item.get("artifact_type")
    })
    allowed_operations: list[str] = []
    stop_conditions: list[str] = ["no_effective_progress_for_two_cycles"]
    for skill in skills:
        for item in skill.get("allowed_operations") or []:
            allowed_operations.append(str(item))
        for item in skill.get("stopping_conditions") or []:
            stop_conditions.append(str(item))
    forbidden: list[str] = []
    for skill in skills:
        for item in skill.get("negative_triggers") or []:
            forbidden.append(str(item))
    key = directive_key(goal, target_scope, evidence_types, skill_ids)
    return InvestigationDirective(
        directive_key=key,
        evidence_order=[],
        collected_evidence_types=evidence_types,
        missing_evidence_types=sorted(set(missing_facts or [])),
        next_action=None,
        answer_policy="evidence_driven_free_within_policy",
        forbidden_directions=sorted(set(forbidden)),
        allowed_operations=sorted(set(allowed_operations)),
        stop_conditions=sorted(set(stop_conditions)),
        rationale=(
            "Policy Context only. The model chooses the next operation from "
            "observed Evidence/Gap/Skill within allowed operations, budget and risk."
        ),
    )
