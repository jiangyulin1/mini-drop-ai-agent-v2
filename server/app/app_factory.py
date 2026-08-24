"""
Mini-Drop HTTP application factory and lifecycle composition.

启动 FastAPI 服务（端口 8191），同时在后台线程运行 gRPC server（端口 50051）。
两者共享同一个 SqlRepository 实例——Agent 通过 gRPC 上报的数据，
Web 通过 HTTP API 即时可见。
"""

from __future__ import annotations

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

from fastapi import Depends, FastAPI, HTTPException, Request
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
from server.app.capability_tokens import canonical_hash
from server.app.database import init_db, new_session
from server.app.event_bus import BUS
from server.app.http.auth import valid_web_session
from server.app.flamegraph_parser import extract_top_functions_from_svg
from server.app.prometheus_metrics import (
    REGISTRY,
    record_http_request,
    record_maintenance_step,
    record_source_access,
)
from server.app.grpc_server import serve_in_background
from server.app.jobs.outbox_relay import OutboxRelay
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
from server.app.agent_runtime.options import RuntimeOptions
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
from server.app.state_machine import Actor, now_utc, TaskStatus
from server.app.task_kinds import list_task_kinds
from server.app.task_names import normalize_task_name
from server.app import storage as store
from server.app.application.health_service import HealthService
from server.app.application.task_views import task_view
from server.app.container import AppContainer
from server.app.http.dependencies import bind_request_application_services
from server.app.http.routers.health import router as health_router
from server.app.routes.common import router as common_router
from server.app.routes.agents_process import router as agents_process_router
from server.app.routes.diagnoses import router as diagnoses_router
from server.app.routes.cases import _case_agent_progress, router as cases_router
from server.app.routes.plans_control import router as plans_control_router
from server.app.routes.fanout import router as fanout_router
from server.app.routes.recovery import _judge_recovery
from server.app.routes.actuation import (
    ACTUATION_GATEWAY,
    AUTONOMOUS_AGENT,
    CASE_SUPERVISOR,
    PLAN_DRIVER,
    router as actuation_router,
)
from server.app.routes.nlp import router as nlp_router
from server.app.routes.tasks import router as tasks_router
from server.app.routes.knowledge_memory import router as knowledge_memory_router
from server.app.routes.topology_discovery import router as topology_discovery_router
from server.app.v6_routes import (
    QueryError,
    _build_runtime_case_context,
    _case_investigation_footprint,
    _create_case_query_task,
    router as v6_router,
)
from server.app.runtime_services import (
    bind_application_services,
    build_application_services,
    case_evidence_service,
    collection_supervisor,
    diagnosis_orchestrator,
    evidence_analysis_service,
    evidence_attachment_service,
    fanout_service,
    investigation_plan_service,
    install_compatibility_default,
    mcp_client_manager,
    mcp_evidence_service,
    reference_resolver,
    repo,
    source_gateway,
    target_resolver,
)

# ruff: noqa: F401

configure_tracing("mini-drop-server")


def _database_health_probe() -> None:
    from sqlalchemy import text as _sa_text

    session = new_session()
    try:
        session.execute(_sa_text("SELECT 1"))
    finally:
        session.close()


def _build_container() -> AppContainer:
    services = build_application_services()
    return AppContainer(
        application_services=services,
        health_service=HealthService(
            database_probe=_database_health_probe,
            storage_probe=lambda bucket: store.bucket_available(bucket),
            analyzer_probe=lambda timeout: services.repository.analysis_health(
                timeout_sec=timeout,
            ),
            log_warning=log_event,
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
async def _bound_lifespan(_app: FastAPI):
    """Bind one app's service graph across all lifecycle background jobs."""

    with bind_application_services(_app.state.container.application_services):
        async with _service_lifespan(_app):
            yield


@asynccontextmanager
async def _service_lifespan(_app: FastAPI):
    """应用生命周期：启动时拉起 gRPC，关闭时停止。"""
    init_db()
    if os.getenv("MINIO_AUTO_CREATE_BUCKET", "0") == "1":
        _ensure_minio_bucket_with_retry(os.getenv("MINIO_BUCKET", "mini-drop"))
    grpc_port = int(os.getenv("MINI_DROP_GRPC_PORT", "50051"))
    if not 1 <= grpc_port <= 65535:
        raise RuntimeError("MINI_DROP_GRPC_PORT must be between 1 and 65535")
    _grpc = serve_in_background(
        _app.state.container.application_services.repository,
        port=grpc_port,
    )
    _offline_task = asyncio.create_task(_offline_sweeper())
    _autonomy_task = (
        asyncio.create_task(_autonomy_sweeper())
        if os.getenv("MINI_DROP_AUTONOMY_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        else None
    )
    _task_wake_task = asyncio.create_task(_task_wake_loop())
    _runtime_wakeup_task = asyncio.create_task(_runtime_wakeup_loop())
    _outbox_relay_task = asyncio.create_task(_outbox_relay_loop())
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
        _outbox_relay_task.cancel()
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
        try:
            await _outbox_relay_task
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


def _latest_runtime_turn_preferences(
    case_id: str,
    tenant_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Keep autonomous Evidence wakeups on the initiating Turn's policy/options."""
    if not hasattr(repo, "list_context_packets"):
        return None, None, None
    packets = repo.list_context_packets(case_id, tenant_id, limit=100) or []
    runtime_turns = [item for item in packets if item.get("purpose") == "runtime_turn"]
    if not runtime_turns:
        return None, None, None
    packet = max(runtime_turns, key=lambda item: str(item.get("created_at") or ""))
    payload = packet.get("payload") or {}
    raw_options = payload.get("runtime_options") or {}
    allowed_option_keys = set(RuntimeOptions.model_fields)
    runtime_options = {
        key: value for key, value in raw_options.items()
        if key in allowed_option_keys
    }
    return (
        payload.get("runtime_policy") or None,
        runtime_options or None,
        str(payload.get("diagnostic_strategy_id") or "") or None,
    )


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
    reason_class = str(wakeup.get("reason_class") or "EVIDENCE_COMMITTED")
    # Runtime wakeups carry evidence references in their durable source
    # ledger.  Use only those references for this cycle; a Case-wide query here
    # would silently turn every sibling investigation into a shared context.
    wakeup_evidence_ids: set[str] = {
        str(item).strip() for item in (wakeup.get("evidence_ids") or [])
        if str(item).strip()
    }
    task_refs: set[str] = set()
    for ref in wakeup.get("source_refs") or []:
        raw_ref = str(ref or "")
        if raw_ref.startswith("evidence-review:"):
            wakeup_evidence_ids.add(raw_ref[len("evidence-review:"):].split(":r", 1)[0])
        elif raw_ref.startswith("evidence:"):
            wakeup_evidence_ids.add(raw_ref[len("evidence:"):].split(":", 1)[0])
        elif raw_ref.startswith("task:"):
            task_id = raw_ref[len("task:"):].split(":", 1)[0].strip()
            if task_id:
                task_refs.add(task_id)
    # Older EVIDENCE_COMMITTED wakeups carried only task:<id>. Resolve those
    # references narrowly against the named task so the branch does not fall
    # back to a Case-wide Evidence query. Newer wakeups should carry explicit
    # evidence:<id> refs, but this keeps durable rows written before that
    # contract change deliverable.
    if task_refs:
        for item in case_evidence_service.list_evidence(case_id, tenant_id):
            if str(item.get("task_id") or "") in task_refs:
                evidence_id = str(item.get("evidence_id") or "").strip()
                if evidence_id:
                    wakeup_evidence_ids.add(evidence_id)
    # A conclusion is terminal only for the current evidence watermark. New
    # Evidence opens a new exploration revision; the prior conclusion remains
    # durable and is included in the next Case snapshot for comparison.
    inherited_policy, inherited_options, inherited_strategy_id = (
        _latest_runtime_turn_preferences(case_id, tenant_id)
    )
    cycle = (
        repo.get_agent_cycle(str(wakeup.get("cycle_id")))
        if wakeup.get("cycle_id") and hasattr(repo, "get_agent_cycle")
        else None
    )
    branch_id = str(wakeup.get("branch_id") or "").strip() or None
    if cycle and hasattr(repo, "get_investigation_tree_node"):
        root = repo.get_investigation_tree_node(
            case_id, tenant_id, f"tnode_cycle_{cycle.get('cycle_id')}"
        )
        branch_id = str((root or {}).get("branch_id") or "").strip() or branch_id
    if branch_id is None and reason_class in {"EVIDENCE_REVIEWED", "EVIDENCE_ELIGIBILITY_CHANGED"} and hasattr(repo, "list_investigation_tree"):
        tree = repo.list_investigation_tree(case_id, tenant_id, run_id=run.get("run_id"))
        affected = {
            str(item.get("evidence_id") or "")
            for item in (wakeup.get("evidence_ids") or []) if str(item)
        }
        for dep in tree.get("dependencies") or []:
            if str(dep.get("target_kind") or "").upper() == "EVIDENCE" and str(dep.get("target_id") or "") in affected:
                node = next((n for n in tree.get("nodes") or [] if n.get("node_id") == dep.get("node_id")), None)
                if node and node.get("branch_id"):
                    branch_id = str(node["branch_id"])
                    break
    intervention: dict[str, Any] = {}
    if reason_class in {"EVIDENCE_REVIEWED", "EVIDENCE_ELIGIBILITY_CHANGED"}:
        review_refs = [str(item) for item in (wakeup.get("source_refs") or [])]
        evidence_items = case_evidence_service.list_evidence(case_id, tenant_id)
        affected_ids: list[str] = []
        revision_after = 0
        for ref in review_refs:
            # Review wakeups use evidence-review:<id>:r<revision> refs. Keep
            # the original ref for audit, but expose the canonical ID and
            # current revision in the structured intervention.
            evidence_id = ref
            if ref.startswith("evidence-review:"):
                evidence_id = ref[len("evidence-review:"):].split(":r", 1)[0]
            elif ref.startswith("evidence:"):
                # Evidence governance outbox events use evidence:<id>; keep
                # source_refs intact for audit while projecting the canonical
                # ID into the structured intervention.
                evidence_id = ref[len("evidence:"):].split(":", 1)[0]
            if evidence_id and evidence_id not in affected_ids:
                affected_ids.append(evidence_id)
            matching = next(
                (item for item in evidence_items if str(item.get("evidence_id")) == evidence_id),
                None,
            )
            if matching:
                revision_after = max(revision_after, int(matching.get("review_revision") or 0))
        revision_before = max(0, revision_after - 1)
        intervention = {
            "intervention_id": f"intervention-{wakeup_id}",
            "kind": reason_class,
            "source_refs": review_refs,
            "affected_evidence_ids": affected_ids,
            "required": True,
            "trust_state": "RECHECK_REQUIRED",
            "evidence_state_rechecked": False,
            "revision_before": revision_before,
            "revision_after": revision_after,
            "wakeup_id": wakeup_id,
        }
    context = _build_runtime_case_context(
        case, tenant_id,
        disposition="INVESTIGATE",
        side_effect_policy="AUTO_READ_LOW",
        investigation_run_id=run.get("run_id"),
        runtime_policy=inherited_policy,
        runtime_options=inherited_options,
        strategy_id=inherited_strategy_id,
        intervention=intervention,
        evidence_ids=sorted(wakeup_evidence_ids),
        branch_id=branch_id,
    )
    # Evidence lifecycle changes invalidate every old Pi transcript. Rotate
    # the durable generation before creating the cycle so stale tool events
    # are rejected by the server as well as discarded by the Sidecar.
    if reason_class in {"EVIDENCE_REVIEWED", "EVIDENCE_ELIGIBILITY_CHANGED"} and cycle is None:
        binding = repo.get_agent_runtime_binding(case_id, tenant_id) or {}
        new_generation = int(binding.get("runtime_generation") or 1) + 1
        context = context.model_copy(update={
            "runtime_generation": new_generation,
            "runtime_session_id": "",
        })
        if hasattr(repo, "upsert_agent_runtime_binding"):
            repo.upsert_agent_runtime_binding(
                case_id,
                tenant_id,
                runtime_type=binding.get("runtime_type") or "pi",
                runtime_version=binding.get("runtime_version") or "pi",
                runtime_session_id="",
                runtime_generation=new_generation,
                status="REBUILD_REQUIRED",
                last_event_seq=0,
                last_context_snapshot_id=None,
                lease_owner=binding.get("lease_owner"),
            )
    snapshot = None
    model_request = None
    if cycle is None:
        context_payload = context.model_dump(mode="json")
        packet = repo.create_context_packet({
            "case_id": case_id,
            "tenant_id": tenant_id,
            "schema_version": "case-context.v1",
            "purpose": "runtime_wakeup",
            "iteration_no": 0,
            "payload": context_payload,
            "projection_stats": {},
            "source_versions": {
                "context_builder": "runtime-wakeup.v1",
                "source_registry": "source-registry.v1",
            },
            "content_hash": canonical_hash(context_payload),
            "created_by": "mini-drop-agent-runtime",
        }) if hasattr(repo, "create_context_packet") else None
        if packet:
            context = context.model_copy(update={
                "context_packet_id": packet["context_packet_id"],
            })
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
            trigger_type=reason_class,
            trigger_ref=wakeup_id,
            context_snapshot_id=snapshot.get("snapshot_id") if snapshot else None,
            evidence_watermark=int(wakeup.get("to_evidence_watermark") or 0),
            runtime_binding_id=case_id,
            generation=generation,
            branch_id=branch_id,
        ) if hasattr(repo, "create_agent_cycle") else None
        if cycle and hasattr(repo, "create_model_request"):
            projections = []
            if hasattr(repo, "list_evidence_projections"):
                for evidence_id in sorted(wakeup_evidence_ids):
                    projections.extend(repo.list_evidence_projections(
                        case_id, tenant_id, evidence_id=evidence_id,
                    ))
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
        binding = None
        if hasattr(runtime, "start_or_resume"):
            binding = runtime.start_or_resume(context)
        if binding is not None and hasattr(repo, "upsert_agent_runtime_binding"):
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
        if reason_class == "COLLECTION_TERMINAL":
            source_refs = ", ".join(str(item) for item in (wakeup.get("source_refs") or []))
            terminal_detail = str(wakeup.get("reason") or "collection terminated without Evidence")
            note = (
                f"采集任务已失败、取消或完成但未产生 Evidence：{source_refs}。"
                f"终态详情：{terminal_detail}。"
                "请读取 CollectionRequest 获取权威状态；不要假设存在新 Evidence。"
                "请记录 limitation，并使用现有 Evidence 提交结构化终态；"
                "只有在仍缺少决定性事实时，才选择一个可执行的替代 Collector。"
            )
            follow_up_evidence_ids: list[str] = []
        elif reason_class in {"EVIDENCE_REVIEWED", "EVIDENCE_ELIGIBILITY_CHANGED"}:
            source_refs = ", ".join(str(item) for item in (wakeup.get("source_refs") or []))
            note = (
                f"专家已修改 Evidence 推理准入：{source_refs}。"
                "必须先调用 get_case_snapshot 或 list_evidence，确认最新 lifecycle_status、"
                "review_trust_state、review_revision 和 projection_hash；排除证据不得继续作为支持依据。"
                "随后重新评估假设、反证和缺失事实，并通过 finish_investigation 提交新结论。"
            )
            follow_up_evidence_ids = sorted(wakeup_evidence_ids)
        else:
            queued_analyses = [
                item for item in evidence_analysis_service.list_runs(case_id, tenant_id)
                if item.get("status") in {"QUEUED", "RUNNING"}
                and item.get("input_state") == "CURRENT"
            ]
            analysis_run_id = str(queued_analyses[-1].get("analysis_run_id") or "") if queued_analyses else ""
            source_summary = ", ".join(str(item) for item in (wakeup.get("source_refs") or []))
            if analysis_run_id:
                note = (
                    f"新 Evidence 已物化：{source_summary}；"
                    f"本批次的 EvidenceAnalysisRun 是 {analysis_run_id}。"
                    "旧结论仅对旧 Evidence watermark 有效；请展示并保留旧结论，先读取该运行锁定的 Evidence Projection，再调用 submit_evidence_analysis。"
                    "每个 fact 必须提供 claim、certainty，以及包含 evidence_id、projection_hash、field_path 的准确 citations。"
                    "分析提交成功后，在保留历史的前提下更新假设、缺口和因果图并决定下一步。"
                )
            else:
                # A topology run may materialize Evidence directly through its
                # bounded orchestration path, without creating an
                # EvidenceAnalysisRun.  Never tell the model that the run is
                # "未创建" and then invite it to invent an ID: that produces
                # a predictable ANALYSIS_RUN_NOT_FOUND rejection.  In this
                # branch the canonical terminal contract is finish_investigation.
                note = (
                    f"新 Evidence 已物化：{source_summary}；本批次没有预注册的 EvidenceAnalysisRun。"
                    "不要调用 submit_evidence_analysis，也不要编造 analysis_run_id。"
                    "旧结论仅对旧 Evidence watermark 有效；请先展示并读取旧结论、当前 Evidence Projection、依赖图和证据缺口，"
                    "保留旧结论和本批次新 Evidence 的历史，重新探索并提交新的结论 revision；"
                    "然后使用 finish_investigation 提交带 evidence_id、projection_hash、field_path 引用的结构化结论；"
                    "如果证据不足，使用 INSUFFICIENT_EVIDENCE，并明确缺失事实。"
                )
            follow_up_evidence_ids = sorted(wakeup_evidence_ids)
        runtime.follow_up(
            case_id,
            RuntimeFollowUp(
                case_id=case_id,
                note=note,
                evidence_ids=follow_up_evidence_ids,
                intervention=intervention,
            ),
        )
        if hasattr(repo, "consume_runtime_wakeup"):
            repo.consume_runtime_wakeup(wakeup_id, "DELIVERED")
        return True
    except RuntimeError as exc:
        if cycle and hasattr(repo, "transition_agent_cycle"):
            repo.transition_agent_cycle(cycle["cycle_id"], "QUEUED")
        if model_request and hasattr(repo, "transition_model_request"):
            repo.transition_model_request(model_request["model_request_id"], "QUEUED")
        if hasattr(repo, "requeue_runtime_wakeup"):
            repo.requeue_runtime_wakeup(
                wakeup_id, cycle_id=cycle.get("cycle_id") if cycle else None,
            )
        log_event(
            "warning",
            "runtime_wakeup_delivery_deferred",
            case_id=case_id,
            wakeup_id=wakeup_id,
            error_type=type(exc).__name__,
        )
        return False


async def _runtime_wakeup_loop() -> None:
    interval_sec = max(2, min(int(os.getenv("MINI_DROP_WAKEUP_INTERVAL_SEC", "5")), 30))
    while True:
        try:
            await asyncio.to_thread(_run_runtime_wakeup_pass)
        except Exception as exc:
            log_event("error", "runtime_wakeup_loop_failed", error_type=type(exc).__name__, error=str(exc)[:500])
        await asyncio.sleep(interval_sec)


async def _outbox_relay_loop() -> None:
    interval_sec = max(
        1,
        min(int(os.getenv("MINI_DROP_OUTBOX_RELAY_INTERVAL_SEC", "2")), 30),
    )
    while True:
        try:
            await asyncio.to_thread(_run_outbox_relay_pass)
        except Exception as exc:
            log_event(
                "error",
                "outbox_relay_loop_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
        await asyncio.sleep(interval_sec)


def _dispatch_domain_outbox_event(event: dict[str, Any]) -> None:
    """Apply one idempotent local effect before the relay acknowledges it."""

    event_id = str(event.get("outbox_id") or "")
    payload = event.get("payload") or {}
    event_type = str(event.get("event_type") or "")
    if event_type not in {
        "EVIDENCE_COMMITTED", "COLLECTION_TERMINAL", "EVIDENCE_ELIGIBILITY_CHANGED",
    }:
        repo.record_outbox_consumer_effect(
            event_id=event_id,
            consumer_name="control-plane",
            effect_key=f"observe:{event_id}",
            effect_payload={"event_type": event.get("event_type")},
        )
        return

    case_id = str(payload.get("case_id") or "")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    run_id = str(payload.get("investigation_run_id") or "")
    if not case_id:
        raise ValueError(f"{event_type} requires case_id and investigation_run_id")
    if not run_id and event_type == "EVIDENCE_ELIGIBILITY_CHANGED":
        repo.record_outbox_consumer_effect(
            event_id=event_id,
            consumer_name="control-plane",
            effect_key=f"review-without-runtime:{event_id}",
            effect_payload={"case_id": case_id, "event_type": event_type},
        )
        return
    if not run_id:
        raise ValueError(f"{event_type} requires case_id and investigation_run_id")
    case = repo.get_incident_case(case_id, tenant_id) or {}
    run = repo.get_investigation_run(case_id, tenant_id, run_id)
    if not case or run is None:
        raise ValueError("outbox aggregate is no longer available")

    wakeup = repo.get_runtime_wakeup_by_outbox(event_id)
    if wakeup is None:
        reason_class = {
            "COLLECTION_TERMINAL": "COLLECTION_TERMINAL",
            "EVIDENCE_ELIGIBILITY_CHANGED": "EVIDENCE_ELIGIBILITY_CHANGED",
        }.get(event_type, "EVIDENCE_COMMITTED")
        if reason_class == "COLLECTION_TERMINAL":
            dedupe_key = (
                f"collection-terminal-wakeup:{payload.get('task_id')}:{payload.get('task_status')}"
            )
        elif reason_class == "EVIDENCE_ELIGIBILITY_CHANGED":
            dedupe_key = f"evidence-review-wakeup:{event_id}"
        else:
            dedupe_key = (
                f"evidence-wakeup:{case_id}:{run_id}:"
                f"{int(payload.get('control_revision') or 1)}:"
                f"{int(payload.get('scope_revision') or 1)}"
            )
        wakeup = repo.create_runtime_wakeup(
            case_id=case_id,
            tenant_id=tenant_id,
            investigation_run_id=run_id,
            reason=str(payload.get("reason") or "canonical Evidence committed"),
            source_refs=list(payload.get("source_refs") or []),
            control_revision=int(payload.get("control_revision") or 1),
            scope_revision=int(payload.get("scope_revision") or 1),
            reason_class=reason_class,
            from_evidence_watermark=int(payload.get("from_evidence_watermark") or 0),
            to_evidence_watermark=int(payload.get("to_evidence_watermark") or 0),
            # All Evidence committed before the 5-second runtime delivery pass
            # belongs to one wakeup. create_runtime_wakeup merges source refs
            # and advances the watermark while the row remains PENDING.
            dedupe_key=dedupe_key,
        )
        repo.add_runtime_wakeup_source(
            wakeup_id=wakeup["wakeup_id"],
            outbox_id=event_id,
            source_ref=(payload.get("source_refs") or [f"outbox:{event_id}"])[0],
            evidence_watermark=int(payload.get("to_evidence_watermark") or 0),
        )
    repo.record_outbox_consumer_effect(
        event_id=event_id,
        consumer_name="runtime-wakeup",
        effect_key=f"wakeup:{wakeup['wakeup_id']}",
        effect_payload={"wakeup_id": wakeup["wakeup_id"], "case_id": case_id},
    )


def _run_outbox_relay_pass(limit: int = 100):
    relay = OutboxRelay(
        repo,
        _dispatch_domain_outbox_event,
        relay_id=f"control-{os.getpid()}",
    )
    return relay.run_once(limit=limit)


def _run_runtime_wakeup_pass() -> None:
    tenant_id = _request_tenant()
    if not hasattr(repo, "list_incident_cases") or not hasattr(repo, "list_runtime_wakeups"):
        return
    for case in repo.list_incident_cases(tenant_id, state="")[:100]:
        case_id = case.get("case_id") or case.get("id") or ""
        if not case_id or case.get("state") in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
            continue
        if not _collection_batch_ready_for_wakeup(case_id, tenant_id):
            continue
        for wakeup in repo.list_runtime_wakeups(case_id, tenant_id, status="PENDING")[:10]:
            run = repo.get_investigation_run(case_id, tenant_id, wakeup["investigation_run_id"]) if hasattr(repo, "get_investigation_run") else None
            if run is None:
                run = _ensure_active_investigation_run(case_id, tenant_id)
            if run is None:
                continue
            _deliver_one_wakeup(case, tenant_id, wakeup, run)


def _collection_batch_ready_for_wakeup(case_id: str, tenant_id: str) -> bool:
    """Wait for one dispatched Collector batch and its outbox writes to settle."""
    requests = repo.list_collection_requests(case_id, tenant_id) if hasattr(
        repo, "list_collection_requests",
    ) else []
    if any(
        item.get("task_id") and item.get("status") in {"ACCEPTED", "DISPATCHED", "RUNNING"}
        for item in requests
    ):
        return False
    updated = [item.get("updated_at") for item in requests if item.get("updated_at")]
    if not updated:
        return True
    quiet_sec = max(0.0, float(os.getenv("MINI_DROP_WAKEUP_QUIET_SEC", "3")))
    latest = max(updated)
    if isinstance(latest, str):
        latest = datetime.fromisoformat(latest.replace("Z", "+00:00"))
    now = now_utc()
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=now.tzinfo)
    return (now - latest).total_seconds() >= quiet_sec


def _wake_case_from_task(task_id: str, to_status: str) -> None:
    task = repo.tasks.get(task_id)
    if task is None:
        return
    options = (task.request_params or {}).get("options") or {}
    case_id = str(options.get("case_id") or "")
    tenant_id = str(options.get("tenant_id") or _request_tenant())
    if not case_id:
        return
    current_case = repo.get_incident_case(case_id, tenant_id) or {}
    stale_reasons: list[str] = []
    pinned_scope = options.get("scope_revision")
    pinned_control = options.get("control_revision")
    if pinned_scope is not None and int(pinned_scope or 0) != int(current_case.get("scope_revision") or 1):
        stale_reasons.append("SCOPE_REVISION_CHANGED")
    if pinned_control is not None and int(pinned_control or 0) != int(current_case.get("control_revision") or 1):
        stale_reasons.append("CONTROL_REVISION_CHANGED")
    pinned_reviews = options.get("input_evidence_review_revisions") or {}
    if isinstance(pinned_reviews, dict):
        for evidence_id, pinned_revision in pinned_reviews.items():
            current_evidence = repo.get_case_evidence(case_id, tenant_id, str(evidence_id))
            if current_evidence is None:
                stale_reasons.append(f"INPUT_EVIDENCE_MISSING:{evidence_id}")
                continue
            pinned_review_revision = (
                pinned_revision.get("review_revision")
                if isinstance(pinned_revision, dict) else pinned_revision
            )
            if int(current_evidence.get("review_revision") or 0) != int(pinned_review_revision or 0):
                stale_reasons.append(f"INPUT_EVIDENCE_REVIEW_CHANGED:{evidence_id}")
    pinned_generation = options.get("runtime_generation")
    if pinned_generation is not None and hasattr(repo, "get_agent_runtime_binding"):
        binding = repo.get_agent_runtime_binding(case_id, tenant_id) or {}
        if int(binding.get("runtime_generation") or 1) != int(pinned_generation or 1):
            stale_reasons.append("RUNTIME_GENERATION_CHANGED")
    stale_for_current_revision = bool(stale_reasons)
    terminal_requests = collection_supervisor.mark_task_terminal(
        case_id, tenant_id, task_id, to_status,
    )
    outcome = PLAN_DRIVER.on_task_done(case_id, tenant_id, task_id, status=to_status)
    evidence_ids: list[str] = []
    current_evidence_rows: list[dict[str, Any]] | None = None
    if to_status == "DONE" and getattr(task, "collector_type", ""):
        evidence_ids = case_evidence_service.materialize_task_artifacts(
            case_id,
            tenant_id,
            task_id=task_id,
            actor_id="mini-drop-task-wake",
            stale_for_current_revision=stale_for_current_revision,
        )
        log_event(
            "info",
            "task_wake_evidence_materialized",
            task_id=task_id,
            case_id=case_id,
            evidence_count=len(evidence_ids),
        )
        if evidence_ids and not stale_for_current_revision:
            # A task wakeup must not turn the entire Case Evidence store into
            # a shared live context for every investigation chain.  The
            # current cycle receives only the Evidence materialized by this
            # task; older results can be loaded later through an explicit,
            # fingerprint-checked reuse decision.
            evidence_id_set = {str(item) for item in evidence_ids if str(item)}
            current_evidence_rows = case_evidence_service.list_evidence(
                case_id, tenant_id,
            )
            task_evidence_ids = [
                str(item.get("evidence_id") or "")
                for item in current_evidence_rows
                if str(item.get("evidence_id") or "") in evidence_id_set
                and item.get("status") == "ACTIVE"
                and not bool(item.get("stale_for_current_revision"))
            ]
            if task_evidence_ids:
                try:
                    analysis = evidence_analysis_service.create_run(
                        case_id=case_id,
                        tenant_id=tenant_id,
                        evidence_ids=task_evidence_ids,
                        mode="SINGLE" if len(task_evidence_ids) == 1 else "MULTI",
                        prompt_version="evidence-analysis.v1",
                    )
                    log_event(
                        "info",
                        "task_wake_analysis_queued",
                        task_id=task_id,
                        case_id=case_id,
                        analysis_run_id=analysis.get("analysis_run_id"),
                        reused=bool(analysis.get("reused")),
                    )
                except ValueError as exc:
                    log_event(
                        "warning",
                        "task_wake_analysis_deferred",
                        task_id=task_id,
                        case_id=case_id,
                        reason=str(exc)[:300],
                    )
    terminal_without_evidence = bool(terminal_requests) and (
        to_status in {"FAILED", "CANCELLED"} or (to_status == "DONE" and not evidence_ids)
    )
    if (evidence_ids and not stale_for_current_revision) or terminal_without_evidence:
        case = repo.get_incident_case(case_id, tenant_id) or current_case
        if case.get("state") not in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
            run = _ensure_active_investigation_run(case_id, tenant_id)
            if run is not None and runtime_mode() in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
                # Reuse the snapshot already loaded for the task-scoped
                # analysis when possible; this avoids a second Case-wide
                # Evidence query during every terminal wakeup.
                watermark = len(
                    current_evidence_rows
                    if current_evidence_rows is not None
                    else case_evidence_service.list_evidence(case_id, tenant_id)
                )
                if hasattr(repo, "enqueue_domain_outbox"):
                    if evidence_ids:
                        repo.enqueue_domain_outbox(
                            aggregate_type="evidence_batch",
                            aggregate_id=f"{case_id}:{task_id}",
                            event_type="EVIDENCE_COMMITTED",
                            payload={
                                "case_id": case_id,
                                "task_id": task_id,
                                "tenant_id": tenant_id,
                                "investigation_run_id": run["run_id"],
                                "evidence_ids": evidence_ids,
                                # Keep the durable source-ref contract
                                # task-scoped for compatibility.  The wakeup
                                # delivery path resolves this task to its own
                                # Evidence rows, never to the whole Case.
                                "source_refs": [f"task:{task_id}"],
                                "control_revision": int(case.get("control_revision") or 1),
                                "scope_revision": int(case.get("scope_revision") or 1),
                                "from_evidence_watermark": max(0, watermark - len(evidence_ids)),
                                "to_evidence_watermark": watermark,
                                "reason": (
                                    f"Task {task_id} 完成并产生 {len(evidence_ids)} 条 canonical Evidence"
                                ),
                            },
                            dedupe_key=f"evidence-batch:{case_id}:{task_id}:{','.join(sorted(evidence_ids))}",
                        )
                    else:
                        request_ids = [
                            str(item.get("collection_request_id") or "")
                            for item in terminal_requests
                            if item.get("collection_request_id")
                        ]
                        task_reason = str(getattr(task, "status_reason", "") or to_status)
                        repo.enqueue_domain_outbox(
                            aggregate_type="collection_terminal",
                            aggregate_id=f"{case_id}:{task_id}",
                            event_type="COLLECTION_TERMINAL",
                            payload={
                                "case_id": case_id,
                                "task_id": task_id,
                                "task_status": to_status,
                                "task_reason": task_reason[:1000],
                                "collection_request_ids": request_ids,
                                "tenant_id": tenant_id,
                                "investigation_run_id": run["run_id"],
                                "source_refs": [
                                    f"collection_request:{request_id}:{to_status}"
                                    for request_id in request_ids
                                ] or [f"task:{task_id}:{to_status}"],
                                "control_revision": int(case.get("control_revision") or 1),
                                "scope_revision": int(case.get("scope_revision") or 1),
                                "from_evidence_watermark": watermark,
                                "to_evidence_watermark": watermark,
                                "reason": (
                                    f"Task {task_id} entered {to_status} without canonical Evidence: "
                                    f"{task_reason[:300]}"
                                ),
                            },
                            dedupe_key=f"collection-terminal:{task_id}:{to_status}",
                        )
                    # Keep the synchronous helper contract for API/tests;
                    # production also runs the same relay continuously.
                    _run_outbox_relay_pass()
    elif evidence_ids and stale_for_current_revision:
        # Keep the late result in the immutable Case store for audit and
        # explicit reuse, but do not wake the current investigation cycle.
        repo.record_case_event(
            case_id,
            tenant_id,
            event_type="evidence_committed_stale",
            payload={
                "task_id": task_id,
                "evidence_ids": evidence_ids,
                "stale_reasons": stale_reasons,
                "control_revision": current_case.get("control_revision"),
                "scope_revision": current_case.get("scope_revision"),
            },
            actor_id="mini-drop-task-wake",
        )
        log_event(
            "info",
            "task_wake_evidence_fenced",
            task_id=task_id,
            case_id=case_id,
            reasons=stale_reasons,
        )
    return outcome


def _run_case_task_wake_pass() -> None:
    """Recover terminal collection Tasks missed by the in-process event bus.

    Analyzer Workers can finish in another process, so the durable sweep covers
    successful, failed and cancelled CollectionRequests.
    """
    repo.invalidate_cache("tasks")
    for task in list(getattr(repo, "tasks", {}).values()):
        task_status = status_value(getattr(task, "status", ""))
        if task_status not in {
            TaskStatus.DONE.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value,
        }:
            continue
        options = (getattr(task, "request_params", None) or {}).get("options") or {}
        case_id = str(options.get("case_id") or "")
        if not case_id:
            continue
        if task_status != TaskStatus.DONE.value and not options.get("collection_request_id"):
            continue
        tenant_id = str(options.get("tenant_id") or _request_tenant())
        existing = repo.list_case_evidence(case_id, tenant_id) if hasattr(repo, "list_case_evidence") else []
        if task_status == TaskStatus.DONE.value and any(
            str(item.get("task_id") or "") == str(task.id) for item in existing
        ):
            # Some bounded workflows (notably topology discovery) materialize
            # canonical Evidence before the cross-process recovery sweep sees
            # the terminal Task event.  Evidence idempotency must not leave the
            # associated CollectionRequest stuck in DISPATCHED, otherwise the
            # runtime batch gate blocks every later Evidence wakeup.
            collection_supervisor.mark_task_terminal(
                case_id, tenant_id, str(task.id), task_status,
            )
            continue
        try:
            _wake_case_from_task(str(task.id), task_status)
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
    # The pre-v6 DiagnosisSession loop is compatibility-only. New deployments
    # keep it frozen unless an operator explicitly opts in for legacy cases.
    if env_bool("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS", False):
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


# request-id 中间件：为每个 HTTP 请求生成唯一 ID，注入响应头、请求状态和结构化日志
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


async def _api_key_auth(request: Request, call_next):
    token = _extract_api_token(request)
    web_session_valid = valid_web_session(request)
    if _requires_api_auth(request):
        expected = os.getenv("MINI_DROP_API_KEY", "")
        if not expected:
            return JSONResponse(
                status_code=500,
                content={"detail": "API auth enabled but MINI_DROP_API_KEY is empty"},
            )
        if not web_session_valid and (not token or not secrets.compare_digest(token, expected)):
            return JSONResponse(status_code=401, content={"detail": "无效 API Key"})
    request.state.principal_id = _principal_for_request(token or ("web-session" if web_session_valid else None))
    request.state.principal_roles = _roles_for_request()
    return await call_next(request)


def _task_view(record) -> TaskView:
    """Compatibility alias for legacy routes during the C1 migration."""

    return task_view(record)


def _requires_api_auth(request: Request) -> bool:
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    path = request.url.path
    return path.startswith("/api/") and path not in {
        "/api/healthz", "/api/livez", "/api/readyz", "/api/metrics",
        "/api/auth/set-cookie", "/api/auth/bootstrap", "/api/auth/clear-cookie",
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


def run_ai_validation() -> APIResponse:
    """Run the complete provider + Drop AI validation suite on demand."""
    try:
        result = run_ai_validation_suite()
    except AIValidationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=result)



def create_app() -> FastAPI:
    """Assemble a fresh HTTP application without starting external services."""

    container = _build_container()
    install_compatibility_default(container.application_services)
    application = FastAPI(
        title="Mini-Drop Server",
        version="0.1.0",
        lifespan=_bound_lifespan,
    )
    application.state.container = container
    for router in (
        health_router,
        common_router,
        agents_process_router,
        diagnoses_router,
        cases_router,
        plans_control_router,
        fanout_router,
        actuation_router,
        nlp_router,
        tasks_router,
        knowledge_memory_router,
        topology_discovery_router,
        v6_router,
    ):
        application.include_router(
            router,
            dependencies=[Depends(bind_request_application_services)],
        )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv(
            "MINI_DROP_CORS_ORIGINS",
            "http://localhost:5173",
        ).split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.middleware("http")(_request_id)
    application.middleware("http")(_access_log)
    application.middleware("http")(_api_key_auth)
    application.add_api_route("/api/ai-config", ai_config, methods=["GET"])
    application.add_api_route(
        "/api/ai-validation/runs",
        run_ai_validation,
        methods=["POST"],
    )
    return application


app = create_app()

# ── 启动入口 ──────────────────────────────────────────────────
