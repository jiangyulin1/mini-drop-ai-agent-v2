"""
Mini-Drop HTTP API 入口。

启动 FastAPI 服务（端口 8191），同时在后台线程运行 gRPC server（端口 50051）。
两者共享同一个 SqlRepository 实例——Agent 通过 gRPC 上报的数据，
Web 通过 HTTP API 即时可见。
"""

from __future__ import annotations

import server.app._env  # noqa: F401 — 自动加载 .env

import hashlib
import os
import re
import secrets
import sys as _sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mini_drop_observability.tracing import (
    configure_tracing,
    shutdown_tracing,
    start_span,
    trace_id_from_current,
    traceparent_from_current,
)
import asyncio
from typing import Any

import server.app._env  # noqa: F401 — 自动加载 .env

import hashlib
import io
import os
import re
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path as _Path
from urllib.parse import quote as _url_quote

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from mini_drop_observability.tracing import (
    configure_tracing,
    shutdown_tracing,
    start_span,
    trace_id_from_current,
    traceparent_from_current,
)
import asyncio
import json as _json
import queue as _queue
from typing import Any, Optional

from server.app.common_utils import env_bool, status_value
from server.app.artifact_service import (
    evidence_artifact_links,
    inspect_artifact,
    read_artifact_bytes,
)
from server.app.ai_provider import get_ai_settings, model_audit_scope
from server.app.ai_validation import AIValidationBusy, run_ai_validation_suite
from server.app.database import init_db, new_session
from server.app.event_bus import BUS
from server.app.flamegraph_parser import extract_top_functions_from_svg
from server.app.prometheus_metrics import (
    REGISTRY,
    record_http_request,
    record_maintenance_step,
    record_source_access,
)
from server.app.grpc_server import serve_in_background
from server.app.logging_utils import log_event
from server.app.nlp.intent_parser import parse_intent
from server.app.nlp.process_resolver import resolve_pid
from server.app.nlp.summarizer import summarize, suggest_followup
from server.app.diagnosis import DiagnosisOrchestrator
from server.app.diagnosis.audit_trace import build_audit_bundle
from server.app.diagnosis.action_registry import (
    ActionEvaluationRequest,
    DEFAULT_ACTION_REGISTRY,
    evaluate_action,
)
from server.app.diagnosis.actuation import (
    ActuationError,
    ActuationGateway,
    is_executable,
)
from server.app.diagnosis.authorization import (
    AuthorizationDecision,
    AuthorizationEvaluationRequest,
    CreateAuthorizationGrantRequest,
    evaluate_source_access,
)
from server.app.diagnosis.autonomous_agent import AgentCallbacks, AutonomousIncidentAgent
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
from server.app.diagnosis.evidence_attachments import EvidenceAttachmentService
from server.app.diagnosis.cluster_scope import (
    EnvironmentProfile,
    MembershipSnapshot,
    TargetResolver,
)
from server.app.diagnosis.fanout import FanoutCollectionRun, FanoutCollectionService
from server.app.diagnosis.investigation_plan import (
    EvidenceReviewInput,
    InvestigationPlanService,
    PlanUpdateInput,
)
from server.app.diagnosis.mcp_fact_resolver import McpEvidenceService, McpFactResolver
from server.app.diagnosis.reference_resolver import ReferenceResolver, ResourceRef
from server.app.diagnosis.query_registry import QUERY_REGISTRY
from server.app.agent_runtime.config import (
    AgentRuntimeMode,
    agent_flags,
    agent_max_active_cases,
    runtime_mode,
)
from server.app.agent_runtime.dispatcher import active_runtime_info, get_runtime
from server.app.agent_runtime.port import AgentTurnInput, CaseContextSnapshot, RuntimeFollowUp
from server.app.agent_runtime.shadow import (
    build_deterministic_plan,
    compare_plans,
    request_shadow_plan,
)
from server.app.diagnosis.case_evidence import CaseEvidenceService
from server.app.diagnosis.v6_policy import (
    READ_ONLY_TOOLS,
    PROPOSE_ONLY_TOOLS,
    route_disposition,
    tool_policy_error,
    verify_claim_binding,
    verify_primary_confirmation,
)
from server.app.diagnosis.investigation_directive import build_directive
from server.app.diagnosis.knowledge import retrieve_knowledge
from server.app.diagnosis.skill_registry import SKILL_REGISTRY
from server.app.diagnosis.case_supervisor import CaseSupervisor
from server.app.diagnosis.campaign import CampaignCreateInput, build_campaign_plan, campaign_matrix
from server.app.diagnosis.plan_driver import PlanDriver
from server.app.diagnosis.governance import (
    CAPABILITY_EPOCH,
    RED_BUTTON,
    issue_capability_key,
)
from server.app.diagnosis.verification_contract import (
    build_verification_contract,
    evaluate_verification,
)
from server.app.diagnosis.recovery_verifier import RecoveryCheckError, run_http_checks
from server.app.diagnosis.distributed_actuation import DistributedActuationGateway
from server.app.diagnosis.probe_registry import list_probes as list_registered_probes
from server.app.diagnosis.source_gateway import SourceGateway, SourceGatewayError, SourceQueryRequest
from server.app.mcp_integration import MCPClientManager
from server.app.diagnosis.investigation_planner import (
    InvestigationActionCandidate,
    evaluate_investigation_stop,
    rank_investigation_actions,
)
from server.app.diagnosis.proposal_card import build_proposal_cards
from server.app.diagnosis.schemas import (
    ApprovalRequest,
    CreateDiagnosisRequest,
    TERMINAL_DIAGNOSIS_STATUSES,
)
from server.app.case_collaboration import (
    AttachResourcesRequest,
    CaseCorrectionRequest,
    CaseMessageRequest,
    CaseState,
    CaseTransitionRequest,
    CreateCaseRequest,
    CreateChangeRequest,
    CreateRecoveryPlanRequest,
    CreateTargetSessionRequest,
    CreateTargetSignalRequest,
    EvidenceReviewRequest,
    ExcludeAttachmentRequest,
    IndexProfileTaskRequest,
    PlanUpdateRequest,
    RecoveryPlanDecisionRequest,
    RecoveryPlanExecuteRequest,
    ReferenceSearchRequest,
    ReprioritizeStepRequest,
    RetargetStepRequest,
    TargetSessionTransitionRequest,
    StartCaseDiagnosisRequest,
    build_case_diagnosis_query,
    build_case_context_packet,
    serialize_time_range,
)
from server.app.schemas import (
    APIResponse,
    CancelTaskRequest,
    CreateTaskRequest,
    MAX_SAMPLE_RATE,
    MAX_TASK_DURATION_SEC,
    RCAFeedbackRequest,
    RetryTaskRequest,
    TaskView,
)
from server.app.sql_repository import SqlRepository
from server.app.state_machine import Actor, now_utc, TaskStatus
from server.app.task_kinds import list_task_kinds
from server.app.task_names import normalize_task_name
from server.app import storage as store

# ruff: noqa: F401

from server.app.common_utils import status_value
from server.app.database import init_db
from server.app.event_bus import BUS
from server.app.prometheus_metrics import (
    record_http_request,
    record_maintenance_step,
)
from server.app.grpc_server import serve_in_background
from server.app.logging_utils import log_event
from server.app.diagnosis import DiagnosisOrchestrator
from server.app.diagnosis.evidence_attachments import EvidenceAttachmentService
from server.app.diagnosis.cluster_scope import (
    TargetResolver,
)
from server.app.diagnosis.fanout import FanoutCollectionService
from server.app.diagnosis.investigation_plan import (
    InvestigationPlanService,
)
from server.app.diagnosis.mcp_fact_resolver import McpEvidenceService, McpFactResolver
from server.app.diagnosis.reference_resolver import ReferenceResolver
from server.app.agent_runtime.config import (
    AgentRuntimeMode,
    runtime_mode,
)
from server.app.agent_runtime.dispatcher import get_runtime
from server.app.agent_runtime.port import RuntimeFollowUp
from server.app.diagnosis.case_evidence import CaseEvidenceService
from server.app.diagnosis.source_gateway import SourceGateway
from server.app.mcp_integration import MCPClientManager
from server.app.schemas import (
    TaskView,
)
from server.app.sql_repository import SqlRepository
from server.app.state_machine import TaskStatus
from server.app import storage as store

configure_tracing("mini-drop-server")
repo = SqlRepository()
diagnosis_orchestrator = DiagnosisOrchestrator(repo)
try:
    mcp_client_manager = MCPClientManager()
except ValueError as exc:
    raise RuntimeError(f"invalid MCP connector configuration: {exc}") from exc
source_gateway = SourceGateway(
    repo,
    diagnosis_orchestrator,
    extra_connectors=mcp_client_manager.connectors,
    extra_source_definitions=mcp_client_manager.source_definitions(),
)
reference_resolver = ReferenceResolver(repo)
evidence_attachment_service = EvidenceAttachmentService(repo, reference_resolver)
case_evidence_service = CaseEvidenceService(repo)
investigation_plan_service = InvestigationPlanService(repo)
target_resolver = TargetResolver()
fanout_service = FanoutCollectionService(repo)
mcp_fact_resolver = McpFactResolver(
    native_collectors={"sys_metrics", "log_scan", "perf_cpu", "connection_probe"},
    registered_sources={item.source_id for item in mcp_client_manager.source_definitions()},
)
mcp_evidence_service = McpEvidenceService(
    mcp_fact_resolver,
    query_fn=lambda source_id, request, principal_id: source_gateway.query(
        source_id, request, principal_id=principal_id,
    ),
)


def _warn_on_insecure_defaults() -> None:
    """生产安全默认值检测：监听非回环地址但认证关闭时给出醒目告警。

    默认配置（认证关闭）适合开发/内网演示；暴露到不可信网络前必须设置
    MINI_DROP_API_AUTH_ENABLED=1 + MINI_DROP_API_KEY，以及
    MINI_DROP_GRPC_AUTH_ENABLED=1 + MINI_DROP_GRPC_TOKEN。
    """
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    api_auth = os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    grpc_auth = os.getenv("MINI_DROP_GRPC_AUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    if host not in {"127.0.0.1", "localhost"} and not (api_auth and grpc_auth):
        log_event(
            "warning",
            "insecure_default_config",
            bind_host=host,
            http_auth=api_auth,
            grpc_auth=grpc_auth,
            message=(
                "Server 监听非回环地址但 HTTP/gRPC 认证未全部开启。"
                "生产环境请设置 MINI_DROP_API_AUTH_ENABLED=1 + MINI_DROP_API_KEY "
                "和 MINI_DROP_GRPC_AUTH_ENABLED=1 + MINI_DROP_GRPC_TOKEN，"
                "或绑定 SERVER_HOST=127.0.0.1。"
            ),
        )


_warn_on_insecure_defaults()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用生命周期：启动时拉起 gRPC，关闭时停止。"""
    init_db()
    if os.getenv("MINIO_AUTO_CREATE_BUCKET", "0") == "1":
        _ensure_minio_bucket_with_retry(os.getenv("MINIO_BUCKET", "mini-drop"))
    grpc_port = int(os.getenv("MINI_DROP_GRPC_PORT", "50051"))
    if not 1 <= grpc_port <= 65535:
        raise RuntimeError("MINI_DROP_GRPC_PORT must be between 1 and 65535")
    _grpc = serve_in_background(repo, port=grpc_port)
    _offline_task = asyncio.create_task(_offline_sweeper())
    _autonomy_task = (
        asyncio.create_task(_autonomy_sweeper())
        if os.getenv("MINI_DROP_AUTONOMY_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        else None
    )
    _task_wake_task = asyncio.create_task(_task_wake_loop())
    _runtime_wakeup_task = asyncio.create_task(_runtime_wakeup_loop())
    _plan_driver_task = (
        asyncio.create_task(_plan_driver_sweeper())
        if os.getenv("MINI_DROP_PLAN_DRIVER_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
        else None
    )
    try:
        yield
    finally:
        _offline_task.cancel()
        if _autonomy_task is not None:
            _autonomy_task.cancel()
        _task_wake_task.cancel()
        _runtime_wakeup_task.cancel()
        if _plan_driver_task is not None:
            _plan_driver_task.cancel()
        try:
            await _offline_task
        except asyncio.CancelledError:
            pass
        if _autonomy_task is not None:
            try:
                await _autonomy_task
            except asyncio.CancelledError:
                pass
        try:
            await _task_wake_task
        except asyncio.CancelledError:
            pass
        try:
            await _runtime_wakeup_task
        except asyncio.CancelledError:
            pass
        if _plan_driver_task is not None:
            try:
                await _plan_driver_task
            except asyncio.CancelledError:
                pass
        _grpc.stop(grace=None).wait(timeout=5)
        shutdown_tracing()


async def _offline_sweeper() -> None:
    timeout_sec = int(os.getenv("AGENT_OFFLINE_TIMEOUT_SEC", "30"))
    stale_task_timeout_sec = int(os.getenv("TASK_STALE_TIMEOUT_SEC", "900"))
    interval_sec = max(1, min(timeout_sec // 2, 15))
    while True:
        # 同步阻塞调用（DB 读写 + MinIO 对象读取 + JSON 解析）必须移出
        # 事件循环，否则单 worker 下会阻塞全部 HTTP 请求（含 SSE）。
        try:
            await asyncio.to_thread(_run_offline_sweep_pass, timeout_sec, stale_task_timeout_sec)
        except Exception as exc:
            # _run_offline_sweep_pass isolates every known step. This final
            # guard keeps a future regression from permanently killing the
            # long-lived maintenance coroutine.
            record_maintenance_step("offline_sweep", "failure")
            log_event(
                "error",
                "maintenance_loop_failed",
                loop="offline_sweep",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
        await asyncio.sleep(interval_sec)


async def _plan_driver_sweeper() -> None:
    interval_sec = max(3, min(int(os.getenv("MINI_DROP_PLAN_DRIVER_INTERVAL_SEC", "8")), 60))
    while True:
        try:
            await asyncio.to_thread(_run_plan_driver_pass)
        except Exception as exc:
            record_maintenance_step("plan_driver", "failure")
            log_event("error", "plan_driver_loop_failed", error_type=type(exc).__name__,
                      error=str(exc)[:500])
        await asyncio.sleep(interval_sec)


async def _autonomy_sweeper() -> None:
    interval_sec = max(3, min(int(os.getenv("MINI_DROP_AUTONOMY_INTERVAL_SEC", "10")), 60))
    while True:
        try:
            await asyncio.to_thread(_run_autonomy_pass)
            record_maintenance_step("autonomy", "success")
        except Exception as exc:
            record_maintenance_step("autonomy", "failure")
            log_event(
                "error",
                "maintenance_loop_failed",
                loop="autonomy",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
        await asyncio.sleep(interval_sec)


def _run_autonomy_pass() -> None:
    tenant_id = _request_tenant()
    try:
        for outcome in CASE_SUPERVISOR.scan_and_advance(tenant_id, limit=200):
            case_id = outcome["case_id"]
            if outcome.get("outcome") == "BUSY":
                continue
            if outcome.get("outcome") in {"ESCALATED", "NOT_AUTONOMOUS"}:
                repo.record_audit(
                    event_type="AUTONOMOUS_AGENT_TICK",
                    message=f"Case {case_id} 自主循环结果 {outcome.get('outcome')}",
                    metadata={"case_id": case_id, "outcome": outcome.get("outcome")},
                )
    except Exception as exc:
        repo.record_audit(
            event_type="AUTONOMOUS_AGENT_TICK_FAILED",
            message="Case Supervisor 推进失败",
            metadata={"error": str(exc)[:500]},
        )
        raise


def _run_plan_driver_pass() -> None:
    """E4：扫描非终态 Case，自动调度 READ_LOW 计划步骤（Supervisor 自动调度）。

    MINI_DROP_AGENT_AUTO_READ_LOW=0 时后台扫描不得自动创建采集任务；
    计划步骤保留为待用户确认。显式 /agent/plan-driver 仍可用于人工触发。
    """
    from server.app.agent_runtime.config import agent_auto_read_low
    if not agent_auto_read_low():
        return
    tenant_id = _request_tenant()
    try:
        for case in repo.list_incident_cases(tenant_id=tenant_id, limit=200):
            case_id = str(case.get("case_id") or "")
            state = str(case.get("state") or "")
            if state in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
                continue
            if repo.get_investigation_plan(case_id, tenant_id) is None:
                continue
            try:
                PLAN_DRIVER.dispatch_case_ready_steps(case_id, tenant_id)
            except Exception:  # noqa: BLE001 — 单 Case 失败不阻断其它 Case
                repo.record_audit(
                    event_type="PLAN_DRIVER_TICK_FAILED",
                    message=f"Case {case_id} 计划调度失败",
                    metadata={"case_id": case_id},
                )
    except Exception as exc:
        repo.record_audit(
            event_type="PLAN_DRIVER_PASS_FAILED",
            message="计划驱动扫描失败",
            metadata={"error": str(exc)[:500]},
        )


async def _task_wake_loop() -> None:
    """E4：Task 完成唤醒 —— 订阅 task_changed，DONE/FAILED 立即推进 Case。"""
    queue = BUS.subscribe()
    try:
        while True:
            event = await asyncio.to_thread(queue.get)
            if event.get("event") != "task_changed":
                continue
            data = event.get("data") or {}
            to_status = str(data.get("to_status") or "")
            if to_status not in {"DONE", "FAILED", "CANCELLED"}:
                continue
            task_id = str(data.get("task_id") or "")
            if not task_id:
                continue
            try:
                _wake_case_from_task(task_id, to_status)
            except Exception as exc:  # noqa: BLE001 — 唤醒失败不阻断事件流
                log_event(
                    "warning",
                    "task_wake_failed",
                    task_id=task_id,
                    to_status=to_status,
                    error_type=type(exc).__name__,
                    error=str(exc)[:300],
                )
    finally:
        BUS.unsubscribe(queue)


def _ensure_active_investigation_run(case_id: str, tenant_id: str) -> dict[str, Any] | None:
    if hasattr(repo, "list_investigation_runs"):
        runs = repo.list_investigation_runs(case_id, tenant_id)
        active = [
            item for item in runs
            if item.get("status") not in {"RESOLVED", "STOPPED", "INSUFFICIENT_EVIDENCE", "FAILED"}
        ]
        if active:
            return active[0]
        if runs:
            return None
    return repo.create_investigation_run(
        case_id=case_id,
        tenant_id=tenant_id,
    ) if hasattr(repo, "create_investigation_run") else None


def _deliver_one_wakeup(
    case: dict[str, Any],
    tenant_id: str,
    wakeup: dict[str, Any],
    run: dict[str, Any],
) -> bool:
    """Seal Wakeup -> Snapshot/Cycle/ModelRequest -> durable Sidecar delivery."""
    case_id = str(case.get("case_id") or "")
    wakeup_id = str(wakeup.get("wakeup_id") or "")
    if not wakeup_id or wakeup.get("status") != "PENDING":
        return False
    context = _build_runtime_case_context(
        case, tenant_id,
        disposition="INVESTIGATE",
        side_effect_policy="AUTO_READ_LOW",
        investigation_run_id=run.get("run_id"),
    )
    snapshot = repo.create_case_context_snapshot(
        case_id=case_id,
        tenant_id=tenant_id,
        investigation_run_id=run.get("run_id"),
        content=context.model_dump(mode="json"),
    ) if hasattr(repo, "create_case_context_snapshot") else None
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    generation = int((binding or {}).get("runtime_generation") or 1)
    cycle = repo.create_agent_cycle(
        case_id=case_id,
        tenant_id=tenant_id,
        run_id=run["run_id"],
        trigger_type="EVIDENCE_COMMITTED",
        trigger_ref=wakeup_id,
        context_snapshot_id=snapshot.get("snapshot_id") if snapshot else None,
        evidence_watermark=int(wakeup.get("to_evidence_watermark") or 0),
        runtime_binding_id=case_id,
        generation=generation,
    ) if hasattr(repo, "create_agent_cycle") else None
    model_request = None
    if cycle and hasattr(repo, "create_model_request"):
        projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(repo, "list_evidence_projections") else []
        model_request = repo.create_model_request(
            case_id=case_id,
            tenant_id=tenant_id,
            run_id=run["run_id"],
            cycle_id=cycle["cycle_id"],
            input_snapshot_hash=snapshot.get("snapshot_hash") if snapshot else None,
            evidence_projection_hashes=[
                item.get("projection_hash") for item in projections
                if item.get("projection_hash")
            ],
            idempotency_key=f"mreq:{wakeup_id}:{cycle['cycle_id']}",
        )
    if hasattr(repo, "seal_runtime_wakeup"):
        repo.seal_runtime_wakeup(wakeup_id, cycle_id=cycle.get("cycle_id") if cycle else None)
    try:
        runtime = get_runtime()
        runtime.follow_up(
            case_id,
            RuntimeFollowUp(
                case_id=case_id,
                note=f"新 Evidence 已物化：{', '.join(str(item) for item in (wakeup.get('source_refs') or []))}；请读取 Projection 后继续",
                evidence_ids=[
                    item.get("evidence_id")
                    for item in case_evidence_service.list_evidence(case_id, tenant_id)
                ],
            ),
        )
        if hasattr(repo, "consume_runtime_wakeup"):
            repo.consume_runtime_wakeup(wakeup_id, "DELIVERED")
        return True
    except RuntimeError:
        if cycle and hasattr(repo, "transition_agent_cycle"):
            repo.transition_agent_cycle(cycle["cycle_id"], "QUEUED")
        if model_request and hasattr(repo, "transition_model_request"):
            repo.transition_model_request(model_request["model_request_id"], "QUEUED")
        return False


async def _runtime_wakeup_loop() -> None:
    interval_sec = max(2, min(int(os.getenv("MINI_DROP_WAKEUP_INTERVAL_SEC", "5")), 30))
    while True:
        try:
            await asyncio.to_thread(_run_runtime_wakeup_pass)
        except Exception as exc:
            log_event("error", "runtime_wakeup_loop_failed", error_type=type(exc).__name__, error=str(exc)[:500])
        await asyncio.sleep(interval_sec)


def _run_runtime_wakeup_pass() -> None:
    tenant_id = _request_tenant()
    if not hasattr(repo, "list_incident_cases") or not hasattr(repo, "list_runtime_wakeups"):
        return
    for case in repo.list_incident_cases(tenant_id, state="")[:100]:
        case_id = case.get("case_id") or case.get("id") or ""
        if not case_id or case.get("state") in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
            continue
        for wakeup in repo.list_runtime_wakeups(case_id, tenant_id, status="PENDING")[:10]:
            run = repo.get_investigation_run(case_id, tenant_id, wakeup["investigation_run_id"]) if hasattr(repo, "get_investigation_run") else None
            if run is None:
                run = _ensure_active_investigation_run(case_id, tenant_id)
            if run is None:
                continue
            _deliver_one_wakeup(case, tenant_id, wakeup, run)


def _wake_case_from_task(task_id: str, to_status: str) -> None:
    task = repo.tasks.get(task_id)
    if task is None:
        return
    options = (task.request_params or {}).get("options") or {}
    case_id = str(options.get("case_id") or "")
    tenant_id = str(options.get("tenant_id") or _request_tenant())
    if not case_id:
        return
    outcome = PLAN_DRIVER.on_task_done(case_id, tenant_id, task_id, status=to_status)
    if to_status == "DONE" and getattr(task, "collector_type", ""):
        evidence_ids = case_evidence_service.materialize_task_artifacts(
            case_id,
            tenant_id,
            task_id=task_id,
            actor_id="mini-drop-task-wake",
        )
        log_event(
            "info",
            "task_wake_evidence_materialized",
            task_id=task_id,
            case_id=case_id,
            evidence_count=len(evidence_ids),
        )
        if evidence_ids:
            case = repo.get_incident_case(case_id, tenant_id) or {}
            if case.get("state") not in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
                run = _ensure_active_investigation_run(case_id, tenant_id)
                if run is not None and runtime_mode() in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
                    watermark = len(case_evidence_service.list_evidence(case_id, tenant_id))
                    if hasattr(repo, "enqueue_domain_outbox"):
                        repo.enqueue_domain_outbox(
                            aggregate_type="evidence_batch",
                            aggregate_id=f"{case_id}:{task_id}",
                            event_type="EVIDENCE_COMMITTED",
                            payload={
                                "case_id": case_id,
                                "task_id": task_id,
                                "evidence_ids": evidence_ids,
                                "evidence_watermark": watermark,
                            },
                            dedupe_key=f"evidence-batch:{case_id}:{task_id}:{','.join(sorted(evidence_ids))}",
                        )
                    if hasattr(repo, "create_runtime_wakeup"):
                        wakeup = repo.create_runtime_wakeup(
                            case_id=case_id,
                            tenant_id=tenant_id,
                            investigation_run_id=run["run_id"],
                            reason=f"Task {task_id} 完成并产生 {len(evidence_ids)} 条 canonical Evidence",
                            source_refs=[f"task:{task_id}"],
                            control_revision=int(case.get("control_revision") or 1),
                            scope_revision=int(case.get("scope_revision") or 1),
                            reason_class="EVIDENCE_COMMITTED",
                            from_evidence_watermark=max(0, watermark - len(evidence_ids)),
                            to_evidence_watermark=watermark,
                            dedupe_key=f"wakeup:{case_id}:{run['run_id']}:{case.get('control_revision') or 1}:{case.get('scope_revision') or 1}:EVIDENCE_COMMITTED",
                        )
                        _deliver_one_wakeup(case, tenant_id, wakeup, run)
    return outcome


def _run_case_task_wake_pass() -> None:
    """G5/G6：Analyzer Worker 在独立进程中完成 Task，Server 事件总线看不到其
    task_changed 事件。周期扫描 Case 派生且已 DONE 但尚未物化 Evidence 的 Task，
    执行与实时唤醒相同的逻辑。
    """
    if hasattr(repo, "_cache"):
        repo._cache.pop("tasks", None)
    for task in list(getattr(repo, "tasks", {}).values()):
        if status_value(getattr(task, "status", "")) != TaskStatus.DONE.value:
            continue
        options = (getattr(task, "request_params", None) or {}).get("options") or {}
        case_id = str(options.get("case_id") or "")
        if not case_id:
            continue
        tenant_id = str(options.get("tenant_id") or _request_tenant())
        existing = repo.list_case_evidence(case_id, tenant_id) if hasattr(repo, "list_case_evidence") else []
        if any(str(item.get("task_id") or "") == str(task.id) for item in existing):
            continue
        try:
            _wake_case_from_task(str(task.id), TaskStatus.DONE.value)
        except Exception as exc:  # noqa: BLE001
            log_event(
                "warning",
                "case_task_wake_sweep_failed",
                task_id=str(task.id),
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )


def _run_offline_sweep_pass(timeout_sec: int, stale_task_timeout_sec: int) -> None:
    _run_maintenance_step(
        "agent_offline_detection",
        lambda: repo.mark_offline_agents(timeout_sec=timeout_sec),
    )
    _run_maintenance_step(
        "stale_task_recovery",
        lambda: repo.recover_stale_tasks(timeout_sec=stale_task_timeout_sec),
    )
    if hasattr(repo, "persist_agent_metric_snapshots"):
        _run_maintenance_step("agent_metric_snapshot", repo.persist_agent_metric_snapshots)
    _run_maintenance_step("diagnosis_advance", diagnosis_orchestrator.advance_active)
    _run_maintenance_step("case_task_wake", _run_case_task_wake_pass)


def _run_maintenance_step(step: str, operation) -> bool:
    """Run one periodic step without allowing it to starve sibling steps."""
    try:
        operation()
    except Exception as exc:
        record_maintenance_step(step, "failure")
        log_event(
            "error",
            "maintenance_step_failed",
            step=step,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return False
    record_maintenance_step(step, "success")
    return True


def _ensure_minio_bucket_with_retry(bucket: str) -> None:
    attempts = max(1, int(os.getenv("MINI_DROP_MINIO_READY_RETRIES", "5")))
    delay_sec = max(0.0, float(os.getenv("MINI_DROP_MINIO_READY_DELAY_SEC", "1")))
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            store.ensure_bucket(bucket)
            return
        except Exception as exc:
            last_exc = exc
            log_event(
                "warning",
                "minio_bucket_init_retry",
                bucket=bucket,
                attempt=attempt,
                attempts=attempts,
                error=type(exc).__name__,
            )
            if attempt < attempts and delay_sec > 0:
                time.sleep(delay_sec)

    if last_exc is None:
        raise RuntimeError("minio_bucket_init_failed: all retries exhausted with no exception")
    raise last_exc


app = FastAPI(title="Mini-Drop Server", version="0.1.0", lifespan=_lifespan)

# CORS 中间件：允许前端跨域开发访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("MINI_DROP_CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# request-id 中间件：为每个 HTTP 请求生成唯一 ID，注入响应头、请求状态和结构化日志
@app.middleware("http")
async def _request_id(request: Request, call_next):
    import uuid
    requested_id = request.headers.get("x-request-id", "").strip()
    rid = (
        requested_id
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", requested_id)
        else uuid.uuid4().hex[:12]
    )
    with start_span(
        f"{request.method} {request.url.path}",
        traceparent=request.headers.get("traceparent"),
        kind="server",
        attributes={
            "http.request.method": request.method,
            "url.path": request.url.path,
            "mini_drop.request.id": rid,
        },
    ):
        request.state.request_id = rid
        request.state.traceparent = traceparent_from_current()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        trace_id = trace_id_from_current()
        if trace_id:
            response.headers["x-trace-id"] = trace_id
        return response


@app.middleware("http")
async def _access_log(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        log_event(
            "error",
            "http_request_failed",
            request_id=getattr(request.state, "request_id", ""),
            method=request.method,
            path=request.url.path,
            error=type(exc).__name__,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        raise

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    log_event(
        "info",
        "http_request",
        request_id=getattr(request.state, "request_id", ""),
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )
    record_http_request(request.method, request.url.path, response.status_code, latency_ms)
    return response


@app.middleware("http")
async def _api_key_auth(request: Request, call_next):
    token = _extract_api_token(request)
    if _requires_api_auth(request):
        expected = os.getenv("MINI_DROP_API_KEY", "")
        if not expected:
            return JSONResponse(
                status_code=500,
                content={"detail": "API auth enabled but MINI_DROP_API_KEY is empty"},
            )
        if not token or not secrets.compare_digest(token, expected):
            return JSONResponse(status_code=401, content={"detail": "无效 API Key"})
    request.state.principal_id = _principal_for_request(token)
    request.state.principal_roles = _roles_for_request()
    return await call_next(request)


def _task_view(record) -> TaskView:
    """将 TaskRecord 转为前端模型。"""
    return TaskView(
        id=record.id,
        name=record.name,
        agent_id=record.agent_id,
        target_pid=record.target_pid,
        collector_type=record.collector_type,
        sample_rate=record.sample_rate,
        duration_sec=record.duration_sec,
        status=status_value(record.status),
        status_reason=record.status_reason,
        collection_status=getattr(record, "collection_status", None) or status_value(record.status),
        analysis_status=getattr(record, "analysis_status", None) or "WAITING",
        current_attempt_id=getattr(record, "current_attempt_id", None),
        row_version=int(getattr(record, "row_version", 0) or 0),
        collection_deadline_at=getattr(record, "collection_deadline_at", None),
        request_id=getattr(record, "request_id", None),
        request_params=record.request_params,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _requires_api_auth(request: Request) -> bool:
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    path = request.url.path
    return path.startswith("/api/") and path not in {
        "/api/healthz", "/api/livez", "/api/readyz", "/api/metrics",
        "/api/auth/set-cookie", "/api/auth/clear-cookie",
    }


def _extract_api_token(request: Request) -> str | None:
    # 1. Authorization: Bearer <token> header
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 2. X-API-Key header
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    # 3. HttpOnly cookie (preferred for browser clients — resists XSS exfiltration)
    cookie = request.cookies.get("mini_drop_api_key")
    if cookie:
        return cookie.strip()
    return None


def _principal_for_request(token: str | None) -> str:
    """Bind authorization to a server-derived identity, never a request body."""
    configured = os.getenv("MINI_DROP_API_PRINCIPAL_ID", "").strip()
    if configured:
        return configured[:128]
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        return f"api-key:{digest}"
    return "local-development"


def _roles_for_request() -> set[str]:
    configured = os.getenv("MINI_DROP_API_ROLES", "").strip()
    if configured:
        return {item.strip() for item in configured.split(",") if item.strip()}
    # Authentication-disabled mode is an explicit local-development mode.
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return {"operator", "authorization_admin"}
    return {"operator"}


def _request_principal(request: Request) -> str:
    return getattr(request.state, "principal_id", "local-development")


def _request_tenant() -> str:
    """Bind Case access to server-side identity configuration, not request JSON."""
    tenant_id = os.getenv("MINI_DROP_API_TENANT_ID", "local-development").strip()
    return (tenant_id or "local-development")[:128]


def _require_role(request: Request, role: str) -> None:
    roles = getattr(request.state, "principal_roles", set())
    if role not in roles:
        raise HTTPException(status_code=403, detail=f"当前主体缺少角色: {role}")



def _resolve_query_target(
    case: dict[str, Any],
    target_ref: str,
) -> dict[str, Any] | None:
    instances = (case.get("target_scope") or {}).get("instances") or []
    if target_ref:
        for item in instances:
            if target_ref in {
                str(item.get("instance_id") or ""),
                str(item.get("agent_id") or ""),
                str(item.get("pid") or ""),
            }:
                return {"agent_id": str(item.get("agent_id") or ""), "pid": int(item.get("pid") or 1)}
    for item in instances:
        return {"agent_id": str(item.get("agent_id") or ""), "pid": int(item.get("pid") or 1)}
    for agent in getattr(repo, "agents", {}).values():
        if isinstance(agent, dict):
            agent_id = str(agent.get("agent_id") or agent.get("id") or "")
            status = str(agent.get("status") or "ONLINE")
        else:
            agent_id = str(getattr(agent, "id", "") or "")
            status = str(getattr(agent, "status", "") or "ONLINE")
        if status == "ONLINE" and agent_id:
            return {"agent_id": agent_id, "pid": 1}
    return None


def _resolve_artifact_path_or_none(local_path: str | None) -> _Path | None:
    try:
        return _resolve_artifact_path(local_path)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _read_artifact_object_text(artifact: dict) -> str:
    bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
    key = _validate_presign_request(bucket, artifact.get("object_key", ""))
    try:
        return store.read_object_bytes(bucket, key).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_event("warning", "artifact_object_read_failed", bucket=bucket, object_key=key, error=type(exc).__name__)
        raise HTTPException(status_code=404, detail="对象存储产物不存在") from exc


def _validate_presign_request(bucket: str, key: str) -> str:
    allowed_bucket = os.getenv("MINIO_BUCKET", "mini-drop")
    if bucket != allowed_bucket:
        raise HTTPException(status_code=403, detail="bucket 不在允许范围内")
    if not key:
        raise HTTPException(status_code=400, detail="key 参数不能为空")
    normalized = key.replace("\\", "/")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="key 路径不合法")
    if not normalized.startswith("tasks/"):
        raise HTTPException(status_code=403, detail="key 不在任务产物目录内")
    return normalized


def _safe_download_filename(value: str) -> str:
    filename = _Path(value.replace("\\", "/")).name
    filename = "".join(ch for ch in filename if ch >= " " and ch not in {'"', ';'})
    return filename[:255] or "artifact.bin"



__all__ = [name for name in list(globals()) if not name.startswith("__")]


def _artifact_root() -> _Path:
    return _Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def _resolve_artifact_path(local_path: str | None) -> _Path:
    if not local_path:
        raise HTTPException(status_code=404, detail="本地产物不存在")

    root = _artifact_root()
    candidate = _Path(local_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="产物路径不在允许目录内")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="本地产物不存在")
    return resolved

def _extract_artifact_json(artifacts: list[dict], artifact_type: str):
    """从 artifacts 列表中提取指定类型的 JSON 数据。"""
    for art in artifacts:
        if art.get("artifact_type") == artifact_type:
            local_path = art.get("local_path", "")
            try:
                path = _resolve_artifact_path_or_none(local_path)
                if path is not None:
                    return _json.loads(path.read_text(encoding="utf-8"))
                if art.get("object_key"):
                    return _json.loads(_read_artifact_object_text(art))
            except HTTPException as exc:
                log_event(
                    "warning",
                    "artifact_json_unavailable",
                    artifact_type=artifact_type,
                    local_path=local_path,
                    status_code=exc.status_code,
                )
                return None
            except Exception as exc:
                log_event(
                    "warning",
                    "artifact_json_parse_failed",
                    artifact_type=artifact_type,
                    local_path=local_path,
                    error=type(exc).__name__,
                )
                return None
    return None


def _extract_task_artifact_json(repository, task_id: str, artifact_type: str):
    """Read a JSON artifact from one task without leaking repository layout to callers."""
    return _extract_artifact_json(repository.artifacts.get(task_id, []), artifact_type)

@app.get("/api/healthz")
def healthz(core_only: bool = False) -> APIResponse:
    """健康检查端点：验证服务自身及关键依赖（数据库、对象存储）的状态。

    Kubernetes liveness/readiness probe 可通过此端点区分：
      - 200 + healthy=true  → 服务完全可用
      - 200 + healthy=false → 服务存活但依赖不可用（readiness 应标记为未就绪）
      - 非 200               → 服务未存活
    """
    checks: dict[str, dict] = {}

    # 数据库连通性检查
    try:
        from sqlalchemy import text as _sa_text
        session = new_session()
        try:
            session.execute(_sa_text("SELECT 1"))
        finally:
            session.close()
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        log_event(
            "warning",
            "health_dependency_failed",
            dependency="database",
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        checks["database"] = {
            "status": "unavailable",
            "error_code": "dependency_unavailable",
        }

    storage_required = env_bool(
        "MINI_DROP_REQUIRE_STORAGE",
        env_bool("MINIO_AUTO_CREATE_BUCKET"),
    )
    if not storage_required:
        checks["storage"] = {"status": "disabled"}
    else:
        # 对象存储只读检查。Bucket 创建只允许发生在启动阶段，避免高频
        # readiness 探针意外执行写操作或掩盖部署配置错误。
        try:
            bucket = os.getenv("MINIO_BUCKET", "mini-drop")
            if store.bucket_available(bucket):
                checks["storage"] = {"status": "ok"}
            else:
                checks["storage"] = {
                    "status": "unavailable",
                    "error_code": "bucket_missing",
                }
        except Exception as exc:
            log_event(
                "warning",
                "health_dependency_failed",
                dependency="storage",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            checks["storage"] = {
                "status": "unavailable",
                "error_code": "dependency_unavailable",
            }

    analyzer_required = os.getenv("MINI_DROP_REQUIRE_ANALYZER", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    try:
        analyzer = repo.analysis_health(
            timeout_sec=int(os.getenv("MINI_DROP_ANALYZER_OFFLINE_TIMEOUT_SEC", "30")),
        )
        if not analyzer_required and analyzer["workers_online"] == 0:
            analyzer["status"] = "disabled"
        checks["analyzer"] = analyzer
    except Exception as exc:
        log_event(
            "warning",
            "health_dependency_failed",
            dependency="analyzer",
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        checks["analyzer"] = {
            "status": "unavailable" if analyzer_required else "disabled",
            "error_code": "dependency_unavailable",
        }

    effective_checks = {
        key: value for key, value in checks.items()
        if not (core_only and key == "analyzer")
    }
    all_ok = all(c["status"] in {"ok", "disabled"} for c in effective_checks.values())
    return APIResponse(data={
        "service": "mini-drop-server",
        "version": "0.1.0",
        "healthy": all_ok,
        "checks": checks,
    })


@app.get("/api/livez")
def livez() -> APIResponse:
    """Process liveness probe; dependency failures must not trigger restarts."""

    return APIResponse(data={
        "service": "mini-drop-server",
        "version": "0.1.0",
        "alive": True,
    })


@app.get("/api/readyz")
def readyz(response: Response, core_only: bool = False) -> APIResponse:
    """Dependency-aware readiness probe with a conventional 503 failure."""

    report = healthz(core_only=core_only)
    if not report.data["healthy"]:
        response.status_code = 503
    return report


@app.get("/api/ai-config")
def ai_config() -> APIResponse:
    """Return safe AI configuration metadata without exposing the API key."""
    settings = get_ai_settings()
    return APIResponse(data={
        "enabled": settings.enabled,
        "provider": settings.provider,
        "base_url": settings.base_url,
        "model": settings.model,
        "has_api_key": bool(settings.api_key),
        "features": {
            "nlp": settings.nlp_enabled,
            "rca": settings.rca_enabled,
            "summarize": settings.summarize_enabled,
        },
    })


@app.post("/api/ai-validation/runs")
def run_ai_validation() -> APIResponse:
    """Run the complete provider + Drop AI validation suite on demand."""
    try:
        result = run_ai_validation_suite()
    except AIValidationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=result)



# When executed as `python -m server.app.main`, the module is loaded as
# ``__main__``.  Alias it into ``sys.modules`` before route modules import
# ``server.app.main`` so exactly one module object exists and the circular
# import remains impossible.
if __name__ == "__main__" and __package__:
    _sys.modules[__package__ + ".main"] = _sys.modules[__name__]

# Export the complete legacy namespace (including underscore helpers) to extracted route modules.
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.v6_routes import *  # noqa: F403,F405
from server.app.v6_routes import (  # noqa: F401
    _build_runtime_case_context,
    _case_investigation_footprint,
    _create_case_query_task,
)
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.common import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.agents_process import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.tasks import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.diagnoses import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.cases import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.plans_control import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.recovery import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.fanout import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.actuation import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

from server.app.routes.nlp import *  # noqa: F403,F405
__all__ = [name for name in list(globals()) if not name.startswith("__")]

# ── 启动入口 ──────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8191")),
    )
