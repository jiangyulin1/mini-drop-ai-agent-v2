"""Investigation planning, control, and Agent-turn HTTP endpoints."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.dispatcher import get_runtime
from server.app.agent_runtime.port import AgentTurnInput
from server.app.ai_provider import model_audit_scope
from server.app.case_collaboration import (
    CaseMessageRequest,
    EvidenceReviewRequest,
    PlanUpdateRequest,
    ReprioritizeStepRequest,
    RetargetStepRequest,
    StartCaseDiagnosisRequest,
    build_case_context_packet,
    build_case_diagnosis_query,
)
from server.app.common_utils import status_value
from server.app.diagnosis.agent_runtime import (
    AgentTurnIntent,
    AgentTurnRequest,
    AgentTurnResult,
    DeploymentAssessmentRequest,
    assess_deployment_capacity,
    build_case_evidence_chain,
    build_observability_tool_plan,
    classify_turn,
    execute_tool_plan,
    parse_deployment_requirements,
    render_understanding_answer,
)
from server.app.diagnosis.investigation_plan import EvidenceReviewInput, PlanUpdateInput
from server.app.diagnosis.investigation_planner import (
    InvestigationActionCandidate,
    evaluate_investigation_stop,
    rank_investigation_actions,
)
from server.app.diagnosis.proposal_card import build_proposal_cards
from server.app.diagnosis.reference_resolver import ResourceRef
from server.app.diagnosis.schemas import CreateDiagnosisRequest, TERMINAL_DIAGNOSIS_STATUSES
from server.app.diagnosis.v6_policy import route_disposition
from server.app.http.auth import (
    request_principal as _request_principal,
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.routes.cases import _case_agent_progress
from server.app.runtime_services import (
    diagnosis_orchestrator,
    evidence_attachment_service,
    investigation_plan_service,
    repo,
    source_gateway,
)
from server.app.schemas import APIResponse
from server.app.state_machine import Actor, TaskStatus
from server.app.v6_routes import _build_runtime_case_context, _case_investigation_footprint


router = APIRouter()

# ── E2 持久化调查计划与双通道控制 ────────────────────────────────


@router.get("/api/v1/cases/{case_id}/plans/current")
def get_case_investigation_plan(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = investigation_plan_service.read_plan(case_id, tenant_id)
    return APIResponse(data=plan if plan else {"plan_id": None, "steps": []})


@router.put("/api/v1/cases/{case_id}/plans")
def update_case_investigation_plan(
    case_id: str,
    payload: PlanUpdateRequest,
    request: Request,
) -> APIResponse:
    """写入新 Plan Revision（乐观锁）。旧修订的延迟调用返回 STALE_PLAN。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    try:
        plan = investigation_plan_service.update_plan(
            case_id,
            tenant_id,
            PlanUpdateInput(**payload.model_dump(mode="json")),
            actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_case_event(
        case_id, tenant_id, event_type="plan_updated",
        payload={"plan_revision": plan["plan_revision"], "actor_id": principal_id},
        actor_id=principal_id,
    )
    return APIResponse(data=plan)


def _cancel_case_tasks(case_id: str, tenant_id: str) -> list[str]:
    """G5：停止/解决 Case 时取消所有 Case 派生且仍活跃的原生 Task。"""
    cancelled: list[str] = []
    for task in list(getattr(repo, "tasks", {}).values()):
        options = (getattr(task, "request_params", None) or {}).get("options") or {}
        if str(options.get("case_id") or "") != case_id:
            continue
        if status_value(getattr(task, "status", "")) in {
            TaskStatus.CANCELLED.value, TaskStatus.DONE.value, TaskStatus.FAILED.value,
        }:
            continue
        repo.cancel_task(str(getattr(task, "id", "") or ""), "Case 已停止/解决", Actor.WEB)
        cancelled.append(str(getattr(task, "id", "") or ""))
    return list(dict.fromkeys(cancelled))


def _cancel_step_tasks(case_id: str, tenant_id: str, step_id: str) -> list[str]:
    """G5：用户取消 PlanStep 时同步取消其原生 Task / Fanout 子任务。"""
    cancelled: list[str] = []
    task = repo.get_task_by_diagnosis_step_id(step_id)
    if task is not None and status_value(task.status) not in {
        TaskStatus.CANCELLED.value, TaskStatus.DONE.value, TaskStatus.FAILED.value,
    }:
        repo.cancel_task(task.id, "用户取消计划步骤", Actor.WEB)
        cancelled.append(task.id)
    for run in repo.list_fanout_runs(case_id, tenant_id):
        if str(run.get("plan_step_id") or "") != step_id:
            continue
        for task_id in run.get("task_ids") or []:
            fanout_task = repo.tasks.get(str(task_id))
            if fanout_task is None:
                continue
            if status_value(fanout_task.status) in {
                TaskStatus.CANCELLED.value, TaskStatus.DONE.value, TaskStatus.FAILED.value,
            }:
                continue
            repo.cancel_task(str(task_id), "用户取消集群计划步骤", Actor.WEB)
            cancelled.append(str(task_id))
    return list(dict.fromkeys(cancelled))


@router.post("/api/v1/cases/{case_id}/steps/{step_id}/cancel")
def cancel_case_plan_step(case_id: str, step_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    try:
        step = investigation_plan_service.cancel_step(
            case_id, tenant_id, step_id, actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    cancelled_task_ids = _cancel_step_tasks(case_id, tenant_id, step_id)
    repo.record_case_event(
        case_id, tenant_id, event_type="step_cancelled",
        payload={
            "step_id": step_id,
            "status": step.get("status"),
            "actor_id": principal_id,
            "cancelled_task_ids": cancelled_task_ids,
        },
        actor_id=principal_id,
    )
    return APIResponse(data=step)


@router.post("/api/v1/cases/{case_id}/steps/{step_id}/remove")
def remove_case_plan_step(case_id: str, step_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    try:
        step = investigation_plan_service.remove_step(
            case_id, tenant_id, step_id, actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_case_event(
        case_id, tenant_id, event_type="step_removed",
        payload={"step_id": step_id, "status": step.get("status"), "actor_id": principal_id},
        actor_id=principal_id,
    )
    return APIResponse(data=step)


@router.post("/api/v1/cases/{case_id}/steps/{step_id}/reprioritize")
def reprioritize_case_plan_step(
    case_id: str,
    step_id: str,
    payload: ReprioritizeStepRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    try:
        step = investigation_plan_service.reprioritize_step(
            case_id, tenant_id, step_id, payload.priority,
            actor_id=principal_id, user_locked=payload.user_locked,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=step)


@router.post("/api/v1/cases/{case_id}/steps/{step_id}/retarget")
def retarget_case_plan_step(
    case_id: str,
    step_id: str,
    payload: RetargetStepRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
    current_step = next(
        (item for item in (plan.get("steps") or []) if item.get("step_id") == step_id),
        None,
    )
    if current_step is not None and current_step.get("status") in {"RUNNING", "DISPATCHING"}:
        _cancel_step_tasks(case_id, tenant_id, step_id)
    try:
        step = investigation_plan_service.retarget_step(
            case_id, tenant_id, step_id,
            target_refs=payload.target_refs,
            collector_id=payload.collector_id,
            actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=step)


@router.post("/api/v1/cases/{case_id}/evidence/{evidence_id}/reviews")
def review_case_evidence(
    case_id: str,
    evidence_id: str,
    payload: EvidenceReviewRequest,
    request: Request,
) -> APIResponse:
    """评价证据（LOW_TRUST/EXCLUDED），随后重算受影响假设。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    review_payload = payload.model_copy(update={"evidence_id": evidence_id})
    try:
        review = investigation_plan_service.review_evidence(
            case_id, tenant_id,
            EvidenceReviewInput(**review_payload.model_dump(mode="json")),
            actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_case_event(
        case_id, tenant_id, event_type="evidence_reviewed",
        payload={"evidence_id": evidence_id, "decision": review["decision"], "actor_id": principal_id},
        actor_id=principal_id,
    )
    return APIResponse(data=review)


@router.get("/api/v1/cases/{case_id}/evidence-reviews")
def list_case_evidence_reviews(
    case_id: str,
    request: Request,
    evidence_id: str | None = None,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    items = investigation_plan_service.list_reviews(
        case_id, tenant_id, evidence_id=evidence_id,
    )
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/context-packets")
def list_case_context_packets(
    case_id: str,
    request: Request,
    limit: int = 100,
    offset: int = 0,
) -> APIResponse:
    _require_role(request, "operator")
    items = repo.list_context_packets(
        case_id,
        _request_tenant(),
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    )
    if items is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/model-attempts")
def list_case_model_attempts(
    case_id: str,
    request: Request,
    limit: int = 100,
    offset: int = 0,
) -> APIResponse:
    _require_role(request, "operator")
    items = repo.list_model_attempts(
        case_id,
        _request_tenant(),
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    )
    if items is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/hypotheses")
def get_case_hypotheses(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    graph = repo.get_case_hypothesis_graph(case_id, _request_tenant())
    if graph is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=graph)


@router.get("/api/v1/cases/{case_id}/iterations")
def get_case_iterations(
    case_id: str,
    request: Request,
    limit: int = 100,
    offset: int = 0,
) -> APIResponse:
    _require_role(request, "operator")
    items = repo.list_investigation_iterations(
        case_id,
        _request_tenant(),
        limit=min(max(limit, 1), 500),
        offset=max(offset, 0),
    )
    if items is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/api/v1/cases/{case_id}/diagnoses")
def start_case_diagnosis(
    case_id: str,
    payload: StartCaseDiagnosisRequest,
    request: Request,
) -> APIResponse:
    """Start the existing deterministic diagnosis workflow under Case governance."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    if case["run_mode"] == "ASSIST":
        raise HTTPException(
            status_code=409,
            detail="CASE_ASSIST_MODE_DOES_NOT_START_AUTOMATED_DIAGNOSIS",
        )
    if case["state"] in {"NEEDS_SCOPE_CONFIRMATION", "PAUSED", "STOPPED", "RESOLVED"}:
        raise HTTPException(status_code=409, detail="CASE_NOT_INVESTIGATABLE")
    if case.get("diagnosis_session_id"):
        raise HTTPException(status_code=409, detail="CASE_DIAGNOSIS_ALREADY_ATTACHED")
    if payload.expected_row_version is not None and case["row_version"] != payload.expected_row_version:
        raise HTTPException(status_code=409, detail="CASE_VERSION_CONFLICT")

    events = repo.list_case_events(case_id, tenant_id, limit=20, after_id=0) or []
    grants = repo.list_authorization_grants(
        principal_id=principal_id,
        tenant_id=tenant_id,
        include_inactive=False,
    )
    prior_iterations = repo.list_investigation_iterations(
        case_id, tenant_id, limit=500, offset=0,
    ) or []
    iteration_no = max(
        (int(item.get("iteration_no", -1)) for item in prior_iterations),
        default=-1,
    ) + 1
    case_scope = case.get("target_scope") or {}
    case_service = case_scope.get("service_id")
    if not case_service and case_scope.get("service_ids"):
        case_service = case_scope["service_ids"][0]
    recent_changes = repo.list_change_records(
        tenant_id=tenant_id,
        service_id=case_service,
        environment=case.get("environment"),
        limit=10,
    )
    packet_payload, packet_stats, packet_hash = build_case_context_packet(
        case,
        recent_events=events,
        grants=grants,
        recent_changes=recent_changes,
        iteration_no=iteration_no,
        required_output_schema="normalized-diagnosis-intent.v1",
    )
    packet = repo.create_context_packet({
        "case_id": case_id,
        "tenant_id": tenant_id,
        "schema_version": "case-context.v1",
        "purpose": "diagnosis_intent",
        "iteration_no": iteration_no,
        "payload": packet_payload,
        "projection_stats": packet_stats,
        "source_versions": {
            "context_builder": "case-context-builder.v1",
            "source_registry": "source-registry.v1",
        },
        "content_hash": packet_hash,
        "created_by": principal_id,
    })

    target_scope = case.get("target_scope") or {}
    service_id = target_scope.get("service_id")
    if not service_id and target_scope.get("service_ids"):
        service_id = target_scope["service_ids"][0]
    diagnosis_request = CreateDiagnosisRequest.model_validate({
        "query": build_case_diagnosis_query(
            case,
            events,
            recent_changes=recent_changes,
        ),
        "context": {
            "service_id": service_id,
            "environment": case["environment"],
            "time_range": case.get("time_range") or None,
            "instances": target_scope.get("instances") or [],
            "dependencies": target_scope.get("dependencies") or [],
        },
        "budget_profile": payload.budget_profile,
        "budget": payload.budget.model_dump(mode="json") if payload.budget else None,
        "analysis_strategy": payload.analysis_strategy.value,
        "evidence_time_policy": payload.evidence_time_policy.model_dump(mode="json"),
    })
    diagnosis: dict[str, Any] | None = None
    try:
        with model_audit_scope(
            case_id=case_id,
            tenant_id=tenant_id,
            context_packet_id=packet["context_packet_id"],
            prompt_version="diagnosis-intent.v1",
            output_schema="normalized-diagnosis-intent.v1",
            recorder=repo.record_model_attempt,
        ):
            # E1：诊断消费入口统一——initial_task_ids + ACCEPTED task 附件
            # 一起作为初始证据；target_scope.evidence_task_ids 旧字段不再单独读取。
            attachment_task_ids = evidence_attachment_service.active_task_ids(case_id, tenant_id)
            initial_task_ids = list(dict.fromkeys(
                (case.get("initial_task_ids") or []) + attachment_task_ids
            ))
            diagnosis = diagnosis_orchestrator.create(
                diagnosis_request,
                creator_id=principal_id,
                initial_task_ids=initial_task_ids,
            )
        graph = repo.sync_case_hypothesis_graph(
            case_id,
            tenant_id,
            graph=diagnosis.get("hypothesis_graph") or {},
            source="diagnosis_session",
            actor_id=principal_id,
        )
        ranked_actions = rank_investigation_actions([
            InvestigationActionCandidate(
                action_id="diagnosis-orchestrator.start",
                source_id="mini-drop-control-plane",
                operation="diagnosis.start",
                expected_information_gain=0.8,
                source_reliability=0.95,
                probability_of_success=0.95,
                hypothesis_discrimination=0.75,
                latency_cost=1,
                resource_cost=0.5,
                monetary_cost=0,
                risk_cost=0.2,
                approval_wait_cost=0,
            ),
        ])
        diagnosis_status = diagnosis.get("status")
        stop_decision = evaluate_investigation_stop(
            budget_exhausted=diagnosis_status == "BUDGET_EXHAUSTED",
            source_unavailable=diagnosis_status == "TOPOLOGY_UNAVAILABLE",
            scope_complete=diagnosis_status != "NEEDS_SCOPE_CONFIRMATION",
        )
        iteration = repo.create_investigation_iteration({
            "case_id": case_id,
            "tenant_id": tenant_id,
            "iteration_no": iteration_no,
            "context_packet_id": packet["context_packet_id"],
            "status": "COMPLETED",
            "hypothesis_changes": [
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "to_status": item["status"],
                    "revision": item["revision"],
                }
                for item in graph["hypotheses"]
            ],
            "candidate_actions": ranked_actions,
            "selected_action": ranked_actions[0] if ranked_actions else {},
            "policy_decision": {
                "decision": "AUTO_REVIEWED",
                "operation_class": "COLLECT",
                "impact_level": "I1",
                "reason_codes": ["REGISTERED_DIAGNOSIS_WORKFLOW"],
            },
            "cost": diagnosis.get("budget_used") or {},
            "result": {
                "diagnosis_session_id": diagnosis["diagnosis_id"],
                "diagnosis_status": diagnosis_status,
            },
            "stop_decision": stop_decision,
            "created_by": principal_id,
        })
        updated_case = repo.attach_case_diagnosis(
            case_id,
            tenant_id,
            diagnosis_id=diagnosis["diagnosis_id"],
            actor_id=principal_id,
            expected_row_version=payload.expected_row_version,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        if diagnosis is not None:
            try:
                diagnosis_orchestrator.cancel(
                    diagnosis["diagnosis_id"],
                    "Case 关联失败，取消孤立诊断",
                )
            except ValueError:
                pass
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={
        "case": updated_case,
        "diagnosis": diagnosis,
        "context_packet_id": packet["context_packet_id"],
        "investigation_iteration_id": iteration["iteration_id"],
    })


@router.get("/api/v1/cases/{case_id}/proposals")
def list_case_proposals(case_id: str, request: Request) -> APIResponse:
    """提案卡：把 Case 诊断的待审批动作派生为可读卡片（依据/作用/影响/成本）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    actions: list[dict] = []
    diagnosis_id = case.get("diagnosis_session_id")
    if diagnosis_id:
        session = diagnosis_orchestrator.store.get_detail(diagnosis_id)
        conclusion = (session or {}).get("latest_conclusion") or {}
        actions = conclusion.get("actions") or []
    cards = build_proposal_cards(actions, step_id_prefix=f"{case_id}:")
    return APIResponse(data={"case_id": case_id, "proposals": cards})


@router.get("/api/v1/cases/{case_id}/understanding")
def get_case_current_understanding(case_id: str, request: Request) -> APIResponse:
    """Return the current programmatic understanding from live Case evidence."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    graph = repo.get_case_hypothesis_graph(case_id, tenant_id) or {
        "hypotheses": [], "edges": [],
    }
    diagnosis_id = case.get("diagnosis_session_id")
    detail = (
        diagnosis_orchestrator.store.get_detail(diagnosis_id)
        if diagnosis_id else {}
    ) or {}
    recent_changes = repo.list_change_records(
        tenant_id=tenant_id,
        service_id=(case.get("target_scope") or {}).get("service_id"),
        environment=case.get("environment"),
        limit=10,
    )
    packet, _, _ = build_case_context_packet(
        case,
        diagnosis={"hypothesis_graph": graph},
        evidence=detail.get("evidence") or [],
        recent_changes=recent_changes,
        required_output_schema="current-understanding.v1",
    )
    return APIResponse(data={
        "case_id": case_id,
        "diagnosis_id": diagnosis_id,
        "current_understanding": packet["current_understanding"],
    })


@router.post("/api/v1/cases/{case_id}/messages")
def append_incident_case_message(
    case_id: str,
    payload: CaseMessageRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    try:
        result = repo.append_case_message(
            case_id,
            _request_tenant(),
            actor_id=_request_principal(request),
            content=payload.content,
            kind=payload.kind,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=result)






@router.post("/api/v1/cases/{case_id}/deployment-assessment")
@router.post("/api/v1/cases/{case_id}/deployment-assessments")
def assess_case_deployment(
    case_id: str,
    payload: DeploymentAssessmentRequest,
    request: Request,
) -> APIResponse:
    """G9：独立部署承载评估入口；缺事实必须返回 INSUFFICIENT_DATA。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = build_observability_tool_plan(
        case,
        intent=AgentTurnIntent.DEPLOYMENT_ASSESSMENT,
        max_tool_calls=payload.max_tool_calls,
        source_definitions=source_gateway.list_sources(),
    )
    tool_evidence: list[dict[str, Any]] = []
    if payload.execute_safe_tools:
        _, tool_evidence = execute_tool_plan(
            source_gateway,
            plan,
            tenant_id=tenant_id,
            case_id=case_id,
            principal_id=principal_id,
        )
    assessment = assess_deployment_capacity(
        payload.deployment_requirements,
        target_scope=case.get("target_scope") or {},
        tool_evidence=tool_evidence,
    )
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="deployment_assessment_completed",
        payload=assessment.model_dump(mode="json"),
        actor_id=principal_id,
    )
    return APIResponse(data=assessment.model_dump(mode="json"))


@router.post("/api/v1/cases/{case_id}/agent/turn")
def run_incident_case_agent_turn(
    case_id: str,
    payload: AgentTurnRequest,
    request: Request,
) -> APIResponse:
    """Run one conversation-first Agent turn over the durable Case.

    The response exposes an auditable decision/evidence chain, never hidden
    model reasoning.  Every external read is selected from SourceRegistry and
    executed through SourceGateway, so MCP data receives the same scope,
    redaction, result-budget and grant controls as native sources.
    """
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")

    # v6 canonical Turn request contract: references, stable client command id,
    # requested disposition and after_attach semantics.
    references = list(payload.references or [])
    attachment_results: list[dict[str, Any]] = []
    if references:
        attachment_results = evidence_attachment_service.attach_resources(
            case,
            tenant_id,
            [ResourceRef(**item) for item in references],
            actor_id=principal_id,
            purpose="turn_reference",
            source="user_mention",
        )
        rejected = [item for item in attachment_results if item.get("result") != "ACCEPTED"]
        if rejected:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REFERENCE_REJECTED",
                    "items": attachment_results,
                },
            )

    intent = classify_turn(payload.message, payload.intent)
    disposition, side_effect_policy, needs_user = route_disposition(
        payload.message,
        requested_disposition=payload.requested_disposition,
        execute_safe_tools=payload.execute_safe_tools,
        case_state=case["state"],
    )
    if references and payload.requested_disposition is None:
        disposition = str(payload.after_attach or "ANSWER_ONLY").upper()
        side_effect_policy = "READ_ONLY" if disposition == "ANSWER_ONLY" else (
            "AUTO_READ_LOW" if payload.execute_safe_tools else "PROPOSE_ONLY"
        )

    terminal = case["state"] in {"STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}
    if terminal and disposition not in {"ANSWER_ONLY", "ATTACH_EVIDENCE"}:
        raise HTTPException(status_code=409, detail="CASE_TERMINAL_NEW_INVESTIGATION_REQUIRES_NEW_RUN")

    footprint_before = _case_investigation_footprint(case_id, tenant_id)
    message_kind = "explanation_request" if disposition == "ANSWER_ONLY" else "answer"
    repo.append_case_message(
        case_id,
        tenant_id,
        actor_id=principal_id,
        content=payload.message,
        kind=message_kind,
    )
    case = repo.get_incident_case(case_id, tenant_id) or case
    deterministic_turn_id = f"turn_{secrets.token_hex(12)}"
    if runtime_mode() not in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        if hasattr(repo, "record_agent_runtime_turn"):
            repo.record_agent_runtime_turn(
                turn_id=deterministic_turn_id,
                case_id=case_id,
                tenant_id=tenant_id,
                runtime_session_id="deterministic",
                runtime_generation=1,
                user_message=payload.message,
                requested_mode="deterministic",
                status="COMPLETED",
                accepted_mode="deterministic",
                detail="deterministic answer",
                idempotency_key=f"runtime-turn:{case_id}:{deterministic_turn_id}",
                disposition=disposition,
                side_effect_policy=side_effect_policy,
                actor_id=principal_id,
                client_command_id=payload.client_command_id,
            )

    runtime_fallback_reason: str | None = None
    # PI is preferred when configured. Provider/Sidecar failure falls back to
    # the deterministic path without granting any additional side effects.
    if runtime_mode() in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        try:
            runtime = get_runtime()
            binding = runtime.start_or_resume(_build_runtime_case_context(
                case, tenant_id, disposition=disposition, side_effect_policy=side_effect_policy,
            ))
            if hasattr(repo, "upsert_agent_runtime_binding"):
                repo.upsert_agent_runtime_binding(
                    case_id,
                    tenant_id,
                    runtime_type=binding.runtime_type,
                    runtime_version=binding.runtime_version,
                    runtime_session_id=binding.runtime_session_id,
                    runtime_generation=binding.runtime_generation,
                    status=binding.status,
                    last_event_seq=binding.last_event_seq,
                    last_context_snapshot_id=binding.last_context_snapshot_id,
                    lease_owner=binding.lease_owner,
                )
            accepted = runtime.submit_turn(
                case_id,
                AgentTurnInput(
                    case_id=case_id,
                    message=payload.message,
                    references=payload.model_dump(mode="json").get("references", []),
                    requested_mode=payload.intent.value if payload.intent else None,
                    client_command_id=None,
                ),
            )
            if hasattr(repo, "record_agent_runtime_turn"):
                repo.record_agent_runtime_turn(
                    turn_id=accepted.turn_id,
                    case_id=case_id,
                    tenant_id=tenant_id,
                    runtime_session_id=binding.runtime_session_id,
                    runtime_generation=binding.runtime_generation,
                    user_message=payload.message,
                    requested_mode=payload.intent.value if payload.intent else None,
                    status="ACCEPTED",
                    accepted_mode=accepted.mode,
                    detail=accepted.detail,
                    idempotency_key=f"runtime-turn:{case_id}:{accepted.turn_id}",
                    disposition=disposition,
                    side_effect_policy=side_effect_policy,
                    actor_id=principal_id,
                    client_command_id=payload.client_command_id,
                )
        except RuntimeError as exc:
            runtime_fallback_reason = str(exc)[:200]
            repo.record_case_event(
                case_id,
                tenant_id,
                event_type="agent_runtime_turn_rejected",
                payload={
                    "status": "deterministic_fallback",
                    "reason": runtime_fallback_reason,
                },
                actor_id="mini-drop-agent-runtime",
            )
            repo.record_agent_runtime_turn(
                turn_id=deterministic_turn_id,
                case_id=case_id,
                tenant_id=tenant_id,
                runtime_session_id="deterministic-fallback",
                runtime_generation=1,
                user_message=payload.message,
                requested_mode="deterministic_fallback",
                status="COMPLETED",
                accepted_mode="deterministic_fallback",
                detail=runtime_fallback_reason,
                idempotency_key=f"runtime-turn:{case_id}:{deterministic_turn_id}",
                disposition=disposition,
                side_effect_policy=side_effect_policy,
                actor_id=principal_id,
                client_command_id=payload.client_command_id,
            )
        else:
            return _runtime_accepted_response(case_id, tenant_id, accepted, intent)

    case = repo.get_incident_case(case_id, tenant_id) or case
    graph = repo.get_case_hypothesis_graph(case_id, tenant_id) or {"hypotheses": [], "edges": []}
    diagnosis_id = case.get("diagnosis_session_id")
    diagnosis = (
        diagnosis_orchestrator.store.get_detail(diagnosis_id)
        if diagnosis_id else {}
    ) or {}
    evidence = diagnosis.get("evidence") or []
    understanding = build_case_context_packet(
        case,
        diagnosis={"hypothesis_graph": graph},
        evidence=evidence,
        required_output_schema="case-agent-turn.v1",
    )[0]["current_understanding"]
    assistant_message, decisions, evidence_chain = render_understanding_answer(understanding)
    evidence_chain = build_case_evidence_chain(graph, evidence) or evidence_chain
    contradictions = list(understanding.get("contradictions") or [])
    limitations = list(understanding.get("missing") or [])
    if runtime_fallback_reason:
        decisions.insert(0, "Pi Runtime 不可用，已自动切换 deterministic Runtime")
        limitations.append(f"runtime_fallback:{runtime_fallback_reason}")
    next_actions: list[dict[str, Any]] = []
    tool_calls = []
    deployment_assessment = None
    status = "answered"

    if intent == AgentTurnIntent.DEPLOYMENT_ASSESSMENT:
        requirements = payload.deployment_requirements or parse_deployment_requirements(payload.message)
        plan = build_observability_tool_plan(
            case,
            intent=intent,
            max_tool_calls=payload.max_tool_calls,
            source_definitions=source_gateway.list_sources(),
        )
        tool_evidence: list[dict[str, Any]] = []
        if payload.execute_safe_tools:
            tool_calls, tool_evidence = execute_tool_plan(
                source_gateway,
                plan,
                tenant_id=tenant_id,
                case_id=case_id,
                principal_id=principal_id,
            )
        else:
            tool_calls = plan
        deployment_assessment = assess_deployment_capacity(
            requirements,
            target_scope=case.get("target_scope") or {},
            tool_evidence=tool_evidence,
        )
        assistant_message = deployment_assessment.summary
        decisions = [
            f"承载力判定：{deployment_assessment.verdict}",
            *deployment_assessment.assumptions,
        ]
        evidence_chain = [
            {
                "evidence_id": item["evidence_id"],
                "source_id": item["source_id"],
                "content_hash": item["content_hash"],
                "projection_hash": item["projection_hash"],
            }
            for item in tool_evidence
        ]
        limitations = deployment_assessment.missing_inputs
        if any(item.status == "approval_required" for item in tool_calls):
            status = "tool_approval_required"
        elif deployment_assessment.verdict == "insufficient_data":
            status = "insufficient_data"
        next_actions = _deployment_next_actions(deployment_assessment.model_dump(mode="json"), tool_calls)
    elif intent == AgentTurnIntent.STATUS:
        progress = _case_agent_progress(case)
        assistant_message = (
            f"当前阶段：{progress['phase_label']}。"
            f"诊断状态：{progress.get('diagnosis_status') or '尚未启动'}；"
            f"已执行 {progress['actions_executed']}/{progress['max_actions']} 个恢复动作。"
        )
        decisions = [assistant_message]
        if case.get("summary", {}).get("need_you", {}).get("required"):
            status = "needs_user"
            next_actions.append({
                "type": "need_user",
                "description": case["summary"]["need_you"].get("question") or "请补充调查范围",
            })
    elif intent == AgentTurnIntent.EXPLAIN:
        if not graph.get("hypotheses"):
            assistant_message = "当前还没有可解释的诊断假设。请先启动调查或补充目标范围。"
            status = "needs_user" if case["state"] == "NEEDS_SCOPE_CONFIRMATION" else "insufficient_data"
        if understanding.get("next"):
            next_actions.append({"type": "investigate", "description": understanding["next"]})
    else:
        if case["state"] == "NEEDS_SCOPE_CONFIRMATION":
            assistant_message = "我已记录补充信息，但还不能安全选择探针：请先确认目标服务、Worker 和 PID。"
            status = "needs_user"
            next_actions.append({"type": "confirm_scope", "description": "确认服务实例、宿主机或 PID 范围"})
        else:
            current_status = str(diagnosis.get("status") or "")
            if (
                diagnosis_id
                and current_status not in TERMINAL_DIAGNOSIS_STATUSES
                and intent != AgentTurnIntent.CORRECT
            ):
                assistant_message = f"我已把这条信息加入证据上下文，当前诊断仍在推进（{current_status}）。"
                status = "diagnosis_in_progress"
            else:
                if diagnosis_id:
                    try:
                        diagnosis_orchestrator.cancel(diagnosis_id, "用户发起新一轮 Agent 调查")
                    except ValueError:
                        pass
                    case = repo.correct_incident_case(
                        case_id,
                        tenant_id,
                        actor_id=principal_id,
                        changes={"target_scope": case.get("target_scope") or {}},
                        reason="用户补充或纠正事实，启动新一轮 Agent 调查",
                        expected_row_version=case.get("row_version"),
                    ) or case
                if case.get("run_mode") == "ASSIST":
                    assistant_message = "当前 Case 是辅助模式，我已记录事实但不会自动启动探针；切换到协作或授权自治模式后可继续。"
                    status = "needs_user"
                    next_actions.append({"type": "change_run_mode", "description": "将 Case 切换为 COLLABORATE 或 AUTHORIZED_AUTONOMY"})
                else:
                    try:
                        started = start_case_diagnosis(
                            case_id,
                            StartCaseDiagnosisRequest(expected_row_version=case.get("row_version")),
                            request,
                        ).data
                    except HTTPException as exc:
                        assistant_message = f"我已记录本轮信息，但自动调查暂未启动：{exc.detail}。"
                        status = "needs_user"
                        limitations = [str(exc.detail)]
                        next_actions.append({"type": "resolve_blocker", "description": str(exc.detail)})
                    else:
                        started_diagnosis = started.get("diagnosis") or {}
                        assistant_message = "我已基于当前对话、范围、变更记录和已有证据启动新一轮调查。"
                        status = "diagnosis_requested"
                        next_actions.append({
                            "type": "diagnosis",
                            "diagnosis_id": started_diagnosis.get("diagnosis_id"),
                            "status": started_diagnosis.get("status"),
                        })

    side_effect_delta: dict[str, Any] = {}
    if intent in {AgentTurnIntent.EXPLAIN, AgentTurnIntent.STATUS}:
        footprint_after = _case_investigation_footprint(case_id, tenant_id)
        side_effect_delta = {
            key: footprint_after[key] - footprint_before[key]
            for key in footprint_before
        }
    result = AgentTurnResult(
        turn_id=deterministic_turn_id,
        intent=intent,
        status=status,
        assistant_message=assistant_message,
        decision_summary=decisions,
        evidence_chain=evidence_chain,
        contradictions=contradictions,
        limitations=limitations,
        next_actions=next_actions,
        tool_calls=tool_calls,
        deployment_assessment=deployment_assessment,
        side_effect_delta=side_effect_delta,
    )
    persisted_message = repo.add_assistant_message(
        case_id=case_id,
        tenant_id=tenant_id,
        content=assistant_message,
        trigger_turn_id=deterministic_turn_id,
        origin_turn_id=deterministic_turn_id,
        evidence_refs=[
            str(item.get("evidence_id"))
            for item in evidence_chain
            if item.get("evidence_id")
        ],
        limitation_refs=limitations,
    )
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="agent_turn_completed",
        payload={
            **result.model_dump(mode="json"),
            "message_id": persisted_message["message_id"],
        },
        actor_id="mini-drop-agent-runtime",
    )
    return APIResponse(data=result.model_dump(mode="json"))


def _runtime_accepted_response(
    case_id: str,
    tenant_id: str,
    accepted,
    intent: AgentTurnIntent,
) -> APIResponse:
    result = AgentTurnResult(
        turn_id=accepted.turn_id,
        intent=intent,
        status="runtime_turn_accepted",
        assistant_message=(
            "本轮已提交给 Agent Runtime 处理；具体回答与工具轨迹通过 Runtime Event 回传并持久化。"
            if accepted.mode != "pi_shadow"
            else "本轮已进入 Shadow 模式：Runtime 可提出计划但不会创建任何 Task。"
        ),
        decision_summary=[
            f"runtime_mode={accepted.mode}",
            f"runtime_turn_id={accepted.turn_id}",
        ],
        evidence_chain=[],
        next_actions=[{
            "type": "runtime_turn",
            "turn_id": accepted.turn_id,
            "mode": accepted.mode,
        }],
    )
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="agent_runtime_turn_submitted",
        payload=result.model_dump(mode="json"),
        actor_id="mini-drop-agent-runtime",
    )
    return APIResponse(data=result.model_dump(mode="json"))


def _deployment_next_actions(assessment: dict[str, Any], tool_calls: list[Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for call in tool_calls:
        if call.status == "approval_required":
            actions.append({
                "type": "approve_source",
                "source_id": call.source_id,
                "operation": call.operation,
                "reason": call.reason,
            })
    if assessment.get("missing_inputs"):
        actions.append({
            "type": "provide_capacity_data",
            "fields": assessment["missing_inputs"],
        })
    if assessment.get("verdict") == "conditional":
        actions.append({"type": "adjust_capacity_or_requirements", "description": "扩容、降低单副本需求或调整调度约束后重新评估"})
    return actions



__all__ = ["_cancel_case_tasks", "router", "start_case_diagnosis"]
