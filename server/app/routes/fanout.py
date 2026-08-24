"""Fanout, recovery, authorization, and system-control HTTP endpoints."""

from __future__ import annotations

from server.app.routes.recovery import (
    VERIFICATION_TASK_DURATION_SEC,
    _find_diagnosis_sys_metrics_task,
    _judge_recovery,
    _read_sys_metrics_artifact_keys,
)
from server.app.routes.plans_control import _cancel_case_tasks

import os
import secrets
import time
from pathlib import Path as _Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.dispatcher import get_runtime
from server.app.artifact_service import extract_artifact_json
from server.app.case_collaboration import CaseCorrectionRequest, CaseTransitionRequest
from server.app.common_utils import status_value
from server.app.diagnosis.action_registry import (
    ActionEvaluationRequest,
    DEFAULT_ACTION_REGISTRY,
    evaluate_action,
)
from server.app.diagnosis.authorization import (
    AuthorizationEvaluationRequest,
    CreateAuthorizationGrantRequest,
    evaluate_source_access,
)
from server.app.diagnosis.cluster_scope import EnvironmentProfile, MembershipSnapshot
from server.app.diagnosis.fanout import FanoutCollectionRun
from server.app.diagnosis.governance import (
    CAPABILITY_EPOCH,
    RED_BUTTON,
    issue_capability_key,
)
from server.app.diagnosis.reference_resolver import ResourceRef
from server.app.diagnosis.source_gateway import (
    SourceGatewayError,
    SourceQueryRequest,
)
from server.app.http.auth import (
    request_principal as _request_principal,
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.legacy_compat import legacy_diagnosis_enabled
from server.app.prometheus_metrics import record_source_access
from server.app.runtime_services import (
    diagnosis_orchestrator,
    evidence_attachment_service,
    fanout_service,
    investigation_plan_service,
    repo,
    source_gateway,
    target_resolver,
)
from server.app.schemas import APIResponse, CreateTaskRequest
from server.app.state_machine import Actor, now_utc


router = APIRouter()


def _extract_task_artifact_json(repository, task_id: str, artifact_type: str):
    return extract_artifact_json(repository.artifacts.get(task_id, []), artifact_type)

# ── E3.5 集群范围与采集扇出 ─────────────────────────────────────────


def _fanout_step(case_id: str, tenant_id: str, step_id: str) -> dict[str, Any]:
    plan = investigation_plan_service.read_plan(case_id, tenant_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Case 尚无调查计划")
    for step in plan.get("steps") or []:
        if step.get("step_id") == step_id:
            return step
    raise HTTPException(status_code=404, detail=f"PlanStep 不存在: {step_id}")


@router.post("/api/v1/cases/{case_id}/fanout")
def create_case_fanout(case_id: str, payload: dict[str, Any], request: Request) -> APIResponse:
    """E3.5：冻结成员快照，按选择策略展开一个逻辑 Step 为单目标 Task 扇出。

    请求体：
      step_id（必须）、strategy（默认 REPRESENTATIVE）、profile（EnvironmentProfile）、
      可选 target_refs / metric_scores / canary_labels / control_labels / max_targets。
    """
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    step_id = str(payload.get("step_id") or "")
    if not step_id:
        raise HTTPException(status_code=422, detail="缺少 step_id")
    step = _fanout_step(case_id, tenant_id, step_id)
    profile = EnvironmentProfile(**payload.get("profile") or {})
    # 集群 Step 的策略优先用 Step 声明，其次请求覆盖，默认 REPRESENTATIVE 分层采样。
    strategy = str(payload.get("strategy") or step.get("selection_strategy") or "REPRESENTATIVE")
    environment_id = str(payload.get("environment_id") or profile.environment_id)
    cluster_id = str(payload.get("cluster_id") or profile.cluster)
    snapshot = fanout_service.build_membership_snapshot(
        environment_id=environment_id,
        cluster_id=cluster_id,
        scope_revision=int(case.get("scope_revision") or 1),
    )
    repo.create_membership_snapshot(case_id, tenant_id, snapshot.model_dump(mode="json"))
    resolution = target_resolver.resolve_collection_targets(
        snapshot,
        strategy,
        profile=profile,
        target_refs=payload.get("target_refs") or step.get("target_refs") or None,
        metric_scores=payload.get("metric_scores"),
        canary_labels=set(payload.get("canary_labels") or []),
        control_labels=set(payload.get("control_labels") or []),
        change_cohort_version=str(payload.get("change_cohort_version") or ""),
        max_targets=int(payload.get("max_targets") or 0),
    )
    step_ctx = dict(step)
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
    step_ctx["plan_revision"] = int(plan.get("plan_revision") or 0)
    step_ctx["scope_revision"] = int(case.get("scope_revision") or 1)
    run = fanout_service.create_fanout_run(
        case_id=case_id,
        tenant_id=tenant_id,
        step=step_ctx,
        profile=profile,
        environment_id=environment_id,
        cluster_id=cluster_id,
        snapshot=snapshot,
        resolution=resolution,
    )
    repo.record_case_event(
        case_id, tenant_id, event_type="fanout_created",
        payload={
            "run_id": run["run_id"], "strategy": strategy,
            "targets": len(resolution.targets), "excluded": len(resolution.excluded),
        },
        actor_id=_request_principal(request),
    )
    return APIResponse(data={
        "run": run,
        "snapshot": snapshot.model_dump(mode="json"),
        "resolution": {
            "strategy": resolution.strategy,
            "targets": [t.member.agent_id for t in resolution.targets],
            "selection_notes": resolution.selection_notes,
            "rejected": resolution.rejected,
        },
    })


@router.get("/api/v1/cases/{case_id}/fanout")
def list_case_fanout_runs(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={"items": repo.list_fanout_runs(case_id, tenant_id)})


@router.get("/api/v1/cases/{case_id}/fanout/{run_id}")
def get_case_fanout_run(case_id: str, run_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    run = repo.get_fanout_run(case_id, tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="FanoutRun 不存在")
    return APIResponse(data=run)


@router.post("/api/v1/cases/{case_id}/fanout/{run_id}/cancel")
def cancel_case_fanout_run(case_id: str, run_id: str, request: Request) -> APIResponse:
    """取消传播：未完成 Task 全部转 CANCELLED，已 DONE 不动。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    run = repo.get_fanout_run(case_id, tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="FanoutRun 不存在")
    updated = fanout_service.cancel_run(FanoutCollectionRun(**run))
    repo.record_case_event(
        case_id, tenant_id, event_type="fanout_cancelled",
        payload={"run_id": run_id}, actor_id=_request_principal(request),
    )
    return APIResponse(data=updated)


@router.post("/api/v1/cases/{case_id}/fanout/{run_id}/resume")
def resume_case_fanout_run(case_id: str, run_id: str, request: Request) -> APIResponse:
    """恢复：已取消的未终态 Task 重新进入 PENDING，run 回到 RUNNING。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    run = repo.get_fanout_run(case_id, tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="FanoutRun 不存在")
    return APIResponse(data=fanout_service.resume_run(FanoutCollectionRun(**run)))


@router.post("/api/v1/cases/{case_id}/fanout/{run_id}/task-outcome")
def report_fanout_task_outcome(case_id: str, run_id: str, payload: dict[str, Any],
                               request: Request) -> APIResponse:
    """记录单个 Task 结果；scope_revision 不匹配的迟到结果被隔离。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    run = repo.get_fanout_run(case_id, tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="FanoutRun 不存在")
    task_id = str(payload.get("task_id") or "")
    status = str(payload.get("status") or "")
    scope_revision = int(payload.get("scope_revision") or 0)
    if task_id not in run.get("task_ids") or []:
        raise HTTPException(status_code=422, detail="task_id 不属于该 FanoutRun")
    return APIResponse(data=fanout_service.update_task_outcome(
        FanoutCollectionRun(**run), task_id, status, scope_revision=scope_revision,
    ))


@router.post("/api/v1/cases/{case_id}/fanout/{run_id}/aggregate")
def aggregate_case_fanout(case_id: str, run_id: str, payload: dict[str, Any],
                          request: Request) -> APIResponse:
    """coverage-aware Evidence 聚合：只以成功成员作结论来源，覆盖率不足只出局部结论。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    run = repo.get_fanout_run(case_id, tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="FanoutRun 不存在")
    snapshot = repo.get_membership_snapshot(case_id, tenant_id, run.get("snapshot_id") or "")
    if snapshot is None:
        raise HTTPException(status_code=409, detail="MembershipSnapshot 不存在")
    # repo 存储带 case_id/tenant_id；StrictModel 拒绝未知字段，只取模型字段。
    snapshot_obj = MembershipSnapshot(**{
        key: value for key, value in snapshot.items()
        if key in MembershipSnapshot.model_fields
    })
    report = fanout_service.aggregate(
        FanoutCollectionRun(**run),
        snapshot_obj,
        time_aligned=bool(payload.get("time_aligned", True)),
        artifact_signals=payload.get("artifact_signals"),
    )
    repo.record_case_event(
        case_id, tenant_id, event_type="fanout_aggregated",
        payload={
            "run_id": run_id,
            "conclusion": report.conclusion,
            "coverage": report.coverage,
        },
        actor_id=_request_principal(request),
    )
    return APIResponse(data={
        "coverage": report.model_dump(mode="json"),
        "run": repo.get_fanout_run(case_id, tenant_id, run_id),
    })


@router.post("/api/v1/cases/{case_id}/verification")
def verify_case_recovery(
    case_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """触发一次验证采集，对比诊断基线判断是否恢复（No-Regression 判定）。

    只在人工确认已执行建议动作后调用；验证采集使用与诊断相同的
    sys_metrics 采集器与目标实例，结果写入 Case 时间线。
    """
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    diagnosis_id = (
        payload.get("diagnosis_id") or case.get("diagnosis_session_id")
    ) if legacy_diagnosis_enabled() else None
    if not diagnosis_id:
        raise HTTPException(status_code=409, detail="Case 尚未关联诊断会话")
    diagnosis = diagnosis_orchestrator.get(diagnosis_id, advance=False)
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    conclusion = diagnosis.get("latest_conclusion") or {}
    # Recovery actions may replace a process.  Prefer the Case's refreshed
    # service→Agent→PID mapping over the immutable incident diagnosis scope.
    instances = (case.get("target_scope") or {}).get("instances") or \
        (diagnosis.get("target_scope") or {}).get("instances") or []
    if not instances:
        raise HTTPException(status_code=409, detail="缺少目标实例，无法验证")
    target = instances[0]

    # 1. 基线：诊断时的 sys_metrics 产物
    baseline_task = _find_diagnosis_sys_metrics_task(repo, diagnosis_id)
    baseline: dict[str, float] = {}
    if baseline_task is not None:
        for artifact in repo.artifacts.get(baseline_task.id, []):
            if artifact.get("artifact_type") != "sys_metrics":
                continue
            value = _extract_task_artifact_json(repo, baseline_task.id, "sys_metrics")
            if value is not None:
                baseline = _read_sys_metrics_artifact_keys(value)
                break

    # 2. 验证采集（同目标、同采集器、短时长）
    task = repo.create_task(
        CreateTaskRequest(
            name=f"验证恢复:{case_id[-8:]}",
            agent_id=target["agent_id"],
            target_pid=int(target["pid"]),
            collector_type="sys_metrics",
            sample_rate=11,
            duration_sec=VERIFICATION_TASK_DURATION_SEC,
            options={"source": "case_verification", "case_id": case_id, "diagnosis_id": diagnosis_id},
        ),
        idempotency_key=f"verify-{case_id}-{int(time.time() // 60)}",
        request_id=getattr(request.state, "request_id", "") or None,
        traceparent=getattr(request.state, "traceparent", "") or None,
    )
    deadline = time.time() + 90
    last_status = "PENDING"
    while time.time() < deadline:
        task_view = repo.tasks.get(task.id)
        if task_view is None:
            break
        last_status = status_value(task_view.status)
        if last_status in ("DONE", "FAILED", "CANCELLED"):
            break
        time.sleep(1.0)
    if last_status != "DONE":
        raise HTTPException(status_code=409, detail=f"验证采集未完成（{last_status}），请稍后重试")

    current: dict[str, float] = {}
    value = _extract_task_artifact_json(repo, task.id, "sys_metrics")
    if value is not None:
        current = _read_sys_metrics_artifact_keys(value)

    # 3. 判定 + 记录
    judgment = _judge_recovery(baseline, current)
    payload = {
        "verification_task_id": task.id,
        "diagnosis_id": diagnosis_id,
        "conclusion_summary": conclusion.get("summary", "")[:200],
        **judgment,
    }
    try:
        repo.record_case_event(
            case_id, tenant_id,
            event_type="verification_completed",
            payload=payload,
            actor_id=_request_principal(request),
        )
    except ValueError:
        pass
    repo.record_audit(
        event_type="CASE_VERIFICATION",
        message=f"Case {case_id} 验证完成: {judgment['status']}",
        metadata=payload,
    )
    return APIResponse(data=payload)


@router.post("/api/v1/cases/{case_id}/manual-actions")
def record_case_manual_action(
    case_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """用户回填人工执行建议动作的结果，进入多轮闭环（执行 → 验证 → 继续）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    action_ref = str(payload.get("action_ref") or "")
    result = str(payload.get("result") or "completed")
    notes = str(payload.get("notes") or "")[:1000]
    if result not in {"completed", "failed", "skipped"}:
        raise HTTPException(status_code=400, detail="result 必须是 completed/failed/skipped")
    record = {
        "action_ref": action_ref or "manual_action",
        "result": result,
        "notes": notes,
        "diagnosis_id": payload.get("diagnosis_id") or case.get("diagnosis_session_id"),
        "performed_at": payload.get("performed_at") or now_utc().isoformat(),
    }
    try:
        event = repo.record_case_event(
            case_id, tenant_id,
            event_type="manual_action",
            payload=record,
            actor_id=_request_principal(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.record_audit(
        event_type="CASE_MANUAL_ACTION",
        message=f"Case {case_id} 人工动作 {record['result']}: {record['action_ref']}",
        metadata=record,
    )
    return APIResponse(data={"case_id": case_id, "event": event, "record": record})


@router.post("/api/v1/cases/{case_id}/corrections")
def correct_incident_case(
    case_id: str,
    payload: CaseCorrectionRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    current = repo.get_incident_case(case_id, tenant_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    if (
        payload.expected_row_version is not None
        and current["row_version"] != payload.expected_row_version
    ):
        raise HTTPException(status_code=409, detail="CASE_VERSION_CONFLICT")
    superseded_diagnosis_id = (
        current.get("diagnosis_session_id") if legacy_diagnosis_enabled() else None
    )
    if superseded_diagnosis_id:
        try:
            diagnosis_orchestrator.cancel(
                superseded_diagnosis_id,
                "Case 范围或恢复目标已修正，旧诊断被替代",
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    changes = payload.model_dump(
        mode="json",
        exclude_none=True,
        exclude={"reason", "expected_row_version"},
    )
    # E1：correction 里 target_scope.evidence_task_ids 不再落入 target_scope，
    # 抽出后投影为 Attachment，由诊断统一消费（修复 G-01 断链）。
    legacy_task_ids: list[str] = []
    scope_change = changes.get("target_scope")
    if isinstance(scope_change, dict) and scope_change.get("evidence_task_ids"):
        legacy_task_ids = list(dict.fromkeys(scope_change.pop("evidence_task_ids")))
    try:
        result = repo.correct_incident_case(
            case_id,
            tenant_id,
            actor_id=_request_principal(request),
            changes=changes,
            reason=payload.reason,
            expected_row_version=payload.expected_row_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    if legacy_task_ids:
        evidence_attachment_service.attach_resources(
            result,
            tenant_id,
            [ResourceRef(type="task", id=str(task_id)) for task_id in legacy_task_ids],
            actor_id=_request_principal(request),
            purpose="批次关联（原 target_scope.evidence_task_ids）",
            source="collection_batch",
        )
    return APIResponse(data=result)


def _transition_case_from_api(
    case_id: str,
    payload: CaseTransitionRequest,
    request: Request,
    action: str,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    current = repo.get_incident_case(case_id, tenant_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    diagnosis_id = (
        current.get("diagnosis_session_id") if legacy_diagnosis_enabled() else None
    )
    diagnosis_changed = False
    try:
        if diagnosis_id and action == "pause":
            diagnosis_orchestrator.pause(diagnosis_id)
            diagnosis_changed = True
        elif diagnosis_id and action == "resume":
            diagnosis_orchestrator.resume(diagnosis_id)
            diagnosis_changed = True
        elif diagnosis_id and action == "stop":
            diagnosis_orchestrator.cancel(diagnosis_id, payload.reason)
            diagnosis_changed = True
        result = repo.transition_incident_case(
            case_id,
            tenant_id,
            actor_id=_request_principal(request),
            action=action,
            reason=payload.reason,
            expected_row_version=payload.expected_row_version,
        )
    except ValueError as exc:
        # Best-effort compensation keeps the Case and diagnosis controls aligned
        # if an optimistic Case update loses a race after the diagnosis changed.
        if diagnosis_id and diagnosis_changed:
            try:
                if action == "pause":
                    diagnosis_orchestrator.resume(diagnosis_id)
                elif action == "resume":
                    diagnosis_orchestrator.pause(diagnosis_id)
            except ValueError:
                pass
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if diagnosis_id and action == "resolve":
        try:
            diagnosis_orchestrator.cancel(diagnosis_id, payload.reason)
        except ValueError:
            # Completed diagnoses need no cancellation.
            pass
    if action in {"stop", "resolve"}:
        _cancel_case_tasks(case_id, tenant_id)
    return APIResponse(data=result)


class CaseCommandRequest(BaseModel):
    client_command_id: str | None = None
    command: str
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = "operator command"
    expected_case_command_revision: int | None = None
    expected_control_revision: int | None = None
    expected_scope_revision: int | None = None
    expected_plan_revision: int | None = None
    expected_campaign_revision: int | None = None


@router.post("/api/v1/cases/{case_id}/commands")
def apply_case_command(
    case_id: str,
    payload: CaseCommandRequest,
    request: Request,
) -> APIResponse:
    """v6 canonical control channel: deterministic command before model."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    principal_id = _request_principal(request)
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    command = str(payload.command).upper()
    target_id = payload.target_id
    conflicts: list[str] = []
    for attr, expected in (
        ("case_command_revision", payload.expected_case_command_revision),
        ("control_revision", payload.expected_control_revision),
        ("scope_revision", payload.expected_scope_revision),
    ):
        if expected is not None and int(case.get(attr) or 0) != int(expected):
            conflicts.append(f"{attr.upper()}_STALE")
    if conflicts:
        return APIResponse(code=409, message="revision conflict", data={
            "applied": False,
            "conflicts": conflicts,
            "current_revisions": {
                "case_command": case.get("case_command_revision") or 1,
                "control": case.get("control_revision") or 1,
                "scope": case.get("scope_revision") or 1,
            },
        })

    applied = False
    affected_task_ids: list[str] = []
    runtime_instruction = None
    if command in {"PAUSE", "RESUME", "STOP"}:
        transition_request = CaseTransitionRequest(
            reason=payload.reason,
            expected_row_version=None,
        )
        action = command.lower()
        _transition_case_from_api(case_id, transition_request, request, action)
        applied = True
        runtime_instruction = "abort" if command in {"PAUSE", "STOP"} else "resume"
        if command == "PAUSE" and runtime_mode() in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
            try:
                get_runtime().abort(case_id, "case paused by user command")
            except RuntimeError:
                pass
    elif command == "CANCEL_TASK":
        if not target_id:
            raise HTTPException(status_code=422, detail="CANCEL_TASK requires target_id")
        repo.cancel_task(target_id, payload.reason, Actor.WEB)
        affected_task_ids = [target_id]
        applied = True
    elif command == "CANCEL_STEP":
        if not target_id:
            raise HTTPException(status_code=422, detail="CANCEL_STEP requires target_id")
        updated = repo.update_plan_step(case_id, tenant_id, target_id, status="CANCELLED", reason=payload.reason)
        if updated is None:
            raise HTTPException(status_code=404, detail="STEP_NOT_FOUND")
        plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
        for step in plan.get("steps") or []:
            if step.get("step_id") == target_id:
                affected_task_ids = [str(item) for item in (step.get("task_ids") or [])]
                break
        for task_id in affected_task_ids:
            repo.cancel_task(task_id, payload.reason, Actor.WEB)
        applied = True
    elif command in {"RETARGET_STEP", "REORDER_STEPS", "REMOVE_STEP", "LOCK_STEP", "UNLOCK_STEP", "DISABLE_OPERATION", "ENABLE_OPERATION", "REVIEW_EVIDENCE"}:
        # Accepted for the control contract.  Detailed structural commands go
        # through the existing Plan/CAS services; here we record the command as
        # applied only after validating the target still exists.
        if target_id:
            if command == "REVIEW_EVIDENCE":
                evidence = repo.get_case_evidence(case_id, tenant_id, target_id)
                if evidence is None:
                    raise HTTPException(status_code=404, detail="EVIDENCE_NOT_FOUND")
            else:
                step = next(
                    (item for item in (investigation_plan_service.read_plan(case_id, tenant_id) or {}).get("steps") or []
                     if item.get("step_id") == target_id), None,
                )
                if step is None:
                    raise HTTPException(status_code=404, detail="STEP_NOT_FOUND")
        applied = True
    else:
        raise HTTPException(status_code=422, detail=f"UNSUPPORTED_COMMAND:{command}")

    if applied:
        if hasattr(repo, "enqueue_domain_outbox"):
            repo.enqueue_domain_outbox(
                aggregate_type="case_command",
                aggregate_id=case_id,
                event_type=f"CONTROL_{command}",
                payload={"command": command, "target_id": target_id, "payload": payload.payload},
                dedupe_key=f"command:{case_id}:{payload.client_command_id or secrets.token_hex(8)}:{command}",
            )
        repo.record_case_event(
            case_id,
            tenant_id,
            event_type="control.applied",
            payload={"command": command, "target_id": target_id, "reason": payload.reason},
            actor_id=principal_id,
        )
    updated_case = repo.get_incident_case(case_id, tenant_id) or {}
    return APIResponse(data={
        "command_id": f"cmd_{secrets.token_hex(8)}",
        "applied": applied,
        "new_case_command_revision": updated_case.get("case_command_revision") or 1,
        "new_control_revision": updated_case.get("control_revision") or 1,
        "new_scope_revision": updated_case.get("scope_revision") or 1,
        "new_plan_revision": payload.expected_plan_revision or 0,
        "new_campaign_revision": payload.expected_campaign_revision or 0,
        "affected_step_ids": [target_id] if target_id and command != "CANCEL_TASK" else [],
        "affected_task_ids": affected_task_ids,
        "runtime_instruction": runtime_instruction,
        "conflicts": [],
    })


class EvaluationBootstrapRequest(BaseModel):
    run_start_receipt: dict[str, Any] = Field(default_factory=dict)
    authority_digest: str = ""
    candidate_digest: str = ""


@router.post("/api/v1/internal/evaluation-runs/bootstrap")
def bootstrap_evaluation_run(payload: EvaluationBootstrapRequest, request: Request) -> APIResponse:
    """11.6 Formal Harness entry point.

    Without a host-mounted Authority and signed run-start receipt this route
    fail-closed; it never lets a browser self-select a namespace.
    """
    authority_path = os.getenv("MINI_DROP_FORMAL_AUTHORITY_PATH", "")
    receipt = payload.run_start_receipt or {}
    if not authority_path or not _Path(authority_path).is_file():
        raise HTTPException(status_code=409, detail="FORMAL_HARNESS_UNAVAILABLE")
    if not receipt.get("signature") or not payload.authority_digest or not payload.candidate_digest:
        raise HTTPException(status_code=401, detail="INVALID_RUN_START_RECEIPT")
    # Full Ed25519 verification against the host-mounted trust root is executed
    # only by the candidate-external Harness; the SUT cannot self-issue it.
    return APIResponse(data={
        "accepted": False,
        "reason": "RUN_START_RECEIPT_SIGNATURE_VERIFICATION_REQUIRED",
    })


@router.post("/api/v1/cases/{case_id}/pause")
def pause_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "pause")


@router.post("/api/v1/cases/{case_id}/resume")
def resume_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "resume")


@router.post("/api/v1/cases/{case_id}/stop")
def stop_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "stop")


@router.post("/api/v1/cases/{case_id}/resolve")
def resolve_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "resolve")


class SystemControlRequest(BaseModel):
    enabled: bool = True
    value: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/v1/controls")
def list_controls(request: Request) -> APIResponse:
    _require_role(request, "operator")
    return APIResponse(data=repo.list_system_controls())


@router.post("/api/v1/controls/{control_name}")
def set_control(control_name: str, payload: SystemControlRequest, request: Request) -> APIResponse:
    _require_role(request, "operator")
    allowed = {RED_BUTTON, CAPABILITY_EPOCH}
    if control_name not in allowed:
        raise HTTPException(status_code=400, detail="未知的控制项")
    result = repo.set_system_control(control_name, enabled=payload.enabled, value=payload.value)
    if control_name == CAPABILITY_EPOCH:
        result["note"] = "Capability Key 轮换后，旧纪元 Key 已失效，需重新签发。"
    return APIResponse(data=result)


@router.post("/api/v1/controls/capability-key/issue")
def issue_capability(request: Request, body: Optional[dict] = None) -> APIResponse:
    _require_role(request, "operator")
    body = body or {}
    source_ids = body.get("source_ids") or []
    key = issue_capability_key(repo, principal_id=_request_principal(request), source_ids=source_ids)
    return APIResponse(data=key)


@router.post("/api/v1/sources/{source_id}/query")
def query_registered_source(
    source_id: str,
    payload: SourceQueryRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    if payload.tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="SOURCE_TENANT_MISMATCH")
    started_at = time.perf_counter()
    try:
        envelope = source_gateway.query(
            source_id,
            payload,
            principal_id=_request_principal(request),
        )
    except SourceGatewayError as exc:
        record_source_access(
            source_id,
            f"error_{exc.status_code}",
            (time.perf_counter() - started_at) * 1000,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    record_source_access(
        source_id,
        "granted",
        (time.perf_counter() - started_at) * 1000,
        int(envelope.redactions.get("projected_bytes", 0)),
    )
    return APIResponse(data=envelope.model_dump(mode="json"))


@router.post("/api/v1/grants")
def create_authorization_grant(payload: CreateAuthorizationGrantRequest, request: Request) -> APIResponse:
    _require_role(request, "authorization_admin")
    if payload.tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="GRANT_TENANT_MISMATCH")
    selected = []
    for source_id in payload.source_ids:
        source = source_gateway.registry.get(source_id)
        if source is None:
            raise HTTPException(status_code=400, detail=f"未注册的信息源: {source_id}")
        if not source.enabled:
            raise HTTPException(status_code=409, detail=f"信息源未启用: {source_id}")
        selected.append(source)
    supported_operations = {operation for source in selected for operation in source.operations}
    unsupported = sorted(set(payload.operations) - supported_operations)
    if unsupported:
        raise HTTPException(status_code=400, detail=f"信息源不支持授权操作: {', '.join(unsupported)}")
    allowed_dimensions = {dimension for source in selected for dimension in source.resource_dimensions}
    unknown_dimensions = sorted(set(payload.resource_scope) - allowed_dimensions)
    if unknown_dimensions:
        raise HTTPException(status_code=400, detail=f"未知资源维度: {', '.join(unknown_dimensions)}")
    trusted_payload = payload.model_copy(update={"created_by": _request_principal(request)})
    created = repo.create_authorization_grant(trusted_payload.model_dump(mode="python"))
    return APIResponse(data=created)


@router.get("/api/v1/grants")
def list_authorization_grants(
    request: Request,
    principal_id: str = "",
    tenant_id: str = "",
    include_inactive: bool = False,
) -> APIResponse:
    _require_role(request, "authorization_admin")
    request_tenant = _request_tenant()
    if tenant_id.strip() and tenant_id.strip() != request_tenant:
        raise HTTPException(status_code=403, detail="GRANT_TENANT_MISMATCH")
    items = repo.list_authorization_grants(
        principal_id=principal_id.strip(),
        tenant_id=request_tenant,
        include_inactive=include_inactive,
    )
    return APIResponse(data={"items": items, "total": len(items)})


@router.delete("/api/v1/grants/{grant_id}")
def revoke_authorization_grant(grant_id: str, request: Request) -> APIResponse:
    _require_role(request, "authorization_admin")
    result = repo.revoke_authorization_grant(grant_id, _request_principal(request))
    if result is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    return APIResponse(data=result)


@router.post("/api/v1/policy/evaluate-source")
def evaluate_source_authorization(payload: AuthorizationEvaluationRequest, request: Request) -> APIResponse:
    _require_role(request, "authorization_admin")
    if payload.tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="SOURCE_TENANT_MISMATCH")
    grants = repo.list_authorization_grants(
        principal_id=payload.principal_id,
        tenant_id=payload.tenant_id,
        include_inactive=True,
    )
    result = evaluate_source_access(payload, grants, registry=source_gateway.registry)
    return APIResponse(data=result.model_dump(mode="json"))


@router.get("/api/v1/actions")
def list_registered_actions(request: Request) -> APIResponse:
    _require_role(request, "operator")
    items = [item.model_dump(mode="json") for item in DEFAULT_ACTION_REGISTRY.list()]
    return APIResponse(data={
        "schema_version": "action-registry.v1",
        "execution_enabled": any(item.get("implementation_status") == "executable" for item in items),
        "items": items,
    })


@router.post("/api/v1/actions/{action_id}/evaluate")
def evaluate_registered_action(
    action_id: str,
    payload: ActionEvaluationRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    if payload.tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    result = evaluate_action(action_id, payload)
    return APIResponse(data={
        **result.model_dump(mode="json"),
        "principal_id": _request_principal(request),
        "tenant_id": payload.tenant_id,
    })



__all__ = ["router", "verify_case_recovery"]
