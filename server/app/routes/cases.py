"""Incident case, target, campaign, and workspace HTTP endpoints."""

from __future__ import annotations

import asyncio
import hmac
import io
import json as _json
import os
import secrets
import zipfile
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import Response, StreamingResponse

from mini_drop_contracts import get_collector_spec
from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.dispatcher import get_runtime
from server.app.agent_runtime.port import AgentTurnInput, CaseContextSnapshot
from server.app.artifact_service import read_artifact_bytes
from server.app.application.task_views import task_view as _task_view
from server.app.case_collaboration import (
    AttachResourcesRequest,
    CaseState,
    CreateCaseRequest,
    CreateChangeRequest,
    CreateTargetSessionRequest,
    CreateTargetSignalRequest,
    ExcludeAttachmentRequest,
    EvaluationEvidenceImportRequest,
    IndexProfileTaskRequest,
    ReferenceSearchRequest,
    TargetSessionTransitionRequest,
    serialize_time_range,
)
from server.app.diagnosis.campaign import (
    CampaignCreateInput,
    build_campaign_plan,
    campaign_matrix,
)
from server.app.diagnosis.query_registry import QUERY_REGISTRY
from server.app.diagnosis.case_evidence import stable_projection_hash
from server.app.diagnosis.network_discovery import (
    aggregate_dependency_graph,
    case_dependency_graph_snapshot,
)
from server.app.diagnosis.case_control import (
    focus_from_case,
    notify_runtime_abort,
    notify_runtime_steer,
)
from server.app.diagnosis.reference_resolver import ResourceRef
from server.app.event_bus import BUS
from server.app.http.auth import (
    request_principal as _request_principal,
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.legacy_compat import legacy_diagnosis_enabled
from server.app.runtime_services import (
    case_evidence_service,
    collection_supervisor,
    diagnosis_orchestrator,
    evidence_analysis_service,
    evidence_attachment_service,
    investigation_plan_service,
    investigation_state_service,
    reference_resolver,
    repo,
)
from server.app.schemas import APIResponse
from server.app.v6_routes import (
    QueryError,
    _create_case_query_task,
    _filter_branch_collection_items,
    _evidence_visible_to_branch,
    _filter_tree_for_branch,
)


router = APIRouter()


class CaseFocusRequest(BaseModel):
    focus_kind: str
    focus_ref: str = Field(min_length=1, max_length=512)
    reason: str = Field(default="operator focus change", max_length=1000)
    expected_scope_revision: int | None = None
    expected_control_revision: int | None = None


def _focus_target_allowed(case: dict[str, Any], graph: dict[str, Any], kind: str, ref: str) -> bool:
    scope = case.get("target_scope") or {}
    nodes = graph.get("graph", {}).get("nodes") or []
    edges = graph.get("graph", {}).get("edges") or []
    if kind == "SERVICE":
        if ref in {str(scope.get(key) or "") for key in ("service_id", "service", "service_name")}:
            return True
        return any(str(node.get("entity_id")) == ref and node.get("entity_type") in {"service", "instance"} for node in nodes)
    if kind == "PROCESS":
        return any(
            str(node.get("entity_id")) == ref
            and node.get("entity_type") == "process"
            for node in nodes
        ) or any(
            str(ref).isdigit() and str(node.get("entity_id", "")).split(":")[-1] == str(ref)
            and node.get("entity_type") == "process"
            for node in nodes
        )
    if kind == "DEPENDENCY_EDGE":
        return any(str(edge.get("edge_id")) == ref for edge in edges)
    return False

# ── AI Incident Case 协作层（v1）───────────────────────────────


@router.post("/api/v1/target-sessions")
def create_target_session(
    payload: CreateTargetSessionRequest, request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    try:
        result = repo.create_target_session({
            **payload.model_dump(mode="json"),
            "tenant_id": _request_tenant(),
            "created_by": _request_principal(request),
        })
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=result)


@router.get("/api/v1/target-sessions")
def list_target_sessions(
    request: Request, status: str = "", limit: int = 100,
) -> APIResponse:
    _require_role(request, "operator")
    if status and status not in {"ACTIVE", "PAUSED", "ARCHIVED"}:
        raise HTTPException(status_code=400, detail="未知目标会话状态")
    items = repo.list_target_sessions(
        _request_tenant(), status=status, limit=limit,
    )
    return APIResponse(data={"items": items})


@router.get("/api/v1/target-sessions/{target_session_id}")
def get_target_session(target_session_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    result = repo.get_target_session(target_session_id, _request_tenant())
    if result is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    return APIResponse(data=result)


@router.post("/api/v1/target-sessions/{target_session_id}/transition")
def transition_target_session(
    target_session_id: str,
    payload: TargetSessionTransitionRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    to_status = {
        "pause": "PAUSED", "resume": "ACTIVE", "archive": "ARCHIVED",
    }[payload.action]
    try:
        result = repo.transition_target_session(
            target_session_id,
            _request_tenant(),
            to_status=to_status,
            reason=payload.reason,
            actor_id=_request_principal(request),
            expected_row_version=payload.expected_row_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    return APIResponse(data=result)


@router.post("/api/v1/target-sessions/{target_session_id}/signals")
def create_target_signal(
    target_session_id: str,
    payload: CreateTargetSignalRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    signal, created = repo.record_target_signal(
        target_session_id,
        _request_tenant(),
        payload.model_dump(mode="python"),
    )
    if signal is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    triggered_case = None
    if created:
        triggered_case = repo.create_case_for_target_signal(
            target_session_id,
            signal["signal_id"],
            _request_tenant(),
            created_by=_request_principal(request),
        )
        refreshed = repo.list_target_signals(
            target_session_id, _request_tenant(), limit=500,
        ) or []
        signal = next(
            (item for item in refreshed if item["signal_id"] == signal["signal_id"]),
            signal,
        )
    return APIResponse(data={
        "signal": signal,
        "created": created,
        "triggered_case": triggered_case,
    })


@router.get("/api/v1/target-sessions/{target_session_id}/signals")
def list_target_signals(
    target_session_id: str, request: Request, limit: int = 100,
) -> APIResponse:
    _require_role(request, "operator")
    items = repo.list_target_signals(
        target_session_id, _request_tenant(), limit=limit,
    )
    if items is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    return APIResponse(data={"items": items})


@router.post("/api/v1/target-sessions/{target_session_id}/profile-windows/index-task")
def index_target_profile_task(
    target_session_id: str,
    payload: IndexProfileTaskRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    try:
        items = repo.index_profile_task(
            target_session_id, _request_tenant(), payload.task_id,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail == "PROFILE_TASK_NOT_FOUND" else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc
    if items is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    return APIResponse(data={"items": items, "indexed_count": len(items)})


@router.get("/api/v1/target-sessions/{target_session_id}/profile-windows")
def list_target_profile_windows(
    target_session_id: str,
    request: Request,
    start: datetime,
    end: datetime,
    include_expired: bool = False,
    limit: int = 200,
) -> APIResponse:
    _require_role(request, "operator")
    if end.timestamp() <= start.timestamp():
        raise HTTPException(status_code=400, detail="PROFILE_WINDOW_RANGE_INVALID")
    if end.timestamp() - start.timestamp() > 7 * 86400:
        raise HTTPException(status_code=400, detail="PROFILE_WINDOW_RANGE_TOO_LARGE")
    items = repo.list_profile_windows(
        target_session_id,
        _request_tenant(),
        start=start,
        end=end,
        include_expired=include_expired,
        limit=limit,
    )
    if items is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    return APIResponse(data={"items": items})


@router.post("/api/v1/cases")
def create_incident_case(payload: CreateCaseRequest, request: Request) -> APIResponse:
    _require_role(request, "operator")
    target = None
    if payload.target_session_id:
        target = repo.get_target_session(payload.target_session_id, _request_tenant())
        if target is None:
            raise HTTPException(status_code=404, detail="TARGET_SESSION_NOT_FOUND")
        if target["status"] == "ARCHIVED":
            raise HTTPException(status_code=409, detail="TARGET_SESSION_ARCHIVED")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    trusted = {
        **payload.model_dump(mode="json", exclude={"time_range"}),
        "time_range": serialize_time_range(payload.time_range),
        "tenant_id": tenant_id,
        "created_by": principal_id,
    }
    if target is not None:
        trusted["environment"] = target["environment"]
        trusted["target_scope"] = target["target_scope"]
    # E1：target_scope.evidence_task_ids 不再持久化依赖，改由附件统一消费
    legacy_task_ids = list(dict.fromkeys(
        (trusted.get("target_scope") or {}).get("evidence_task_ids") or []
    ))
    if legacy_task_ids and isinstance(trusted.get("target_scope"), dict):
        trusted["target_scope"].pop("evidence_task_ids", None)
    try:
        result = repo.create_incident_case(trusted)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.endswith("_NOT_FOUND") else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc
    # 统一数据入口：initial_tasks 与遗留 evidence_task_ids 一并投影为 Attachment
    initial_tasks = list(dict.fromkeys((result.get("initial_task_ids") or []) + legacy_task_ids))
    if initial_tasks:
        evidence_attachment_service.attach_resources(
            result,
            tenant_id,
            [ResourceRef(type="task", id=str(task_id)) for task_id in initial_tasks],
            actor_id=principal_id,
            purpose="创建 Case 时的初始任务证据",
            source="from_task",
        )
    return APIResponse(data=result)


@router.post("/api/v1/changes")
def create_change_record(payload: CreateChangeRequest, request: Request) -> APIResponse:
    """登记一次发布/配置/开关变更（供 AI 做变更前后对比与回归关联）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    data = payload.model_dump(mode="json", exclude={"changed_at"})
    data["changed_at"] = payload.changed_at
    result = repo.create_change_record({
        **data,
        "tenant_id": tenant_id,
        "created_by": principal_id,
    })
    return APIResponse(data=result)


@router.get("/api/v1/changes")
def list_change_records(
    request: Request,
    service_id: str | None = None,
    environment: str | None = None,
) -> APIResponse:
    """列出登记过的服务变更（按 service/environment 过滤）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    items = repo.list_change_records(
        tenant_id=tenant_id,
        service_id=service_id,
        environment=environment,
    )
    return APIResponse(data={"items": items})


@router.get("/api/v1/cases")
def list_incident_cases(
    request: Request,
    state: str = "",
    limit: int = 100,
    offset: int = 0,
) -> APIResponse:
    _require_role(request, "operator")
    if state and state not in {item.value for item in CaseState}:
        raise HTTPException(status_code=400, detail="未知 Case 状态")
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    tenant_id = _request_tenant()
    items = repo.list_incident_cases(
        tenant_id,
        state=state,
        limit=limit,
        offset=offset,
    )
    return APIResponse(data={
        "items": items,
        "total": repo.count_incident_cases(tenant_id, state=state),
        "limit": limit,
        "offset": offset,
    })


@router.get("/api/v1/cases/{case_id}")
def get_incident_case(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    result = repo.get_incident_case(case_id, _request_tenant())
    if result is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    result = dict(result)
    result["agent_progress"] = _case_agent_progress(result)
    return APIResponse(data=result)


def _case_agent_progress(case: dict[str, Any]) -> dict[str, Any]:
    """P6：Agent 当前阶段、预计等待与恢复进度（供持续会话首页展示）。"""
    loop = ((case.get("recovery") or {}).get("agent_loop") or {})
    phase = str(loop.get("phase") or "OBSERVING")
    stable = int(loop.get("stable_verifications") or 0)
    policy = ((case.get("target_scope") or {}).get("autonomy_policy") or {})
    required = int(policy.get("stable_verification_count") or 2) if isinstance(policy, dict) else 2
    actions = int(loop.get("actions_executed") or 0)
    max_actions = int(policy.get("max_actions") or 3) if isinstance(policy, dict) else 3
    diagnosis_id = (
        case.get("diagnosis_session_id") if legacy_diagnosis_enabled() else None
    )
    diagnosis_status = None
    if diagnosis_id:
        session = diagnosis_orchestrator.store.get_session(diagnosis_id)
        if session is not None:
            diagnosis_status = session.get("status")
    phase_labels = {
        "OBSERVING": "待启动诊断",
        "STARTING_DIAGNOSIS": "启动诊断",
        "DIAGNOSING": "调查中",
        "ACTION_DISPATCHING": "执行恢复动作",
        "ACTION_EXECUTED": "已执行，验证中",
        "VERIFYING": "验证恢复",
        "MONITORING": "稳定观察",
        "ROLLBACK_DISPATCHING": "回滚中",
        "ROLLED_BACK": "已回滚，重新调查",
        "RESOLVED": "已解决",
        "ESCALATED": "已升级人工",
    }
    return {
        "phase": phase,
        "phase_label": phase_labels.get(phase, phase),
        "diagnosis_status": diagnosis_status,
        "actions_executed": actions,
        "max_actions": max_actions,
        "stable_verifications": stable,
        "required_stable_verifications": required,
        "verification_progress": round(stable / max(required, 1), 2),
    }


@router.get("/api/v1/cases/{case_id}/events/stream")
async def stream_incident_case_events(
    case_id: str,
    request: Request,
    after_seq: int = 0,
    branch_id: str = "",
) -> StreamingResponse:
    """v6 SSE: replay DB events after Last-Event-ID, then subscribe without a gap."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    last_event_id = request.headers.get("Last-Event-ID", "0")
    try:
        cursor = max(int(last_event_id), max(after_seq, 0))
    except ValueError:
        cursor = max(after_seq, 0)
    # Subscribe before reading the replay window.  An event committed while the
    # replay query runs is therefore present either in the replay, the queue, or
    # both; the monotonic cursor below removes the possible duplicate.
    subscription = BUS.subscribe()
    replay = repo.list_case_events(case_id, tenant_id, limit=1000, after_seq=cursor) or []
    branch_id = str(branch_id or "").strip()

    def branch_event_visible(item: dict[str, Any]) -> bool:
        if not branch_id:
            return True
        return str((item.get("payload") or {}).get("branch_id") or "") == branch_id

    async def event_stream():
        high_water = cursor
        try:
            for item in replay:
                if not branch_event_visible(item):
                    continue
                seq = int(item.get("case_event_seq") or 0)
                if seq <= high_water:
                    continue
                high_water = seq
                yield f"id: {seq}\nevent: case_event\ndata: {_json.dumps(item, ensure_ascii=False, default=str)}\n\n"
            while True:
                try:
                    bus_event = await asyncio.wait_for(
                        asyncio.to_thread(subscription.get), timeout=20,
                    )
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if bus_event.get("event") != "case_event":
                    continue
                data = bus_event.get("data") or {}
                if str(data.get("case_id") or "") != case_id:
                    continue
                if not branch_event_visible(data):
                    continue
                seq = int(data.get("case_event_seq") or 0)
                if seq <= high_water:
                    continue
                high_water = seq
                yield f"id: {seq}\nevent: case_event\ndata: {_json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        finally:
            BUS.unsubscribe(subscription)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/cases/{case_id}/events")
def list_incident_case_events(
    case_id: str,
    request: Request,
    limit: int = 200,
    after_id: int = 0,
    after_seq: int = 0,
    before_seq: int | None = None,
    latest: bool = False,
    branch_id: str = "",
) -> APIResponse:
    _require_role(request, "operator")
    if latest:
        high_water = repo.get_case_event_high_water(case_id, _request_tenant())
        if high_water is None:
            raise HTTPException(status_code=404, detail="Case 不存在")
        return APIResponse(data={"items": [], "total": 0, "last_event_seq": high_water})
    items = repo.list_case_events(
        case_id,
        _request_tenant(),
        limit=min(max(limit, 1), 1000),
        after_id=max(after_id, 0),
        after_seq=max(after_seq, 0),
    )
    if before_seq is not None:
        items = [item for item in items or [] if int(item.get("case_event_seq") or 0) <= before_seq]
    branch_id = str(branch_id or "").strip()
    if branch_id:
        items = [
            item for item in items or []
            if str((item.get("payload") or {}).get("branch_id") or "") == branch_id
        ]
    if items is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/api/v1/references/search")
def search_references(
    payload: ReferenceSearchRequest,
    request: Request,
) -> APIResponse:
    """`@` 自动补全：返回稳定 ResourceRef 候选，不返回模型猜测。"""
    _require_role(request, "operator")
    candidates = reference_resolver.search(
        payload.query,
        _request_tenant(),
        ref_type=payload.type,
        limit=payload.limit,
    )
    return APIResponse(data={
        "items": [item.model_dump(mode="json") for item in candidates],
        "total": len(candidates),
    })


@router.get("/api/v1/query-operations")
def list_query_operations(request: Request) -> APIResponse:
    """G4：注册的低风险只读 Query 目录。"""
    _require_role(request, "operator")
    return APIResponse(data={"items": QUERY_REGISTRY.list_operations(), "total": len(QUERY_REGISTRY.list_operations())})


@router.post("/api/v1/cases/{case_id}/queries")
def create_case_query(
    case_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """G4：把注册 Query 编译为原生 Task，经 Worker/Collector 执行。

    不接受 executable/cwd/env/argv；越界参数在创建 Task 前拒绝。
    """
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    if case["state"] in {"STOPPED", "RESOLVED"}:
        raise HTTPException(status_code=409, detail="CASE_TERMINAL")
    try:
        task, operation_id = _create_case_query_task(
            case, tenant_id, principal_id,
            str(payload.get("operation") or ""),
            payload.get("parameters") or {},
            idempotency_key=str(payload.get("idempotency_key") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return APIResponse(data={"task": _task_view(task), "operation": operation_id})





@router.post("/api/v1/cases/{case_id}/attachments")
def attach_case_resources(
    case_id: str,
    payload: AttachResourcesRequest,
    request: Request,
) -> APIResponse:
    """统一数据入口：将结构化 ResourceRef 绑定到 Case（E1）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    refs = [
        ResourceRef(**item.model_dump(mode="json"))
        for item in payload.references
    ]
    results = evidence_attachment_service.attach_resources(
        case,
        tenant_id,
        refs,
        actor_id=principal_id,
        purpose=payload.purpose,
        source="user_mention",
    )
    repo.record_case_event(
        case_id, tenant_id, event_type="resource_attached",
        payload={"results": results, "actor_id": principal_id}, actor_id=principal_id,
    )
    return APIResponse(data={"items": results})


@router.post("/api/v1/cases/{case_id}/evidence/import")
def import_evaluation_evidence(
    case_id: str,
    payload: EvaluationEvidenceImportRequest,
    request: Request,
) -> APIResponse:
    """Import one bounded projection for a local GitHub replay evaluation.

    This route is intentionally fail-closed.  It is disabled unless the
    operator explicitly enables ``MINI_DROP_EVAL_IMPORT_ENABLED`` and sets a
    separate short-lived ``MINI_DROP_EVAL_IMPORT_TOKEN``.  The raw pack is
    never accepted, and later Agent turns only reference the canonical ID.
    """
    _require_role(request, "operator")
    enabled = os.getenv("MINI_DROP_EVAL_IMPORT_ENABLED", "0").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="EVALUATION_IMPORT_DISABLED")
    expected_token = os.getenv("MINI_DROP_EVAL_IMPORT_TOKEN", "").strip()
    supplied_token = request.headers.get("X-Evaluation-Import-Token", "").strip()
    if not expected_token or not supplied_token or not hmac.compare_digest(
        supplied_token, expected_token,
    ):
        raise HTTPException(status_code=401, detail="EVALUATION_IMPORT_TOKEN_REQUIRED")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    required_prefix = f"eval:{case_id}:"
    if not payload.evidence_id.startswith(required_prefix):
        raise HTTPException(status_code=422, detail="EVALUATION_EVIDENCE_ID_SCOPE_INVALID")
    computed_hash = stable_projection_hash(payload.projection)
    if payload.projection_hash and payload.projection_hash != computed_hash:
        raise HTTPException(status_code=422, detail="EVALUATION_PROJECTION_HASH_MISMATCH")
    try:
        result = case_evidence_service.materialize_evaluation_projection(
            case_id,
            tenant_id,
            evidence_id=payload.evidence_id,
            pack_kind=payload.pack_kind,
            source_id=payload.source_id,
            source_ref=payload.source_ref,
            projection=payload.projection,
            content_hash=payload.content_hash,
            source_bytes=payload.source_bytes,
            synthetic=payload.synthetic,
            observed_at=payload.observed_at,
            actor_id=_request_principal(request),
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(status_code=409 if detail.endswith("CONFLICT") else 400, detail=detail) from exc
    return APIResponse(data=result)


@router.post("/api/v1/cases/{case_id}/campaigns")
def create_case_campaign(
    case_id: str,
    payload: CampaignCreateInput,
    request: Request,
) -> APIResponse:
    """G4：人工或 AI 使用同一 Campaign API，编译为单目标 Task 计划矩阵。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan_input = build_campaign_plan(payload)
    try:
        plan = investigation_plan_service.update_plan(
            case_id,
            tenant_id,
            plan_input,
            actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="case_campaign_created",
        payload={
            "plan_revision": plan.get("plan_revision"),
            "source": "campaign",
            "baseline": payload.common_baseline.collector_id,
            "assignments": [item.collector_id for item in payload.assignments],
        },
        actor_id=principal_id,
    )
    return APIResponse(data={"plan": plan, "matrix": campaign_matrix(plan)})


@router.post("/api/v1/cases/{case_id}/campaigns/preview")
def preview_case_campaign(
    case_id: str,
    payload: CampaignCreateInput,
    request: Request,
) -> APIResponse:
    """v6: manual/AI Campaign preview uses the same compiler as create."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = build_campaign_plan(payload)
    return APIResponse(data={
        "plan": plan,
        "matrix": campaign_matrix(plan),
        "resolved_assignments": [
            {
                "role": item.role,
                "collector_id": item.collector_id,
                "target_refs": item.target_refs or [],
                "risk": item.risk,
                "priority": item.priority,
            }
            for item in payload.assignments
        ],
    })


@router.get("/api/v1/cases/{case_id}/campaigns/current")
def get_case_campaign(
    case_id: str,
    request: Request,
) -> APIResponse:
    """读取最近一次 Campaign 编译结果（真实后端状态投影）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = investigation_plan_service.read_plan(case_id, tenant_id)
    return APIResponse(data={"matrix": campaign_matrix(plan)})


@router.get("/api/v1/cases/{case_id}/evidence")
def list_case_evidence(
    case_id: str,
    request: Request,
) -> APIResponse:
    """G3：读取 canonical Case Evidence Store（Evidence Explorer 后端）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    items = repo.list_case_evidence(case_id, tenant_id)
    for item in items:
        projections = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=item.get("evidence_id"),
        ) if hasattr(repo, "list_evidence_projections") else []
        item["projections"] = [
            {"projection_id": p.get("projection_id"), "projection_kind": p.get("projection_kind"),
             "projection_hash": p.get("projection_hash"), "summary": (p.get("content") or {}).get("summary"),
             "signals": (p.get("content") or {}).get("signals") or {},
             "truncated": p.get("truncated")}
            for p in projections
        ]
    return APIResponse(data={"items": items, "total": len(items)})


def _case_evidence_detail(case_id: str, tenant_id: str, evidence_id: str) -> dict[str, Any]:
    evidence = repo.get_case_evidence(case_id, tenant_id, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence 不存在")
    projections = repo.list_evidence_projections(case_id, tenant_id, evidence_id=evidence_id)
    reviews = investigation_plan_service.list_reviews(case_id, tenant_id, evidence_id=evidence_id)
    analyses = evidence_analysis_service.list_runs(case_id, tenant_id, evidence_id=evidence_id)
    spec = get_collector_spec(str(evidence.get("collector_id") or ""))
    return {
        **evidence,
        "collector_spec": spec.to_dict() if spec else None,
        "projections": projections,
        "reviews": reviews,
        "analyses": analyses,
    }


@router.get("/api/v1/cases/{case_id}/evidence/{evidence_id}")
def get_case_evidence_detail(case_id: str, evidence_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    return APIResponse(data=_case_evidence_detail(case_id, _request_tenant(), evidence_id))


@router.get("/api/v1/cases/{case_id}/evidence/{evidence_id}/preview")
def preview_case_evidence(
    case_id: str, evidence_id: str, request: Request, max_bytes: int = 131072,
) -> APIResponse:
    _require_role(request, "operator")
    detail = _case_evidence_detail(case_id, _request_tenant(), evidence_id)
    max_bytes = max(1, min(int(max_bytes), 262144))
    projections = detail.get("projections") or []
    if not projections:
        raise HTTPException(status_code=409, detail="EVIDENCE_PROJECTION_UNAVAILABLE")
    projection = projections[-1]
    encoded = _json.dumps(
        projection.get("content") or {}, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")
    truncated = len(encoded) > max_bytes
    preview = encoded[:max_bytes].decode("utf-8", errors="replace") if truncated else None
    spec = detail.get("collector_spec") or {}
    return APIResponse(data={
        "evidence_id": evidence_id,
        "review_state": detail.get("status"),
        "projection_hash": projection.get("projection_hash"),
        "projection_kind": projection.get("projection_kind"),
        "preview_modes": spec.get("preview_modes") or ["json"],
        "content": None if truncated else projection.get("content"),
        "text_preview": preview,
        "truncated": truncated or bool(projection.get("truncated")),
        "source_bytes": projection.get("source_bytes"),
        "projected_bytes": projection.get("projected_bytes"),
    })


def _raw_evidence_bytes(detail: dict[str, Any]) -> tuple[bytes, str, str]:
    task_id = str(detail.get("task_id") or "")
    artifact_id = str(detail.get("artifact_id") or "")
    artifacts = list(repo.artifacts.get(task_id, [])) if task_id else []
    artifact = next((item for item in artifacts if str(item.get("id") or "") == artifact_id), None)
    if artifact is None:
        artifact = next((item for item in artifacts if item.get("artifact_type") == detail.get("artifact_type")), None)
    if artifact is not None:
        has_external_raw = bool(artifact.get("local_path") or artifact.get("object_key"))
        try:
            raw = read_artifact_bytes(artifact)
        except FileNotFoundError as exc:
            if has_external_raw:
                raise HTTPException(status_code=409, detail="EVIDENCE_RAW_OBJECT_MISSING") from exc
            raw = _json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return (
            raw,
            str(artifact.get("content_type") or "application/octet-stream"),
            str(artifact.get("filename") or f"{detail['evidence_id']}.json"),
        )
    # Source Evidence has no natural file; its canonical projection is the raw JSON contract.
    if str(detail.get("source_channel") or "") != "COLLECTOR" and detail.get("projections"):
        raw = _json.dumps(
            detail["projections"][-1].get("content") or {},
            ensure_ascii=False, sort_keys=True, default=str,
        ).encode("utf-8")
        return raw, "application/json", f"{detail['evidence_id']}.json"
    raise HTTPException(status_code=409, detail="EVIDENCE_RAW_OBJECT_UNAVAILABLE")


@router.get("/api/v1/cases/{case_id}/evidence/{evidence_id}/download")
def download_case_evidence(
    case_id: str, evidence_id: str, request: Request, format: str = "raw",
) -> Response:
    _require_role(request, "operator")
    if format not in {"raw", "bundle"}:
        raise HTTPException(status_code=400, detail="INVALID_EVIDENCE_DOWNLOAD_FORMAT")
    detail = _case_evidence_detail(case_id, _request_tenant(), evidence_id)
    raw, media_type, filename = _raw_evidence_bytes(detail)
    if format == "raw":
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        }
        return Response(content=raw, media_type=media_type, headers=headers)
    manifest = {
        "schema_version": "evidence-bundle.v1",
        "evidence": {key: value for key, value in detail.items() if key not in {"projections", "reviews", "analyses", "collector_spec"}},
        "collector_spec": detail.get("collector_spec"),
        "raw_filename": filename,
        "registered_sha256": detail.get("sha256"),
        "projection_hashes": [item.get("projection_hash") for item in detail.get("projections") or []],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
        archive.writestr(f"raw/{filename}", raw)
        archive.writestr("projections.json", _json.dumps(detail.get("projections") or [], ensure_ascii=False, indent=2, default=str))
        archive.writestr("reviews.json", _json.dumps(detail.get("reviews") or [], ensure_ascii=False, indent=2, default=str))
        archive.writestr("analyses.json", _json.dumps(detail.get("analyses") or [], ensure_ascii=False, indent=2, default=str))
    bundle_name = f"evidence-{evidence_id}-bundle.zip"
    return Response(
        content=output.getvalue(), media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(bundle_name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v1/cases/{case_id}/evidence/{evidence_id}/analyses")
def list_case_evidence_analyses(case_id: str, evidence_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_case_evidence(case_id, tenant_id, evidence_id) is None:
        raise HTTPException(status_code=404, detail="Evidence 不存在")
    items = evidence_analysis_service.list_runs(case_id, tenant_id, evidence_id=evidence_id)
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/api/v1/cases/{case_id}/evidence/{evidence_id}/analyses")
def create_case_evidence_analysis(
    case_id: str, evidence_id: str, payload: dict[str, Any], request: Request,
) -> APIResponse:
    """Queue a genuinely model-backed, read-only single-Evidence analysis."""
    _require_role(request, "operator")
    if runtime_mode() not in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        raise HTTPException(status_code=409, detail="AI_RUNTIME_NOT_CONFIGURED")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    try:
        run = evidence_analysis_service.create_run(
            case_id=case_id, tenant_id=tenant_id, evidence_ids=[evidence_id],
            mode="SINGLE", model_config_id=payload.get("model_config_id"),
            prompt_version=str(payload.get("prompt_version") or "evidence-analysis.v1"),
            explicit_single=True,
        )
        if run.get("reused"):
            return APIResponse(data=run)
        runtime = get_runtime()
        binding = runtime.start_or_resume(CaseContextSnapshot(
            case_id=case_id, tenant_id=tenant_id,
            case_goal=str(case.get("problem_description") or case.get("title") or ""),
            target_scope=case.get("target_scope") or {},
            case_command_revision=int(case.get("case_command_revision") or 1),
            control_revision=int(case.get("control_revision") or 1),
            scope_revision=int(case.get("scope_revision") or 1),
            evidence_watermark=len(repo.list_case_evidence(case_id, tenant_id)),
            evidence_summary=run.get("evidence_inputs") or [],
            side_effect_policy="READ_ONLY",
            investigation_directive={
                "kind": "EVIDENCE_ANALYSIS", "analysis_run_id": run["analysis_run_id"],
                "required_tool": "submit_evidence_analysis",
            },
        ))
        accepted = runtime.submit_turn(case_id, AgentTurnInput(
            case_id=case_id,
            message=(
                f"Analyze Evidence {evidence_id} for analysis run {run['analysis_run_id']}. "
                "Use only the pinned projection. Every fact requires an exact field/span citation. "
                "Submit the structured result with submit_evidence_analysis; do not create tasks."
            ),
            references=[{"type": "evidence", "id": evidence_id}],
            requested_mode="EVIDENCE_ANALYSIS",
            runtime_policy={"side_effect_policy": "READ_ONLY"},
        ))
        repo.record_agent_runtime_turn(
            turn_id=accepted.turn_id, case_id=case_id, tenant_id=tenant_id,
            runtime_session_id=binding.runtime_session_id,
            runtime_generation=binding.runtime_generation,
            user_message=f"Analyze Evidence {evidence_id}", requested_mode="EVIDENCE_ANALYSIS",
            status="ACCEPTED", accepted_mode=accepted.mode, detail=accepted.detail,
            idempotency_key=f"evidence-analysis:{run['analysis_run_id']}",
            disposition="ANSWER_ONLY", side_effect_policy="READ_ONLY",
            actor_id=_request_principal(request), client_command_id=None,
        )
        run = repo.attach_evidence_analysis_turn(
            run["analysis_run_id"], case_id, tenant_id, accepted.turn_id,
        ) or run
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={**run, "runtime_turn_id": accepted.turn_id, "status": "QUEUED"})


@router.get("/api/v1/cases/{case_id}/evidence/{evidence_id}/projections")
def get_case_evidence_projections(
    case_id: str,
    evidence_id: str,
    request: Request,
) -> APIResponse:
    """v6 canonical projection endpoint used by Pi and Evidence Explorer."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_case_evidence(case_id, tenant_id, evidence_id) is None:
        raise HTTPException(status_code=404, detail="Evidence 不存在")
    items = repo.list_evidence_projections(case_id, tenant_id, evidence_id=evidence_id)
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/workspace")
def get_case_workspace(case_id: str, request: Request, branch_id: str = "") -> APIResponse:
    """v6 9.2: one database snapshot for the Workbench first paint."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    branch_id = str(branch_id or "").strip() or None
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
    evidence_items = repo.list_case_evidence(case_id, tenant_id)
    if branch_id:
        evidence_items = [
            item for item in evidence_items
            if _evidence_visible_to_branch(item, branch_id)
        ]
    for item in evidence_items:
        item["projections"] = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=item.get("evidence_id"),
        ) if hasattr(repo, "list_evidence_projections") else []
    dependency_graph = aggregate_dependency_graph(
        evidence_items,
        [
            projection
            for item in evidence_items
            for projection in item.get("projections") or []
        ],
    )
    all_tree = repo.list_investigation_tree(case_id, tenant_id) if hasattr(repo, "list_investigation_tree") else {"nodes": [], "dependencies": []}
    collection_proposals = _filter_branch_collection_items(
        repo.list_collection_proposals(case_id, tenant_id), all_tree, branch_id,
    )
    visible_proposal_ids = {
        str(item.get("proposal_id") or "") for item in collection_proposals
    }
    collection_requests = _filter_branch_collection_items(
        repo.list_collection_requests(case_id, tenant_id), all_tree, branch_id,
        visible_proposal_ids=visible_proposal_ids if branch_id else None,
    )
    # Collection rows are linked to their durable Agent cycle; they do not
    # carry a second mutable branch column.  The helper above resolves the
    # cycle ownership from the investigation tree and fails closed.
    evidence_analyses = repo.list_evidence_analysis_runs(case_id, tenant_id)
    analysis_count_by_evidence: dict[str, int] = {}
    for analysis in evidence_analyses:
        referenced_ids = {
            str(item.get("evidence_id") or "")
            for item in analysis.get("evidence_inputs") or []
            if isinstance(item, dict) and item.get("evidence_id")
        }
        for evidence_id in referenced_ids:
            analysis_count_by_evidence[evidence_id] = analysis_count_by_evidence.get(evidence_id, 0) + 1
    for item in evidence_items:
        item["analysis_count"] = analysis_count_by_evidence.get(str(item.get("evidence_id") or ""), 0)
    investigation_state = investigation_state_service.snapshot(case_id, tenant_id, branch_id)
    investigation_tree = _filter_tree_for_branch(
        repo.list_investigation_tree(case_id, tenant_id), branch_id,
    ) if hasattr(repo, "list_investigation_tree") else {"nodes": [], "dependencies": []}
    branch_handles: dict[str, dict[str, Any]] = {}
    for node in all_tree.get("nodes") or []:
        node_branch = str(node.get("branch_id") or "").strip()
        if not node_branch:
            continue
        handle = branch_handles.setdefault(node_branch, {
            "branch_id": node_branch, "run_ids": [], "node_count": 0,
            "open_node_count": 0, "created_at": node.get("created_at"),
        })
        handle["node_count"] += 1
        handle["open_node_count"] += int(node.get("status") in {"OPEN", "WAITING_EVIDENCE", "PAUSED"})
        if node.get("run_id") and node["run_id"] not in handle["run_ids"]:
            handle["run_ids"].append(node["run_id"])
    for evidence in repo.list_case_evidence(case_id, tenant_id):
        lineage = evidence.get("lineage") or evidence.get("lineage_json") or {}
        node_branch = str(lineage.get("branch_id") or "").strip()
        if node_branch:
            handle = branch_handles.setdefault(node_branch, {"branch_id": node_branch, "run_ids": [], "node_count": 0, "open_node_count": 0})
            handle["evidence_count"] = int(handle.get("evidence_count") or 0) + 1
    branches = sorted(branch_handles.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)
    for index, handle in enumerate(branches, start=1):
        handle.setdefault("evidence_count", 0)
        handle["label"] = f"探索分支 {index}"
    conclusion_history = (
        repo.list_conclusion_revisions(case_id, tenant_id, branch_id=branch_id)
        if hasattr(repo, "list_conclusion_revisions") else ([investigation_state["conclusion"]] if investigation_state["conclusion"] else [])
    )
    execution_units = repo.list_execution_units(case_id, tenant_id) if hasattr(repo, "list_execution_units") else []
    fanout_runs = repo.list_fanout_runs(case_id, tenant_id)
    turns = repo.list_agent_runtime_turns(case_id, tenant_id)
    messages = repo.list_assistant_messages(case_id, tenant_id) if hasattr(repo, "list_assistant_messages") else []
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    active_turn = next((
        item for item in reversed(turns)
        if item.get("status") in {"ACCEPTED", "ROUTED", "RUNNING"}
    ), None)
    if branch_id:
        branch_cycle_ids = {
            str((node.get("metadata") or {}).get("cycle_id") or "")
            for node in investigation_tree.get("nodes") or []
            if (node.get("metadata") or {}).get("cycle_id")
        }
        messages = [
            item for item in messages
            if str(item.get("cycle_id") or "") in branch_cycle_ids
        ]
        # AgentRuntimeTurn has no branch column in the compatibility schema;
        # hiding the global turn list is fail-closed until a turn is attached
        # to a durable cycle.
        turns = []
        active_turn = None
    active_plan_steps = [item for item in (plan.get("steps") or []) if item.get("status") not in {
        "COMPLETED", "CANCELLED", "FAILED", "SUPERSEDED",
    }]
    request_by_proposal = {
        str(item.get("proposal_id") or ""): item for item in collection_requests
    }
    evidence_by_task: dict[str, list[str]] = {}
    for item in evidence_items:
        task_id = str(item.get("task_id") or "")
        evidence_id = str(item.get("evidence_id") or "")
        if task_id and evidence_id:
            evidence_by_task.setdefault(task_id, []).append(evidence_id)

    def proposal_goal(proposal: dict[str, Any]) -> dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or "")
        request_item = request_by_proposal.get(proposal_id) or {}
        task_id = str(request_item.get("task_id") or "")
        evidence_ids = evidence_by_task.get(task_id, [])
        proposal_status = str(proposal.get("status") or "PROPOSED")
        request_status = str(request_item.get("status") or "")
        awaiting = bool((proposal.get("validation_result") or {}).get("awaiting_execution_authority"))
        if proposal_status == "REJECTED" or request_status in {
            "FAILED", "DISPATCH_FAILED", "CANCELLED", "REJECTED",
        }:
            goal_status = "BLOCKED"
        elif awaiting and not request_item:
            goal_status = "WAITING_APPROVAL"
        elif evidence_ids:
            goal_status = "EVIDENCE_READY"
        elif task_id or request_status in {"ACCEPTED", "DISPATCHED", "RUNNING"}:
            goal_status = "COLLECTING"
        else:
            goal_status = "PROPOSED"
        return {
            "goal_id": proposal.get("plan_step_id") or f"proposal:{proposal_id}",
            "title": proposal.get("information_goal") or "未命名信息目标",
            "reason": proposal.get("reason_summary") or "Agent 提出的采集目标",
            "status": goal_status,
            "source": "proposal",
            "collector_id": proposal.get("collector_id"),
            "risk": proposal.get("expected_risk"),
            "proposal_id": proposal_id,
            "collection_request_id": request_item.get("collection_request_id"),
            "task_id": task_id or None,
            "evidence_ids": evidence_ids,
        }

    information_goals: list[dict[str, Any]] = []
    proposal_by_step = {
        str(item.get("plan_step_id") or ""): item
        for item in collection_proposals if item.get("plan_step_id")
    }
    consumed_proposals: set[str] = set()
    for index, step in enumerate(plan.get("steps") or []):
        step_id = str(step.get("step_id") or "")
        proposal = proposal_by_step.get(step_id)
        if proposal:
            goal = proposal_goal(proposal)
            consumed_proposals.add(str(proposal.get("proposal_id") or ""))
        else:
            step_status = str(step.get("status") or "PROPOSED")
            goal = {
                "goal_id": step_id or f"plan:{index}",
                "title": step.get("expected_information") or step.get("purpose") or f"信息目标 {index + 1}",
                "reason": step.get("purpose") or "调查计划中的信息目标",
                "status": {
                    "COMPLETED": "RESOLVED", "RUNNING": "COLLECTING",
                    "FAILED": "BLOCKED", "CANCELLED": "BLOCKED",
                }.get(step_status, "PROPOSED"),
                "source": "plan",
                "collector_id": step.get("collector_id") or step.get("kind"),
                "risk": step.get("risk"),
                "proposal_id": None,
                "collection_request_id": None,
                "task_id": (step.get("task_ids") or [None])[-1],
                "evidence_ids": evidence_by_task.get(str((step.get("task_ids") or [""])[-1]), []),
            }
        information_goals.append(goal)
    for proposal in collection_proposals:
        if str(proposal.get("proposal_id") or "") not in consumed_proposals:
            information_goals.append(proposal_goal(proposal))
    known_titles = {str(item.get("title") or "").strip().lower() for item in information_goals}
    for gap in investigation_state["evidence_gaps"]:
        title = str(gap.get("required_fact") or "").strip()
        if title.lower() in known_titles:
            continue
        information_goals.append({
            "goal_id": gap.get("gap_id"), "title": title or "未命名证据缺口",
            "reason": gap.get("what_it_does_not_support") or gap.get("blocked_claim") or "需要补证",
            "status": "RESOLVED" if gap.get("status") == "RESOLVED" else "BLOCKED" if gap.get("status") == "BLOCKING" else "PROPOSED",
            "source": "gap", "collector_id": None, "risk": None,
            "proposal_id": None, "collection_request_id": None, "task_id": None,
            "evidence_ids": list(gap.get("observed_evidence") or []),
        })
        known_titles.add(title.lower())
    for analysis in evidence_analyses:
        for index, item in enumerate(analysis.get("next_collection_proposals") or []):
            title = str(item.get("information_goal") or item.get("title") or "").strip()
            if not title or title.lower() in known_titles:
                continue
            information_goals.append({
                "goal_id": f"analysis:{analysis.get('analysis_run_id')}:{index}",
                "title": title, "reason": item.get("reason_summary") or "Evidence 分析提出的下一目标",
                "status": "PROPOSED", "source": "analysis",
                "collector_id": item.get("collector_id"), "risk": item.get("expected_risk"),
                "proposal_id": None, "collection_request_id": None, "task_id": None,
                "evidence_ids": [],
            })
            known_titles.add(title.lower())
    waiting_proposal = next((
        item for item in collection_proposals
        if item.get("status") == "PROPOSED"
        and (item.get("validation_result") or {}).get("awaiting_execution_authority")
    ), None)
    return APIResponse(data={
        "case_projection_version": (
            int(case.get("row_version") or 0) + len(messages) + len(evidence_items)
            + len(collection_proposals) + len(collection_requests) + len(evidence_analyses)
            + len(investigation_state["evidence_gaps"]) + len(execution_units) + len(fanout_runs)
        ),
        "revisions": {
            "case_command": case.get("case_command_revision") or 1,
            "control": case.get("control_revision") or 1,
            "scope": case.get("scope_revision") or 1,
            "plan": plan.get("plan_revision") or 0,
            "campaign": 0,
        },
        "case": case,
        "focus": focus_from_case(case),
        "branch_id": branch_id,
        "branches": branches,
        "engine": {
            "mode": runtime_mode().value,
            "availability": "READY" if binding else "UNAVAILABLE",
            "state": "RUNNING" if active_turn else "IDLE",
        },
        "active_turn": active_turn,
        "active_action": {
            "kind": "plan_step",
            "summary": "当前计划" if active_plan_steps else None,
            "status": active_plan_steps[0].get("status") if active_plan_steps else None,
        },
        "next_action": None,
        "user_action_required": ({
            "kind": "collection_approval",
            "proposal_id": waiting_proposal.get("proposal_id"),
            "summary": waiting_proposal.get("information_goal"),
        } if waiting_proposal else None),
        "plan": plan,
        "information_goals": information_goals,
        "campaign": {},
        "collection_proposals": collection_proposals,
        "collection_requests": collection_requests,
        "evidence": evidence_items,
        "evidence_analyses": evidence_analyses,
        "hypothesis_graph": investigation_state["hypothesis_graph"],
        "hypotheses": investigation_state["hypothesis_graph"].get("hypotheses") or [],
        "evidence_gaps": investigation_state["evidence_gaps"],
        "causal_graph": investigation_state["causal_graph"] or {},
        "dependency_graph": dependency_graph,
        "conclusion": investigation_state["conclusion"],
        "conclusion_history": conclusion_history,
        "recommendations": investigation_state["recommendations"],
        "execution_units": execution_units,
        "fanout_runs": fanout_runs,
        "investigation_tree": investigation_tree,
        "messages": messages,
        "runtime_turns": turns,
        "last_event_seq": max([int(item.get("case_event_seq") or 0) for item in repo.list_case_events(case_id, tenant_id, limit=200) or []] or [0]),
    })


@router.get("/api/v1/cases/{case_id}/branches")
def list_case_investigation_branches(case_id: str, request: Request) -> APIResponse:
    """List branch handles for the operator workspace without exposing content."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    tree = repo.list_investigation_tree(case_id, tenant_id) if hasattr(repo, "list_investigation_tree") else {"nodes": [], "dependencies": []}
    evidence = repo.list_case_evidence(case_id, tenant_id)
    branch_rows: dict[str, dict[str, Any]] = {}
    for node in tree.get("nodes") or []:
        branch = str(node.get("branch_id") or "").strip()
        if not branch:
            continue
        row = branch_rows.setdefault(branch, {
            "branch_id": branch,
            "run_ids": [],
            "node_count": 0,
            "open_node_count": 0,
            "last_status": node.get("status"),
            "created_at": node.get("created_at"),
        })
        row["node_count"] += 1
        row["open_node_count"] += int(node.get("status") in {"OPEN", "WAITING_EVIDENCE", "PAUSED"})
        if node.get("run_id") and node["run_id"] not in row["run_ids"]:
            row["run_ids"].append(node["run_id"])
        row["last_status"] = node.get("status") or row["last_status"]
        row["created_at"] = min(filter(None, [row.get("created_at"), node.get("created_at")]), default=row.get("created_at"))
    for item in evidence:
        lineage = item.get("lineage") or item.get("lineage_json") or {}
        branch = str(lineage.get("branch_id") or "").strip()
        if not branch:
            continue
        row = branch_rows.setdefault(branch, {"branch_id": branch, "run_ids": [], "node_count": 0, "open_node_count": 0})
        row["evidence_count"] = int(row.get("evidence_count") or 0) + 1
    items = sorted(branch_rows.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)
    for index, item in enumerate(items, start=1):
        item.setdefault("evidence_count", 0)
        item["label"] = f"探索分支 {index}"
    return APIResponse(data={"items": items, "total": len(items), "public_seed_available": True})


@router.post("/api/v1/cases/{case_id}/branches")
def create_case_investigation_branch(
    case_id: str, payload: dict[str, Any], request: Request,
) -> APIResponse:
    """Create an isolated branch root for an operator-led investigation turn."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    branch_id = f"branch_{secrets.token_hex(8)}"
    try:
        run = repo.create_investigation_run(
            case_id=case_id,
            tenant_id=tenant_id,
            created_from_turn_id=str(payload.get("created_from_turn_id") or "") or None,
            scope_revision=case.get("scope_revision"),
            control_revision=case.get("control_revision"),
            case_command_revision=case.get("case_command_revision"),
        )
        cycle = repo.create_agent_cycle(
            case_id=case_id,
            tenant_id=tenant_id,
            run_id=run["run_id"],
            trigger_type="USER_BRANCH_CREATED",
            trigger_ref=str(payload.get("reason") or "operator_created"),
            generation=int((repo.get_agent_runtime_binding(case_id, tenant_id) or {}).get("runtime_generation") or 1),
            branch_id=branch_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_case_event(
        case_id, tenant_id,
        event_type="investigation_branch_created",
        payload={"branch_id": branch_id, "run_id": run["run_id"], "cycle_id": cycle["cycle_id"]},
        actor_id=principal_id,
    )
    return APIResponse(data={
        "branch_id": branch_id,
        "run": run,
        "cycle": cycle,
        "label": str(payload.get("label") or "新探索分支")[:80],
    })


@router.get("/api/v1/cases/{case_id}/dependency-graph")
def get_case_dependency_graph(case_id: str, request: Request) -> APIResponse:
    """Return the active, evidence-backed communication graph for one Case."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=case_dependency_graph_snapshot(repo, case_id, tenant_id))


@router.post("/api/v1/cases/{case_id}/focus")
def set_case_focus(case_id: str, payload: CaseFocusRequest, request: Request) -> APIResponse:
    """Change the operator's service/process/edge focus behind Case CAS."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    kind = str(payload.focus_kind or "").upper()
    if kind not in {"SERVICE", "PROCESS", "DEPENDENCY_EDGE"}:
        raise HTTPException(status_code=422, detail="INVALID_FOCUS_KIND")
    for attr, expected in (
        ("scope_revision", payload.expected_scope_revision),
        ("control_revision", payload.expected_control_revision),
    ):
        if expected is not None and int(case.get(attr) or 0) != int(expected):
            raise HTTPException(status_code=409, detail=f"{attr.upper()}_STALE")
    graph = case_dependency_graph_snapshot(repo, case_id, tenant_id)
    if not _focus_target_allowed(case, graph, kind, payload.focus_ref):
        raise HTTPException(status_code=409, detail="FOCUS_TARGET_NOT_AUTHORIZED_BY_DISCOVERY")
    focus = {
        "focus_kind": kind,
        "focus_ref": payload.focus_ref,
        "reason": payload.reason,
        "focus_revision": int(case.get("scope_revision") or 1) + 1,
        "graph_digest": graph.get("graph_digest"),
    }
    scope = dict(case.get("target_scope") or {})
    scope["active_focus"] = focus
    try:
        updated = repo.correct_incident_case(
            case_id,
            tenant_id,
            actor_id=principal_id,
            changes={"target_scope": scope},
            reason=f"focus_changed:{kind}:{payload.focus_ref}:{payload.reason}",
            expected_row_version=case.get("row_version"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    focus["focus_revision"] = int(updated.get("scope_revision") or focus["focus_revision"])
    runtime_abort = notify_runtime_abort(case_id, payload.reason)
    runtime_steer = notify_runtime_steer(
        case_id,
        instruction=f"切换调查焦点到 {kind}:{payload.focus_ref}。原因：{payload.reason}",
        reason_code="FOCUS_CHANGED",
        scope_revision=int(updated.get("scope_revision") or 1),
        plan_revision=int((investigation_plan_service.read_plan(case_id, tenant_id) or {}).get("plan_revision") or 0),
        focus=focus,
    )
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="focus_changed",
        payload={"focus": focus, "runtime_abort": runtime_abort, "runtime_steer": runtime_steer},
        actor_id=principal_id,
    )
    return APIResponse(data={
        "focus": focus,
        "case": updated,
        "runtime": {"abort": runtime_abort, "steer": runtime_steer},
    })


@router.get("/api/v1/cases/{case_id}/investigation-summary")
def get_investigation_summary(case_id: str, request: Request, branch_id: str = "") -> APIResponse:
    """Compact operator view for conversational intervention and handoff."""
    _require_role(request, "operator")
    workspace_response = get_case_workspace(case_id, request, branch_id=branch_id)
    workspace = workspace_response.data
    active_tasks = [
        item for item in workspace.get("collection_requests") or []
        if str(item.get("status") or "").upper() in {"ACCEPTED", "DISPATCHED", "RUNNING"}
    ]
    active_evidence = [
        item for item in workspace.get("evidence") or []
        if str(item.get("lifecycle_status") or item.get("status") or "ACTIVE").upper()
        not in {"EXCLUDED", "SUPERSEDED", "INVALID"}
    ]
    graph = workspace.get("dependency_graph") or {}
    nodes = graph.get("graph", {}).get("nodes") or []
    edges = graph.get("graph", {}).get("edges") or []
    available_focus_targets = [
        {"focus_kind": "SERVICE" if node.get("entity_type") in {"service", "instance"} else "PROCESS", "focus_ref": node.get("entity_id"), "label": node.get("display_name") or node.get("entity_id")}
        for node in nodes if node.get("entity_type") in {"service", "instance", "process"}
    ] + [
        {"focus_kind": "DEPENDENCY_EDGE", "focus_ref": edge.get("edge_id"), "label": f"{edge.get('source_entity')} -> {edge.get('target_entity')}"}
        for edge in edges
    ]
    case = workspace.get("case") or {}
    return APIResponse(data={
        "focus": focus_from_case(case),
        "case_state": case.get("state"),
        "revisions": workspace.get("revisions") or {},
        "run": {"run_id": case.get("active_run_id") or case.get("investigation_run_id")},
        "cycle": {"active_turn": workspace.get("active_turn")},
        "active_tasks": active_tasks,
        "active_evidence": active_evidence,
        "evidence_watermark": len([p for item in active_evidence for p in item.get("projections") or []]),
        "open_gaps": [item for item in workspace.get("evidence_gaps") or [] if item.get("status") in {"OPEN", "BLOCKING"}],
        "current_conclusion": workspace.get("conclusion") or {},
        "next_actions": [workspace.get("next_action")] if workspace.get("next_action") else [],
        "available_focus_targets": available_focus_targets,
        "dependency_semantics": graph.get("graph_semantics") or "dependency_only_not_causal",
    })


@router.post("/api/v1/cases/{case_id}/collection-proposals/{proposal_id}/decision")
def decide_case_collection_proposal(
    case_id: str, proposal_id: str, payload: dict[str, Any], request: Request,
) -> APIResponse:
    """Approve or reject the exact persisted collection proposal."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    try:
        result = collection_supervisor.decide_pending_proposal(
            proposal_id=proposal_id, case_id=case_id, tenant_id=tenant_id,
            decision=str(payload.get("decision") or ""),
            decided_by=_request_principal(request),
            reason=str(payload.get("reason") or ""),
            expected_control_revision=payload.get("expected_control_revision"),
            expected_scope_revision=payload.get("expected_scope_revision"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_case_event(
        case_id, tenant_id, event_type="collection_proposal_decided",
        payload={
            "proposal_id": proposal_id,
            "decision": str(payload.get("decision") or "").upper(),
            "actor_id": _request_principal(request),
        },
        actor_id=_request_principal(request),
    )
    task = result.get("task")
    return APIResponse(data={
        "proposal": result.get("proposal"),
        "collection_request": result.get("collection_request"),
        "task": _task_view(task) if task is not None else None,
    })


@router.get("/api/v1/cases/{case_id}/causal-graphs")
def get_case_causal_graphs(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    branch_id = str(request.query_params.get("branch_id") or "").strip() or None
    graph = repo.get_causal_graph(case_id, tenant_id, branch_id=branch_id) if hasattr(repo, "get_causal_graph") else None
    return APIResponse(data={"graph": graph})


@router.get("/api/v1/cases/{case_id}/evidence-gaps")
def get_case_evidence_gaps(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    branch_id = str(request.query_params.get("branch_id") or "").strip() or None
    items = repo.list_evidence_gaps(case_id, tenant_id, branch_id=branch_id) if hasattr(repo, "list_evidence_gaps") else []
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/conclusions")
def get_case_conclusions(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    branch_id = str(request.query_params.get("branch_id") or "").strip() or None
    conclusion = repo.get_conclusion(case_id, tenant_id, branch_id=branch_id) if hasattr(repo, "get_conclusion") else None
    history = (
        repo.list_conclusion_revisions(case_id, tenant_id, branch_id=branch_id)
        if hasattr(repo, "list_conclusion_revisions") else ([conclusion] if conclusion else [])
    )
    return APIResponse(data={"conclusion": conclusion, "history": history, "total": len(history)})


@router.get("/api/v1/cases/{case_id}/recommendations")
def get_case_recommendations(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    items = repo.list_repair_recommendations(case_id, tenant_id) if hasattr(repo, "list_repair_recommendations") else []
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/acquisition-operations")
def list_acquisition_operations(request: Request) -> APIResponse:
    _require_role(request, "operator")
    items = repo.list_operation_specs() if hasattr(repo, "list_operation_specs") else []
    if not items:
        items = QUERY_REGISTRY.list_operations()
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/execution-units")
def list_case_execution_units(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    items = repo.list_execution_units(case_id, tenant_id) if hasattr(repo, "list_execution_units") else []
    return APIResponse(data={"items": items, "total": len(items)})


@router.get("/api/v1/cases/{case_id}/attachments")
def list_case_attachments(
    case_id: str,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    items = evidence_attachment_service.list_attachments(case_id, tenant_id)
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/api/v1/cases/{case_id}/attachments/{attachment_id}/exclude")
def exclude_case_attachment(
    case_id: str,
    attachment_id: str,
    payload: ExcludeAttachmentRequest,
    request: Request,
) -> APIResponse:
    """排除证据：不物理删除，后续 Prompt 不再包含。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    attachment = evidence_attachment_service.exclude(
        attachment_id, tenant_id, actor_id=principal_id, reason=payload.reason,
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment 不存在")
    repo.record_case_event(
        case_id, tenant_id, event_type="attachment_excluded",
        payload={"attachment_id": attachment_id, "reason": payload.reason, "actor_id": principal_id},
        actor_id=principal_id,
    )
    return APIResponse(data=attachment)



__all__ = ["_case_agent_progress", "router"]
