"""Shadow Plan generation and pairwise comparison (E3).

When MINI_DROP_AGENT_RUNTIME=pi_shadow, Mini-Drop asks the Pi sidecar to produce
a plan for the same Case WITHOUT creating any Task.  The result is paired with
the deterministic planner's plan so quality can be compared before real
AUTO_INVESTIGATE is opened.  If the sidecar or model is unavailable, shadow
degrades to the deterministic plan (documented rules-engine fallback).
"""

from __future__ import annotations

from typing import Any

from server.app.agent_runtime.config import runtime_mode, AgentRuntimeMode
from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter, PiSidecarError
from server.app.diagnosis.investigation_plan import PlanStepInput, PlanUpdateInput


def build_deterministic_plan(
    case: dict[str, Any],
    *,
    active_hypotheses: list[dict[str, Any]],
    probe_candidates: list[dict[str, Any]],
) -> PlanUpdateInput:
    """确定性基线计划：从当前假设与探针候选中生成 READ_LOW 步骤。

    E3.5：当 Case 的 target_scope 声明了集群级范围时，首个 Step 为集群验证
    Step——target_refs 只携带 ``cluster:<id>`` 逻辑锚点，不携带任意主机名；
    成员枚举交给 Target Resolver + 选择策略在扇出时完成。
    """
    steps: list[PlanStepInput] = []
    used = set()
    target_scope = case.get("target_scope") or {}
    cluster_id = str(target_scope.get("cluster_id") or "")
    if cluster_id:
        strategy = str(target_scope.get("selection_strategy") or "REPRESENTATIVE")
        steps.append(PlanStepInput(
            kind="COLLECTION",
            collector_id="sys_metrics",
            target_refs=[f"cluster:{cluster_id}"],
            purpose="集群范围验证：按故障域分层采样确认是否集群级异常",
            hypothesis_refs=[str(h.get("hypothesis_id")) for h in active_hypotheses[:1]],
            priority=90,
            risk="READ_LOW",
            selection_strategy=strategy,
            status="QUEUED",
        ))
        used.add("sys_metrics")
    for candidate in probe_candidates[:6]:
        collector_id = str(candidate.get("collector_id") or candidate.get("probe_id") or "")
        if not collector_id or collector_id in used:
            continue
        used.add(collector_id)
        steps.append(PlanStepInput(
            kind="COLLECTION",
            collector_id=collector_id,
            target_refs=[str(x) for x in (candidate.get("target_refs") or [])],
            purpose=str(candidate.get("rationale") or candidate.get("purpose") or "验证假设"),
            hypothesis_refs=[str(h.get("hypothesis_id")) for h in active_hypotheses[:2]],
            priority=int(candidate.get("priority") or 60),
            risk=str(candidate.get("risk") or "READ_LOW"),
            status="QUEUED",
        ))
    if not steps:
        steps.append(PlanStepInput(
            kind="COLLECTION", collector_id="sys_metrics",
            purpose="收集基础系统指标以建立证据基线",
            priority=50, risk="READ_LOW", status="QUEUED",
        ))
    return PlanUpdateInput(
        goal=str(case.get("problem_description", ""))[:200] or "定位根因",
        steps=steps,
        expected_case_row_version=case.get("row_version") or 0,
        expected_scope_revision=case.get("scope_revision") or 1,
        expected_plan_revision=0,
        source="deterministic",
    )


def plan_signature(plan: PlanUpdateInput) -> dict[str, Any]:
    return {
        "goal": plan.goal,
        "collectors": sorted(step.collector_id or "" for step in plan.steps),
        "risk": sorted({step.risk for step in plan.steps}),
        "step_count": len(plan.steps),
    }


def compare_plans(
    deterministic: PlanUpdateInput,
    shadow: PlanUpdateInput | None,
) -> dict[str, Any]:
    det = plan_signature(deterministic)
    shadow_sig = plan_signature(shadow) if shadow else None
    if shadow_sig is None:
        return {
            "shadow_available": False,
            "deterministic": det,
            "collectors_identical": None,
            "shadow_delta": [],
        }
    det_collectors = set(det["collectors"])
    shadow_collectors = set(shadow_sig["collectors"])
    return {
        "shadow_available": True,
        "deterministic": det,
        "shadow": shadow_sig,
        "collectors_identical": det_collectors == shadow_collectors,
        "shadow_only_collectors": sorted(shadow_collectors - det_collectors),
        "deterministic_only_collectors": sorted(det_collectors - shadow_collectors),
    }


async def request_shadow_plan(
    case: dict[str, Any],
    *,
    sidecar_url: str | None,
    deterministic_plan: PlanUpdateInput,
) -> tuple[PlanUpdateInput | None, str]:
    """请求 Pi Sidecar 的 Shadow 计划。不可用时降级为确定性计划。"""
    if runtime_mode() not in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        return None, "runtime_not_pi"
    if not sidecar_url:
        return None, "sidecar_not_configured"
    try:
        adapter = PiAgentRuntimeAdapter(sidecar_url, timeout=15.0)
        result = adapter.submit_shadow_plan(
            case.get("case_id") or "",
            _context_snapshot_from_case(case),
        )
        steps = result.get("steps") or []
        shadow = PlanUpdateInput(
            goal=str(result.get("goal") or deterministic_plan.goal),
            steps=[
                PlanStepInput(
                    kind="COLLECTION",
                    collector_id=str(step.get("collector_id") or "sys_metrics"),
                    purpose=str(step.get("purpose") or "Shadow 补采"),
                    risk=str(step.get("risk") or "READ_LOW"),
                    priority=int(step.get("priority") or 50),
                    status="QUEUED",
                )
                for step in steps
            ],
            expected_case_row_version=case.get("row_version") or 0,
            expected_scope_revision=case.get("scope_revision") or 1,
            expected_plan_revision=0,
            source="pi_shadow",
        )
        return shadow, "ok"
    except (PiSidecarError, Exception):  # noqa: BLE001 — 降级不阻断
        return None, "sidecar_unavailable"


def _context_snapshot_from_case(case: dict[str, Any]) -> Any:
    from server.app.agent_runtime.port import CaseContextSnapshot
    return CaseContextSnapshot(
        case_id=case.get("case_id") or "",
        tenant_id="local-development",
        case_goal=str(case.get("problem_description", ""))[:500],
        target_scope=case.get("target_scope") or {},
        autonomy_mode=case.get("run_mode") or "COLLABORATE",
        plan_revision=0,
        scope_revision=case.get("scope_revision") or 1,
    )
