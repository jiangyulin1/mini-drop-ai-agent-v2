"""Persistent investigation plans and plan-step state machine (E2).

Mini-Drop owns the plan; a runtime (Pi or deterministic) only proposes edits.
This service enforces the plan/scope revision lock so a stale, late tool call
returns STALE_PLAN instead of creating a Task, and gives the workbench real
domain semantics for delete / reorder / retarget / cancel (plan section 5.4,
8.6 and 8.7).
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from server.app.diagnosis.schemas import StrictModel
from pydantic import Field

# 合法迁移白名单：从 → {允许的目标}
STEP_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"QUEUED", "REMOVED_BY_USER", "SUPERSEDED", "BLOCKED", "WAITING_APPROVAL"},
    "QUEUED": {"DISPATCHING", "REMOVED_BY_USER", "CANCEL_REQUESTED", "CANCELLED", "SUPERSEDED", "SKIPPED_REUSED", "BLOCKED", "WAITING_APPROVAL"},
    "DISPATCHING": {"RUNNING", "FAILED", "CANCELLED", "CANCEL_REQUESTED", "SUPERSEDED"},
    "RUNNING": {"COMPLETED", "FAILED", "CANCELLED", "CANCEL_REQUESTED", "SUPERSEDED"},
    "CANCEL_REQUESTED": {"CANCELLED", "FAILED", "SUPERSEDED"},
    "WAITING_APPROVAL": {"QUEUED", "DISPATCHING", "REMOVED_BY_USER", "CANCEL_REQUESTED", "SUPERSEDED", "BLOCKED"},
}
TERMINAL_STEPS = {"COMPLETED", "FAILED", "CANCELLED", "REMOVED_BY_USER", "SUPERSEDED", "SKIPPED_REUSED", "BLOCKED"}

# 可调度的待执行步骤（Supervisor 从此状态领取）
SCHEDULABLE = {"QUEUED", "WAITING_APPROVAL"}


class PlanStepInput(StrictModel):
    step_id: Optional[str] = None
    kind: str = "COLLECTION"
    collector_id: Optional[str] = None
    target_refs: list[str] = Field(default_factory=list)
    purpose: str = ""
    hypothesis_refs: list[str] = Field(default_factory=list)
    expected_information: str = ""
    priority: int = 0
    priority_source: str = "AI"
    user_locked: bool = False
    depends_on: list[str] = Field(default_factory=list)
    risk: str = "READ_LOW"
    # E3.5：集群 Step 声明选择策略；非集群 Step 为空
    selection_strategy: Optional[str] = None
    status: str = "QUEUED"


class PlanUpdateInput(StrictModel):
    goal: str = "定位根因"
    steps: list[PlanStepInput] = Field(default_factory=list)
    expected_case_row_version: int = 0
    expected_scope_revision: int = 0
    expected_plan_revision: int = 0
    source: str = "deterministic"


class EvidenceReviewInput(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=20)
    reason_code: Optional[str] = Field(default=None, max_length=64)
    reason: Optional[str] = Field(default=None, max_length=1000)


class InvestigationPlanService:
    def __init__(self, repository: Any):
        self._repo = repository

    # ── 读取 ────────────────────────────────────────────────────────────
    def read_plan(self, case_id: str, tenant_id: str) -> dict[str, Any] | None:
        plan = self._repo.get_investigation_plan(case_id, tenant_id)
        if plan is None:
            return None
        plan["schedulable_step_ids"] = [
            step["step_id"] for step in plan.get("steps") or []
            if step.get("status") in SCHEDULABLE
        ]
        return plan

    # ── 写入：新修订，带乐观锁 ──────────────────────────────────────────
    def update_plan(
        self,
        case_id: str,
        tenant_id: str,
        payload: PlanUpdateInput,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        case = self._repo.get_incident_case(case_id, tenant_id)
        if case is None:
            raise ValueError("CASE_NOT_FOUND")
        if case["state"] in {"STOPPED", "RESOLVED"}:
            raise ValueError("CASE_TERMINAL")
        current_plan = self._repo.get_investigation_plan(case_id, tenant_id) or {}
        current_revision = int(current_plan.get("plan_revision") or 0)
        if payload.expected_plan_revision != current_revision:
            raise ValueError(f"STALE_PLAN:expected={payload.expected_plan_revision},current={current_revision}")
        if payload.expected_scope_revision != int(case.get("scope_revision") or 1):
            raise ValueError(
                f"STALE_SCOPE:expected={payload.expected_scope_revision},current={case.get('scope_revision')}"
            )
        if payload.expected_case_row_version != int(case.get("row_version") or 0):
            raise ValueError("STALE_CASE_VERSION")

        new_revision = current_revision + 1
        now_steps = self._repo.list_plan_steps(case_id, tenant_id, plan_revision=current_revision)
        if current_plan.get("plan_id") and now_steps:
            self._repo.supersede_plan_steps(current_plan["plan_id"])
        plan_payload = {
            "plan_id": f"plan-{uuid4().hex[:16]}",
            "case_id": case_id,
            "tenant_id": tenant_id,
            "plan_revision": new_revision,
            "scope_revision": case.get("scope_revision") or 1,
            "goal": payload.goal,
            "source": payload.source,
            "created_by": actor_id,
            "steps": [
                {
                    "step_id": f"step-{uuid4().hex[:16]}",
                    "kind": step.kind,
                    "collector_id": step.collector_id,
                    "target_refs": step.target_refs,
                    "purpose": step.purpose,
                    "hypothesis_refs": step.hypothesis_refs,
                    "expected_information": step.expected_information,
                    "priority": step.priority,
                    "priority_source": step.priority_source,
                    "user_locked": step.user_locked,
                    "depends_on": step.depends_on,
                    "risk": step.risk,
                    "selection_strategy": step.selection_strategy,
                    "status": step.status,
                }
                for step in payload.steps
            ],
        }
        return self._repo.create_investigation_plan(case_id, tenant_id, plan_payload)

    # ── 步骤状态机 ──────────────────────────────────────────────────────
    def transition_step(
        self,
        case_id: str,
        tenant_id: str,
        step_id: str,
        to_status: str,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        step = self._get_step(case_id, tenant_id, step_id)
        if step is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        allowed = STEP_TRANSITIONS.get(step.get("status") or "", set())
        if to_status not in allowed:
            raise ValueError(
                f"INVALID_STEP_TRANSITION:{step.get('status')}->{to_status}"
            )
        updated = self._repo.update_plan_step(
            case_id, tenant_id, step_id, {"status": to_status},
        )
        if updated is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        return updated

    def cancel_step(self, case_id: str, tenant_id: str, step_id: str, *, actor_id: str) -> dict[str, Any]:
        step = self._get_step(case_id, tenant_id, step_id)
        if step is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        status = step.get("status") or ""
        if status in {"CANCELLED", "COMPLETED", "REMOVED_BY_USER", "SUPERSEDED"}:
            return step
        if status in {"QUEUED", "WAITING_APPROVAL"}:
            return self.transition_step(case_id, tenant_id, step_id, "CANCELLED", actor_id=actor_id)
        if status in {"RUNNING", "DISPATCHING"}:
            return self.transition_step(case_id, tenant_id, step_id, "CANCEL_REQUESTED", actor_id=actor_id)
        raise ValueError(f"CANCEL_NOT_ALLOWED:{status}")

    def remove_step(self, case_id: str, tenant_id: str, step_id: str, *, actor_id: str) -> dict[str, Any]:
        step = self._get_step(case_id, tenant_id, step_id)
        if step is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        status = step.get("status") or ""
        if status in {"DRAFT", "QUEUED", "WAITING_APPROVAL"}:
            return self.transition_step(case_id, tenant_id, step_id, "REMOVED_BY_USER", actor_id=actor_id)
        raise ValueError(f"REMOVE_NOT_ALLOWED:{status}")

    def reprioritize_step(
        self,
        case_id: str,
        tenant_id: str,
        step_id: str,
        priority: int,
        *,
        actor_id: str,
        user_locked: bool = True,
    ) -> dict[str, Any]:
        step = self._get_step(case_id, tenant_id, step_id)
        if step is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        updated = self._repo.update_plan_step(
            case_id, tenant_id, step_id,
            {"priority": int(priority), "priority_source": "USER", "user_locked": user_locked},
        )
        if updated is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        return updated

    def retarget_step(
        self,
        case_id: str,
        tenant_id: str,
        step_id: str,
        *,
        target_refs: list[str] | None = None,
        collector_id: str | None = None,
        actor_id: str,
    ) -> dict[str, Any]:
        step = self._get_step(case_id, tenant_id, step_id)
        if step is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        status = step.get("status") or ""
        if status in {"RUNNING", "DISPATCHING"}:
            # 运行中改目标：先取消旧步骤（数据标 partial），由调用方创建新 Step 版本
            self.transition_step(case_id, tenant_id, step_id, "CANCEL_REQUESTED", actor_id=actor_id)
            return self._get_step(case_id, tenant_id, step_id)
        updates: dict[str, Any] = {"priority_source": "USER"}
        if target_refs is not None:
            updates["target_refs"] = list(target_refs)
        if collector_id is not None:
            updates["collector_id"] = collector_id
        updated = self._repo.update_plan_step(case_id, tenant_id, step_id, updates)
        if updated is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        return updated

    # ── 调度前的 revision 校验（STALE_PLAN 门禁）────────────────────────
    def verify_schedulable(
        self,
        case_id: str,
        tenant_id: str,
        step_id: str,
        *,
        plan_revision: int,
        scope_revision: int,
    ) -> dict[str, Any]:
        """Supervisor 在创建 Task 前必须调用；stale 修订直接拒绝。"""
        plan = self._repo.get_investigation_plan(case_id, tenant_id)
        if plan is None:
            raise ValueError(f"NO_PLAN:{case_id}")
        if int(plan["plan_revision"]) != int(plan_revision):
            raise ValueError(
                f"STALE_PLAN:expected={plan_revision},current={plan['plan_revision']}"
            )
        if int(plan["scope_revision"]) != int(scope_revision):
            raise ValueError(
                f"STALE_SCOPE:expected={scope_revision},current={plan['scope_revision']}"
            )
        step = self._get_step(case_id, tenant_id, step_id)
        if step is None:
            raise ValueError(f"PLAN_STEP_NOT_FOUND:{step_id}")
        if step.get("status") not in SCHEDULABLE:
            raise ValueError(f"STEP_NOT_SCHEDULABLE:{step.get('status')}")
        return step

    # ── Evidence Review ─────────────────────────────────────────────────
    def review_evidence(
        self,
        case_id: str,
        tenant_id: str,
        payload: EvidenceReviewInput,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        if payload.decision not in {"TRUSTED", "LOW_TRUST", "EXCLUDED", "RESTORED"}:
            raise ValueError(f"INVALID_REVIEW_DECISION:{payload.decision}")
        review = self._repo.add_evidence_review(case_id, tenant_id, {
            "review_id": f"review-{uuid4().hex[:16]}",
            "evidence_id": payload.evidence_id,
            "decision": payload.decision,
            "reason_code": payload.reason_code,
            "reason": payload.reason,
            "actor_id": actor_id,
        })
        # 排除后的 Evidence 从后续 Attachment/Prompt 投影中剥离
        if payload.decision == "EXCLUDED":
            self._apply_excluded_evidence(case_id, tenant_id, payload.evidence_id)
        return review

    def _apply_excluded_evidence(self, case_id: str, tenant_id: str, evidence_id: str) -> None:
        for attachment in self._repo.list_case_attachments(case_id, tenant_id):
            evidence_ids = attachment.get("evidence_ids") or []
            if evidence_id in evidence_ids:
                remaining = [item for item in evidence_ids if item != evidence_id]
                self._repo.update_case_attachment(
                    attachment.get("attachment_id"),
                    tenant_id,
                    updates={"evidence_ids": remaining},
                )

    def list_reviews(self, case_id: str, tenant_id: str,
                     evidence_id: str | None = None) -> list[dict[str, Any]]:
        return self._repo.list_evidence_reviews(case_id, tenant_id, evidence_id=evidence_id)

    def _get_step(self, case_id: str, tenant_id: str, step_id: str) -> Optional[dict[str, Any]]:
        for step in self._repo.list_plan_steps(case_id, tenant_id):
            if step.get("step_id") == step_id:
                return step
        return None
