"""
Mini-Drop HTTP API 入口。

启动 FastAPI 服务（端口 8191），同时在后台线程运行 gRPC server（端口 50051）。
两者共享同一个 SqlRepository 实例——Agent 通过 gRPC 上报的数据，
Web 通过 HTTP API 即时可见。
"""

from __future__ import annotations

import server.app._env  # noqa: F401 — 自动加载 .env

import hashlib
import io
import os
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path as _Path
from urllib.parse import quote as _url_quote

from fastapi import FastAPI, HTTPException, Request
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

from server.app.common_utils import status_value
from server.app.artifact_service import (
    evidence_artifact_links,
    inspect_artifact,
    read_artifact_bytes,
)
from server.app.ai_provider import get_ai_settings, model_audit_scope
from server.app.ai_validation import AIValidationBusy, run_ai_validation_suite
from server.app.database import init_db, new_session
from server.app.event_bus import BUS, notify_diagnosis_complete
from server.app.flamegraph_parser import extract_top_functions_from_svg
from server.app.prometheus_metrics import (
    REGISTRY,
    record_diagnosis,
    record_http_request,
    record_source_access,
)
from server.app.grpc_server import serve_in_background
from server.app.logging_utils import log_event
from server.app.nlp.intent_parser import parse_intent
from server.app.nlp.process_resolver import resolve_pid
from server.app.nlp.summarizer import summarize, suggest_followup
from server.app.diagnosis import DiagnosisOrchestrator
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
    DEFAULT_SOURCE_REGISTRY,
    evaluate_source_access,
)
from server.app.diagnosis.probe_registry import list_probes as list_registered_probes
from server.app.diagnosis.source_gateway import SourceGateway, SourceGatewayError, SourceQueryRequest
from server.app.diagnosis.investigation_planner import (
    InvestigationActionCandidate,
    evaluate_investigation_stop,
    rank_investigation_actions,
)
from server.app.diagnosis.proposal_card import build_proposal_cards
from server.app.diagnosis.schemas import ApprovalRequest, CreateDiagnosisRequest
from server.app.case_collaboration import (
    CaseCorrectionRequest,
    CaseMessageRequest,
    CaseState,
    CaseTransitionRequest,
    CreateCaseRequest,
    CreateChangeRequest,
    CreateRecoveryPlanRequest,
    CreateTargetSessionRequest,
    CreateTargetSignalRequest,
    IndexProfileTaskRequest,
    RecoveryPlanDecisionRequest,
    RecoveryPlanExecuteRequest,
    TargetSessionTransitionRequest,
    StartCaseDiagnosisRequest,
    build_case_diagnosis_query,
    build_case_context_packet,
    serialize_time_range,
)
from server.app.rca.report import run_diagnosis_context
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

configure_tracing("mini-drop-server")
repo = SqlRepository()
diagnosis_orchestrator = DiagnosisOrchestrator(repo)
source_gateway = SourceGateway(repo, diagnosis_orchestrator)


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
    try:
        yield
    finally:
        _offline_task.cancel()
        try:
            await _offline_task
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
        await asyncio.to_thread(_run_offline_sweep_pass, timeout_sec, stale_task_timeout_sec)
        await asyncio.sleep(interval_sec)


def _run_offline_sweep_pass(timeout_sec: int, stale_task_timeout_sec: int) -> None:
    repo.mark_offline_agents(timeout_sec=timeout_sec)
    repo.recover_stale_tasks(timeout_sec=stale_task_timeout_sec)
    if hasattr(repo, "persist_agent_metric_snapshots"):
        repo.persist_agent_metric_snapshots()
    diagnosis_orchestrator.advance_active()


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
    rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
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


# ── 通用 ──────────────────────────────────────────────────────


@app.get("/api/events/stream")
async def sse_stream(request: Request, since: str = ""):
    """Server-Sent Events 实时推送。

    客户端通过 EventSource 连接此端点，接收任务状态变更、
    Agent 上下线、诊断完成等实时事件。

    用法：const es = new EventSource('/api/events/stream');
          es.onmessage = (e) => console.log(JSON.parse(e.data));
    """
    from fastapi.responses import StreamingResponse

    async def event_generator():
        queue = BUS.subscribe()
        try:
            # 仅断线续传时回放游标之后的事件。首次连接没有游标，
            # 不应把整个进程历史重新弹成前端通知。
            if since:
                for event in BUS.get_history(since):
                    yield (
                        f"id: {event['id']}\n"
                        f"event: {event['event']}\n"
                        f"data: {_json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                    )

            # 持续推送新事件
            while True:
                try:
                    event = await asyncio.to_thread(queue.get, True, 30.0)
                    yield (
                        f"id: {event['id']}\n"
                        f"event: {event['event']}\n"
                        f"data: {_json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                    )
                except _queue.Empty:
                    # 每 30 秒发一个注释行保活
                    yield ":keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            BUS.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 禁用缓冲
        },
    )


@app.get("/api/metrics")
def prometheus_metrics() -> Any:
    """Prometheus 指标端点。

    返回 text/plain 格式的指标数据，可被 Prometheus server 抓取。
    无需鉴权（抓取时 Prometheus 通常不带自定义 header）。
    """
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=REGISTRY.generate(), media_type="text/plain; charset=utf-8")


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
        checks["database"] = {"status": "unavailable", "error": str(exc)[:200]}

    # 对象存储连通性检查
    try:
        store.ensure_bucket(os.getenv("MINIO_BUCKET", "mini-drop"))
        checks["storage"] = {"status": "ok"}
    except Exception as exc:
        checks["storage"] = {"status": "unavailable", "error": str(exc)[:200]}

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
        checks["analyzer"] = {
            "status": "unavailable" if analyzer_required else "disabled",
            "error": str(exc)[:200],
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
def readyz(response: Response) -> APIResponse:
    """Dependency-aware readiness probe with a conventional 503 failure."""

    report = healthz(core_only=False)
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


@app.get("/api/me")
def current_user() -> APIResponse:
    return APIResponse(data={
        "user_id": "demo_user",
        "name": "Mini-Drop Demo User",
        "role": "admin",
    })


@app.post("/api/auth/set-cookie")
def auth_set_cookie(request: Request, body: dict) -> APIResponse:
    """通过 HttpOnly cookie 设置 API Key（比 localStorage 更安全）。

    POST /api/auth/set-cookie
    {"api_key": "sk-..."}

    浏览器将自动在后续请求中携带该 cookie，
    JavaScript 无法通过 document.cookie 读取（HttpOnly）。
    """
    from fastapi.responses import JSONResponse as _JsonResp
    api_key = (body or {}).get("api_key", "").strip()
    if not api_key:
        return APIResponse(code=400, message="api_key 不能为空")
    resp = _JsonResp(content={"code": 0, "message": "ok", "data": None})
    resp.set_cookie(
        key="mini_drop_api_key",
        value=api_key,
        httponly=True,
        samesite="lax",
        secure=False,  # 开发环境 HTTP；生产环境应设为 True 配合 HTTPS
        max_age=7 * 24 * 3600,  # 7 天
        path="/api",
    )
    return resp


@app.post("/api/auth/clear-cookie")
def auth_clear_cookie() -> APIResponse:
    """清除 HttpOnly cookie。"""
    from fastapi.responses import JSONResponse as _JsonResp
    resp = _JsonResp(content={"code": 0, "message": "ok", "data": None})
    resp.delete_cookie(key="mini_drop_api_key", path="/api")
    return resp


# ── Agent（查询面） ────────────────────────────────────────────


@app.get("/api/agents")
def list_agents(
    limit: int = 1000,
    offset: int = 0,
) -> APIResponse:
    """返回 Agent 列表。支持分页。

    调用前自动检查离线。可通过 ?limit=50&offset=0 分页。
    """
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    repo.mark_offline_agents()
    all_items = []
    for agent in repo.agents.values():
        item = repo.as_dict(agent)
        item["latest_metrics"] = getattr(repo, "agent_metrics", {}).get(agent.id, {})
        all_items.append(item)
    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


# ── 进程发现（选择诊断目标用） ────────────────────────────────────


@app.post("/api/agents/{agent_id}/processes/scan")
def scan_agent_processes(
    agent_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """在目标 Worker 上扫描进程，返回可选的诊断目标候选。

    这是把"填 PID"变成"选进程"的关键能力：值班工程师通常只知道
    服务名/进程名，不知道 PID。该接口在 Agent 上执行一次 R0 级
    只读 /proc 扫描（process_scan 采集器），返回匹配进程列表。
    """
    agent = repo.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if agent.status != "ONLINE":
        raise HTTPException(status_code=409, detail=f"Agent {agent_id} 不在线，无法扫描")
    capabilities = set(agent.capabilities or [])
    if "process_scan" not in capabilities:
        raise HTTPException(status_code=409, detail=f"Agent {agent_id} 未注册 process_scan 能力")

    query = str(payload.get("query") or "").strip()
    timeout_sec = max(5, min(int(payload.get("timeout_sec") or 15), 30))
    max_results = max(1, min(int(payload.get("max_results") or 300), 1000))

    scan_name = f"scan:{query or 'all'}:{agent.hostname or agent_id}"
    scan_name = scan_name[:120]
    request_id = getattr(request.state, "request_id", "") or None
    try:
        task = repo.create_task(
            CreateTaskRequest(
                name=scan_name,
                agent_id=agent_id,
                target_pid=1,  # 占位 PID（init），process_scan 采集器扫描全机时忽略
                collector_type="process_scan",
                sample_rate=1,
                duration_sec=2,
                options={
                    "query": query,
                    "max_results": max_results,
                    "source": "process_scan_api",
                },
            ),
            idempotency_key=f"scan-{agent_id}-{query}-{int(time.time() // 2)}",
            request_id=request_id,
            traceparent=getattr(request.state, "traceparent", "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 等待任务完成（心跳领取 + 扫描本身约需 2-8 秒）
    deadline = time.time() + timeout_sec
    last_status = "PENDING"
    while time.time() < deadline:
        task_view = repo.tasks.get(task.id)
        if task_view is None:
            raise HTTPException(status_code=500, detail="扫描任务丢失")
        last_status = status_value(task_view.status)
        if last_status in ("DONE", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)

    if last_status != "DONE":
        return APIResponse(data={
            "task_id": task.id,
            "status": last_status,
            "processes": [],
            "message": "扫描尚未完成，请稍后重试",
        })

    processes = _read_scan_artifact(task.id)
    return APIResponse(data={
        "task_id": task.id,
        "status": "DONE",
        "processes": processes,
        "message": f"找到 {len(processes)} 个候选进程",
    })


def _read_scan_artifact(task_id: str) -> list[dict[str, Any]]:
    """读取 process_scan 任务的进程清单产物。"""
    for artifact in repo.artifacts.get(task_id, []):
        if artifact.get("artifact_type") != "process_scan":
            continue
        path = _resolve_artifact_path_or_none(artifact.get("local_path"))
        if path is None and artifact.get("object_key"):
            text = _read_artifact_object_text(artifact)
            try:
                return _json.loads(text).get("processes", [])
            except (TypeError, ValueError):
                return []
        if path is not None:
            try:
                return _json.loads(path.read_text(encoding="utf-8")).get("processes", [])
            except (TypeError, ValueError):
                return []
    return []


@app.get("/api/audit-logs")
def list_audit_logs(
    limit: int = 1000,
    offset: int = 0,
) -> APIResponse:
    """返回审计日志列表。支持分页。"""
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    all_items = [repo.as_dict(log) for log in repo.audit_logs]
    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


# ── 任务 ──────────────────────────────────────────────────────


@app.get("/api/task-kinds")
def get_task_kinds(agent_id: str = "") -> APIResponse:
    """Return metadata used to build task forms.

    When ``agent_id`` is supplied, unsupported collectors are filtered out at
    the source.  The create-task endpoint still performs authoritative
    validation, so this filtering is only a usability aid.
    """

    capabilities: set[str] | None = None
    if agent_id:
        agent = repo.agents.get(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        capabilities = set(agent.capabilities or [])
    return APIResponse(data={
        "schema_version": "1.0",
        "items": list_task_kinds(capabilities),
    })


def _validate_task_agent_capability(agent_id: str, collector_type: str) -> None:
    agent = repo.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    capabilities = set(agent.capabilities or [])
    # Empty capability lists are retained for compatibility with older Agents
    # that registered before capability discovery was introduced.
    if capabilities and collector_type not in capabilities:
        raise HTTPException(
            status_code=409,
            detail=f"Agent {agent_id} 不支持采集器 {collector_type}",
        )


@app.post("/api/tasks")
def create_task(payload: CreateTaskRequest, request: Request) -> APIResponse:
    _validate_task_agent_capability(payload.agent_id, payload.collector_type)
    if payload.target_pid <= 0:
        raise HTTPException(status_code=400, detail="target_pid 必须为正整数")
    if payload.target_pid > 4194304:  # Linux pid_max 上限
        raise HTTPException(status_code=400, detail=f"target_pid 超出有效范围: {payload.target_pid}")
    if payload.duration_sec <= 0:
        raise HTTPException(status_code=400, detail="duration_sec 必须为正整数")
    if payload.duration_sec > MAX_TASK_DURATION_SEC:
        raise HTTPException(status_code=400, detail=f"duration_sec 不能超过 {MAX_TASK_DURATION_SEC}")
    if payload.sample_rate <= 0:
        raise HTTPException(status_code=400, detail="sample_rate 必须为正整数")
    if payload.sample_rate > MAX_SAMPLE_RATE:
        raise HTTPException(status_code=400, detail=f"sample_rate 不能超过 {MAX_SAMPLE_RATE}")
    normalized_name = normalize_task_name(
        payload.name,
        collector_type=payload.collector_type,
        agent_id=payload.agent_id,
        target_pid=payload.target_pid,
    )
    if normalized_name != payload.name:
        payload = payload.model_copy(update={"name": normalized_name})
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 字符")
    try:
        task = repo.create_task(
            payload,
            idempotency_key=idempotency_key or None,
            request_id=getattr(request.state, "request_id", "") or None,
            traceparent=getattr(request.state, "traceparent", "") or None,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail.startswith("IDEMPOTENCY_CONFLICT"):
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=404, detail=detail) from exc
    return APIResponse(data={"task_id": task.id, "status": status_value(task.status)})


@app.get("/api/tasks")
def list_tasks(
    limit: int = 1000,
    offset: int = 0,
    search: str = "",
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> APIResponse:
    """返回任务列表。支持分页、搜索、排序。

    可通过 ?limit=50&offset=0&search=perf&sort_by=name&sort_order=asc 过滤。
    """
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)

    all_items = [_task_view(t).model_dump() for t in repo.tasks.values()]

    # 搜索：按任务名称模糊匹配
    if search:
        q = search.lower()
        all_items = [t for t in all_items if q in (t.get("name") or "").lower() or q in (t.get("id") or "").lower()]

    # 排序
    sort_keys = {"name", "status", "created_at", "agent_id", "collector_type", "target_pid"}
    by = sort_by if sort_by in sort_keys else "created_at"
    reverse = sort_order.lower() == "desc"
    all_items.sort(key=lambda x: x.get(by, "") or "", reverse=reverse)

    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> APIResponse:
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=_task_view(task).model_dump())


@app.get("/api/tasks/{task_id}/attempts")
def get_task_attempts(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_task_attempts(task_id))


@app.get("/api/tasks/{task_id}/analysis-jobs")
def get_task_analysis_jobs(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_task_analysis_jobs(task_id))


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> APIResponse:
    """删除任务及其关联的事件、产物和诊断结果。"""
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 终态保护：RUNNING/ANALYZING 不允许删除
    active_statuses = {"PENDING", "RUNNING", "UPLOADING", "ANALYZING"}
    if status_value(task.status) in active_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"任务状态为 {status_value(task.status)}，请等待任务完成或失败后再删除",
        )
    deleted = repo.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data={"task_id": task_id, "deleted": True})


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, payload: CancelTaskRequest) -> APIResponse:
    """Cancel an active task. Repeating the same request is safe."""

    try:
        task = repo.cancel_task(task_id, payload.reason.strip(), Actor.WEB)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "不存在" in detail else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return APIResponse(data={
        "task_id": task.id,
        "status": status_value(task.status),
        "cancelled": status_value(task.status) == TaskStatus.CANCELLED.value,
    })


@app.post("/api/tasks/{task_id}/retry")
def retry_task(
    task_id: str,
    payload: RetryTaskRequest,
    request: Request,
) -> APIResponse:
    """Create a new task from a terminal task without mutating its history."""

    original = repo.tasks.get(task_id)
    if original is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if status_value(original.status) not in {
        TaskStatus.DONE.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    }:
        raise HTTPException(status_code=409, detail="仅终态任务可以重试")
    options = dict((original.request_params or {}).get("options") or {})
    options.pop("diagnosis_step_id", None)
    options.update({"source": "task_retry", "retry_of": original.id})
    retry_payload = CreateTaskRequest(
        name=payload.name or f"重试: {original.name}",
        agent_id=original.agent_id,
        target_pid=original.target_pid,
        collector_type=original.collector_type,
        sample_rate=original.sample_rate,
        duration_sec=original.duration_sec,
        options=options,
    )
    _validate_task_agent_capability(
        retry_payload.agent_id,
        retry_payload.collector_type,
    )
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 字符")
    try:
        retried = repo.create_task(
            retry_payload,
            idempotency_key=idempotency_key or None,
            request_id=getattr(request.state, "request_id", "") or None,
            traceparent=getattr(request.state, "traceparent", "") or None,
        )
        repo.record_task_retry(original.id, retried.id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if detail.startswith("IDEMPOTENCY_CONFLICT") else 404
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return APIResponse(data={
        "task_id": retried.id,
        "status": status_value(retried.status),
        "retry_of": original.id,
    })


@app.get("/api/tasks/{task_id}/events")
def get_task_events(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    return APIResponse(data=items)


@app.get("/api/tasks/{task_id}/artifacts")
def get_task_artifacts(task_id: str, verify: bool = True) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=[
        inspect_artifact(
            task_id,
            artifact,
            check_availability=verify,
            verify_hash=False,
        )
        for artifact in repo.artifacts.get(task_id, [])
    ])


@app.get("/api/tasks/{task_id}/artifacts/{artifact_type}/content")
def get_task_artifact_content(task_id: str, artifact_type: str, index: Optional[int] = None) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    for artifact in repo.artifacts.get(task_id, []):
        if artifact.get("artifact_type") != artifact_type:
            continue
        if index is not None and artifact.get("metadata", {}).get("window_index") != index:
            continue
        local_path = artifact.get("local_path")
        path = _resolve_artifact_path_or_none(local_path)
        if path is None and artifact.get("object_key"):
            text = _read_artifact_object_text(artifact)
            if artifact_type.endswith("_json") or artifact.get("content_type") == "application/json":
                return APIResponse(data=_json.loads(text))
            return APIResponse(data={"text": text})
        if path is None:
            raise HTTPException(status_code=404, detail="本地产物不存在")
        if artifact_type.endswith("_json") or artifact.get("content_type") == "application/json":
            return APIResponse(data=_json.loads(path.read_text(encoding="utf-8")))
        return APIResponse(data={"text": path.read_text(encoding="utf-8", errors="replace")})
    raise HTTPException(status_code=404, detail="产物不存在")


@app.get("/api/tasks/{task_id}/artifacts/{artifact_type}/download")
def download_task_artifact(task_id: str, artifact_type: str, index: Optional[int] = None):
    """经 Server 流式下载产物，使浏览器无需直接访问 MinIO 9000 端口。"""
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    for artifact in repo.artifacts.get(task_id, []):
        if artifact.get("artifact_type") != artifact_type:
            continue
        if index is not None and artifact.get("metadata", {}).get("window_index") != index:
            continue

        filename = _safe_download_filename(
            artifact.get("filename") or artifact.get("object_key") or f"{artifact_type}.bin"
        )
        media_type = artifact.get("content_type") or "application/octet-stream"
        path = _resolve_artifact_path_or_none(artifact.get("local_path"))
        if path is not None:
            expected_size = artifact.get("size_bytes") or 0
            if expected_size and path.stat().st_size != expected_size:
                raise HTTPException(status_code=409, detail="产物完整性检查失败：文件大小与登记值不一致")
            return FileResponse(path, media_type=media_type, filename=filename)

        bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
        key = _validate_presign_request(bucket, artifact.get("object_key", ""))
        stored_size = store.object_size(bucket, key)
        if stored_size is None:
            raise HTTPException(status_code=404, detail="产物文件已不存在，请下载结构化证据 JSON")
        expected_size = artifact.get("size_bytes") or 0
        if expected_size and stored_size != expected_size:
            raise HTTPException(status_code=409, detail="产物完整性检查失败：对象大小与登记值不一致")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(stored_size),
        }
        return StreamingResponse(
            store.stream_object(bucket, key),
            media_type=media_type,
            headers=headers,
        )
    raise HTTPException(status_code=404, detail="产物不存在")


@app.get("/api/storage/presign")
def presign_url(bucket: str = "mini-drop", key: str = "", expires: int = 3600) -> APIResponse:
    """生成 MinIO 预签名下载 URL。"""
    key = _validate_presign_request(bucket, key)
    try:
        url = store.presigned_get_url(bucket, key, expires)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(data={"url": url, "expires_sec": expires})


@app.post("/api/tasks/{task_id}/diagnose")
def diagnose_task(task_id: str) -> APIResponse:
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 收集已有 artifacts 中的结构化数据
    artifacts = repo.artifacts.get(task_id, [])
    top_functions = _extract_top_functions(artifacts)
    ebpf_metrics = _extract_artifact_json(artifacts, "ebpf_metrics")
    sys_metrics = _extract_artifact_json(artifacts, "sys_metrics")

    task_events = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    agent_record = repo.agents.get(task.agent_id)
    model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    diagnosis_id = repo.create_diagnosis_run(task_id, model_name)

    outcome = run_diagnosis_context(
        task_id=task_id,
        task_record=task,
        top_functions=top_functions,
        ebpf_metrics=ebpf_metrics,
        sys_metrics=sys_metrics,
        failure_events=[event.get("reason", "") for event in task_events if event.get("reason")],
        feedback_priors=repo.get_feedback_priors(),
        task_events=task_events,
        agent_record=agent_record,
        repo=repo,
    )
    report = outcome.report
    ranked_causes = [c.model_dump() for c in report.report.ranked_causes]
    confidence = ranked_causes[0]["confidence"] if ranked_causes else 0.0

    for tool_result in outcome.tool_results:
        repo.add_diagnosis_tool_result(
            diagnosis_id=diagnosis_id,
            tool_name=tool_result.tool_name,
            status=tool_result.status,
            evidence_ref=tool_result.evidence_ref,
            input_json=tool_result.input,
            output_json=tool_result.output,
            error_message=tool_result.error_message,
        )

    report_id = repo.add_diagnosis_report(
        diagnosis_id=diagnosis_id,
        report_json=report.report.model_dump(),
        ranked_causes=ranked_causes,
        confidence=confidence,
        not_enough_evidence=report.report.not_enough_evidence,
    )

    repair_plan_data = None
    if outcome.repair_plan is not None:
        repair_plan_data = outcome.repair_plan.model_dump()
        repo.add_repair_plan(
            diagnosis_id=diagnosis_id,
            plan_id=outcome.repair_plan.plan_id,
            cause_id=outcome.repair_plan.cause_id,
            risk_level=outcome.repair_plan.risk_level,
            actions=[action.model_dump() for action in outcome.repair_plan.actions],
            executed_actions=[
                action.model_dump() for action in outcome.repair_plan.actions
                if action.status == "executed"
            ],
            requires_user_confirm=outcome.repair_plan.requires_user_confirm,
            status=outcome.repair_plan.status,
        )

    diag_status = "DONE" if report.validated else "FAILED"
    repo.finish_diagnosis_run(
        diagnosis_id=diagnosis_id,
        status=diag_status,
        summary=report.report.summary,
        validated=report.validated,
        retry_count=report.retry_count,
    )
    record_diagnosis(diag_status)

    notify_diagnosis_complete(task_id, diagnosis_id, diag_status)

    return APIResponse(data={
        "diagnosis_id": diagnosis_id,
        "report_id": report_id,
        "task_id": task_id,
        "model": report.model_name,
        "validated": report.validated,
        "summary": report.report.summary,
        "ranked_causes": ranked_causes,
        "facts": report.report.facts,
        "not_enough_evidence": report.report.not_enough_evidence,
        "tool_results": [item.model_dump() for item in outcome.tool_results],
        "repair_plan": repair_plan_data,
    })


@app.get("/api/tasks/{task_id}/diagnoses")
def list_task_diagnoses(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_diagnoses_for_task(task_id))


@app.get("/api/diagnoses")
def list_diagnosis_history(limit: int = 500, offset: int = 0) -> APIResponse:
    """Return legacy RCA history without one browser request per task."""

    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    items, total = repo.list_diagnosis_history(limit=limit, offset=offset)
    return APIResponse(data={
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    })


@app.get("/api/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: str) -> APIResponse:
    item = repo.get_diagnosis(diagnosis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="诊断不存在")
    return APIResponse(data=item)


@app.post("/api/diagnoses/{diagnosis_id}/feedback")
def submit_diagnosis_feedback(diagnosis_id: str, payload: RCAFeedbackRequest) -> APIResponse:
    item = repo.get_diagnosis(diagnosis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="诊断不存在")
    task_id = item["run"]["task_id"]
    repo.record_rca_feedback(
        diagnosis_id=diagnosis_id,
        task_id=task_id,
        predicted_cause_id=payload.predicted_cause_id,
        feedback_label=payload.feedback_label,
        corrected_cause_id=payload.corrected_cause_id,
        feedback_note=payload.feedback_note,
    )
    return APIResponse(data={"diagnosis_id": diagnosis_id, "feedback_saved": True})


# ── AI 集群诊断会话（v1）──────────────────────────────────────


@app.post("/api/v1/diagnoses")
def create_diagnosis_session(payload: CreateDiagnosisRequest) -> APIResponse:
    """创建独立诊断会话，并只编排注册表中的受控探针。"""
    try:
        data = diagnosis_orchestrator.create(payload, creator_id="demo_user")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(data=data)


@app.get("/api/v1/diagnoses")
def list_diagnosis_sessions(limit: int = 100, offset: int = 0) -> APIResponse:
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    items = diagnosis_orchestrator.list(limit=limit, offset=offset)
    return APIResponse(data={
        "items": items,
        "total": diagnosis_orchestrator.store.count_sessions(),
        "offset": offset,
        "limit": limit,
    })


@app.get("/api/v1/diagnoses/{diagnosis_id}")
def get_diagnosis_session(diagnosis_id: str) -> APIResponse:
    data = diagnosis_orchestrator.get(diagnosis_id, advance=True)
    if data is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    artifacts_by_task = repo.artifacts
    data = {
        **data,
        "evidence": [
            {
                **item,
                "artifact_links": evidence_artifact_links(
                    item,
                    artifacts_by_task,
                    verify=False,
                ),
            }
            for item in data.get("evidence", [])
        ],
    }
    return APIResponse(data=data)


def _find_diagnosis_evidence(diagnosis_id: str, evidence_id: str) -> dict:
    evidence = next(
        (
            item
            for item in diagnosis_orchestrator.store.list_evidence(diagnosis_id)
            if item.get("evidence_id") == evidence_id
        ),
        None,
    )
    if evidence is None:
        raise HTTPException(status_code=404, detail="诊断证据不存在")
    return evidence


@app.get("/api/v1/diagnoses/{diagnosis_id}/evidence/{evidence_id}/download")
def download_diagnosis_evidence(diagnosis_id: str, evidence_id: str) -> Response:
    """Download the persisted structured evidence even if its raw artifact expired."""

    evidence = _find_diagnosis_evidence(diagnosis_id, evidence_id)
    content = _json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2,
        default=str,
    ).encode("utf-8")
    filename = _safe_download_filename(f"evidence-{evidence_id}.json")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/v1/diagnoses/{diagnosis_id}/evidence/{evidence_id}/bundle")
def download_diagnosis_evidence_bundle(diagnosis_id: str, evidence_id: str) -> Response:
    """Build a self-describing ZIP containing evidence, manifest, and available files."""

    evidence = _find_diagnosis_evidence(diagnosis_id, evidence_id)
    artifact_links = evidence_artifact_links(evidence, repo.artifacts, verify=False)
    manifest = {
        "schema_version": "1.0",
        "diagnosis_id": diagnosis_id,
        "evidence_id": evidence_id,
        "artifact_count": len(artifact_links),
        "included_artifact_count": 0,
        "artifacts": [],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "evidence.json",
            _json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        )
        for artifact in artifact_links:
            inspected = inspect_artifact(
                artifact["task_id"],
                artifact,
                check_availability=True,
                verify_hash=False,
            )
            record = {
                key: inspected.get(key)
                for key in (
                    "artifact_id",
                    "task_id",
                    "artifact_type",
                    "filename",
                    "object_key",
                    "content_type",
                    "size_bytes",
                    "sha256",
                    "actual_size_bytes",
                    "availability",
                    "availability_reason",
                    "retention_state",
                    "expires_at",
                    "integrity_status",
                )
            }
            if inspected["availability"] == "available":
                try:
                    content = read_artifact_bytes(inspected)
                    actual_hash = hashlib.sha256(content).hexdigest()
                    record["actual_sha256"] = actual_hash
                    expected_hash = inspected.get("sha256")
                    record["integrity_status"] = (
                        "verified"
                        if expected_hash and actual_hash == expected_hash
                        else "mismatch"
                        if expected_hash
                        else "hash_unavailable"
                    )
                    safe_name = _safe_download_filename(
                        inspected.get("filename")
                        or inspected.get("object_key")
                        or f"{inspected['artifact_type']}.bin"
                    )
                    archive.writestr(
                        f"artifacts/{inspected['artifact_id']}/{safe_name}",
                        content,
                    )
                    manifest["included_artifact_count"] += 1
                except (FileNotFoundError, OSError, ValueError):
                    record["availability"] = "missing"
                    record["availability_reason"] = "打包时文件不可读"
            manifest["artifacts"].append(record)
        archive.writestr(
            "manifest.json",
            _json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
    filename = _safe_download_filename(f"evidence-{evidence_id}-bundle.zip")
    return Response(
        content=output.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/storage/reconciliation")
def reconcile_artifact_storage(limit: int = 1000, verify_hash: bool = False) -> APIResponse:
    """Compare artifact metadata with the files currently present in storage."""

    limit = min(max(limit, 1), 5000)
    items: list[dict] = []
    for task_id, artifacts in repo.artifacts.items():
        for artifact in artifacts:
            if len(items) >= limit:
                break
            items.append(inspect_artifact(
                task_id,
                artifact,
                check_availability=True,
                verify_hash=verify_hash,
            ))
        if len(items) >= limit:
            break
    summary = {
        "scanned": len(items),
        "available": sum(item["availability"] == "available" for item in items),
        "missing": sum(item["availability"] == "missing" for item in items),
        "unavailable": sum(item["availability"] == "unavailable" for item in items),
        "integrity_mismatch": sum(item["integrity_status"] == "mismatch" for item in items),
        "retention_expired": sum(item["retention_state"] == "expired" for item in items),
        "verify_hash": verify_hash,
    }
    return APIResponse(data={"summary": summary, "items": items})


@app.post("/api/v1/diagnoses/{diagnosis_id}/cancel")
def cancel_diagnosis_session(diagnosis_id: str, body: Optional[dict] = None) -> APIResponse:
    """取消诊断会话：终态幂等；非终态收敛到 USER_CANCELED 并取消活跃子任务。"""
    reason = ((body or {}).get("reason") or "").strip() or "用户取消诊断"
    try:
        data = diagnosis_orchestrator.cancel(diagnosis_id, reason)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return APIResponse(data=data)


@app.post("/api/v1/diagnoses/{diagnosis_id}/approvals")
def approve_diagnosis_probe(diagnosis_id: str, payload: ApprovalRequest) -> APIResponse:
    try:
        data = diagnosis_orchestrator.approve(diagnosis_id, payload)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return APIResponse(data=data)


@app.get("/api/v1/probes")
def list_probe_definitions() -> APIResponse:
    return APIResponse(data=[probe.model_dump(mode="json") for probe in list_registered_probes()])


@app.get("/api/v1/sources")
def list_source_definitions() -> APIResponse:
    """List registered AI-readable sources without exposing credential references."""
    return APIResponse(data={
        "schema_version": "source-registry.v1",
        "items": [source.public_dict() for source in DEFAULT_SOURCE_REGISTRY.list()],
    })


@app.get("/api/v1/identity")
def get_current_identity(request: Request) -> APIResponse:
    return APIResponse(data={
        "principal_id": _request_principal(request),
        "tenant_id": _request_tenant(),
        "roles": sorted(getattr(request.state, "principal_roles", set())),
        "identity_source": (
            "configured_principal"
            if os.getenv("MINI_DROP_API_PRINCIPAL_ID", "").strip()
            else "api_key_fingerprint"
            if _extract_api_token(request)
            else "local_development"
        ),
    })


# ── AI Incident Case 协作层（v1）───────────────────────────────


@app.post("/api/v1/target-sessions")
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


@app.get("/api/v1/target-sessions")
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


@app.get("/api/v1/target-sessions/{target_session_id}")
def get_target_session(target_session_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    result = repo.get_target_session(target_session_id, _request_tenant())
    if result is None:
        raise HTTPException(status_code=404, detail="目标会话不存在")
    return APIResponse(data=result)


@app.post("/api/v1/target-sessions/{target_session_id}/transition")
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


@app.post("/api/v1/target-sessions/{target_session_id}/signals")
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


@app.get("/api/v1/target-sessions/{target_session_id}/signals")
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


@app.post("/api/v1/target-sessions/{target_session_id}/profile-windows/index-task")
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


@app.get("/api/v1/target-sessions/{target_session_id}/profile-windows")
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


@app.post("/api/v1/cases")
def create_incident_case(payload: CreateCaseRequest, request: Request) -> APIResponse:
    _require_role(request, "operator")
    target = None
    if payload.target_session_id:
        target = repo.get_target_session(payload.target_session_id, _request_tenant())
        if target is None:
            raise HTTPException(status_code=404, detail="TARGET_SESSION_NOT_FOUND")
        if target["status"] == "ARCHIVED":
            raise HTTPException(status_code=409, detail="TARGET_SESSION_ARCHIVED")
    trusted = {
        **payload.model_dump(mode="json", exclude={"time_range"}),
        "time_range": serialize_time_range(payload.time_range),
        "tenant_id": _request_tenant(),
        "created_by": _request_principal(request),
    }
    if target is not None:
        trusted["environment"] = target["environment"]
        trusted["target_scope"] = target["target_scope"]
    try:
        result = repo.create_incident_case(trusted)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.endswith("_NOT_FOUND") else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return APIResponse(data=result)


@app.post("/api/v1/changes")
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


@app.get("/api/v1/changes")
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


@app.get("/api/v1/cases")
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


@app.get("/api/v1/cases/{case_id}")
def get_incident_case(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    result = repo.get_incident_case(case_id, _request_tenant())
    if result is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=result)


@app.get("/api/v1/cases/{case_id}/events")
def list_incident_case_events(
    case_id: str,
    request: Request,
    limit: int = 200,
    after_id: int = 0,
) -> APIResponse:
    _require_role(request, "operator")
    items = repo.list_case_events(
        case_id,
        _request_tenant(),
        limit=min(max(limit, 1), 1000),
        after_id=max(after_id, 0),
    )
    if items is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={"items": items, "total": len(items)})


@app.get("/api/v1/cases/{case_id}/context-packets")
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


@app.get("/api/v1/cases/{case_id}/model-attempts")
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


@app.get("/api/v1/cases/{case_id}/hypotheses")
def get_case_hypotheses(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    graph = repo.get_case_hypothesis_graph(case_id, _request_tenant())
    if graph is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=graph)


@app.get("/api/v1/cases/{case_id}/iterations")
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


@app.post("/api/v1/cases/{case_id}/diagnoses")
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
        iteration_no=0,
        required_output_schema="normalized-diagnosis-intent.v1",
    )
    packet = repo.create_context_packet({
        "case_id": case_id,
        "tenant_id": tenant_id,
        "schema_version": "case-context.v1",
        "purpose": "diagnosis_intent",
        "iteration_no": 0,
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
            diagnosis = diagnosis_orchestrator.create(
                diagnosis_request,
                creator_id=principal_id,
                initial_task_ids=case.get("initial_task_ids") or [],
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
            "iteration_no": 0,
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


@app.get("/api/v1/cases/{case_id}/proposals")
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


@app.get("/api/v1/cases/{case_id}/understanding")
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


@app.post("/api/v1/cases/{case_id}/messages")
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


# ── 恢复验证与人工动作回填（多轮诊断闭环） ────────────────────


VERIFICATION_TASK_DURATION_SEC = 10


def _read_sys_metrics_artifact_keys(artifact_value: Any) -> dict[str, float]:
    """从 sys_metrics.v2 产物提取验证可对比的关键指标。"""
    if not isinstance(artifact_value, dict):
        return {}
    normalized = artifact_value.get("normalized")
    if not isinstance(normalized, dict):
        return {}
    result: dict[str, float] = {}
    process = normalized.get("process") or {}
    cpu = process.get("cpu") or {}
    mem = process.get("memory") or {}
    host = normalized.get("host") or {}
    host_cpu = host.get("cpu") or {}
    try:
        result["process_cpu_cores"] = float(cpu.get("normalized_core_usage", 0.0) or 0.0)
        result["iowait_ratio"] = float(host_cpu.get("iowait_ratio", 0.0) or 0.0)
        result["rss_bytes"] = float(mem.get("rss_bytes", 0.0) or 0.0)
    except (TypeError, ValueError):
        return {}
    return result


def _find_diagnosis_sys_metrics_task(repo_obj, diagnosis_id: str) -> Any | None:
    """在诊断会话的子任务中找到 sys_metrics 采集任务（baseline 来源）。"""
    for task in repo_obj.tasks.values():
        options = task.request_params.get("options") or {}
        if options.get("diagnosis_id") != diagnosis_id:
            continue
        if task.collector_type != "sys_metrics":
            continue
        if status_value(task.status) != "DONE":
            continue
        return task
    return None


def _judge_recovery(baseline: dict[str, float], current: dict[str, float]) -> dict[str, Any]:
    """按关键指标对比判定恢复状态（确定性，不读模型）。

    - recovered：全部关键指标显著回落（<50%）或本就正常；
    - degraded：任一指标明显恶化（>150%）；
    - partially_recovered：部分回落；
    - not_recovered / indeterminate：无显著变化或缺少对比。
    """
    keys = [
        key for key in ("process_cpu_cores", "iowait_ratio", "rss_bytes")
        if baseline.get(key) is not None and current.get(key) is not None
    ]
    if not keys:
        return {"status": "indeterminate", "reason": "缺少可对比的关键指标", "metrics": {}}
    metrics: dict[str, Any] = {}
    for key in keys:
        b = float(baseline[key])
        c = float(current[key])
        ratio = (c / b) if b > 0 else (0.0 if c <= 0.02 else 1.0)
        if b <= 0.02 and c <= 0.02:
            verdict = "normal"
        elif ratio < 0.5:
            verdict = "recovered"
        elif ratio > 1.5 and c > 0.02:
            verdict = "degraded"
        else:
            verdict = "unchanged"
        metrics[key] = {"baseline": round(b, 4), "current": round(c, 4), "ratio": round(ratio, 2), "verdict": verdict}
    verdicts = [item["verdict"] for item in metrics.values()]
    if "degraded" in verdicts:
        status = "degraded"
    elif verdicts and all(item in ("recovered", "normal") for item in verdicts):
        status = "recovered"
    elif "recovered" in verdicts:
        status = "partially_recovered"
    else:
        status = "not_recovered"
    return {"status": status, "reason": f"对比 {len(keys)} 项关键指标", "metrics": metrics}


@app.post("/api/v1/cases/{case_id}/verification")
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
    diagnosis_id = payload.get("diagnosis_id") or case.get("diagnosis_session_id")
    if not diagnosis_id:
        raise HTTPException(status_code=409, detail="Case 尚未关联诊断会话")
    diagnosis = diagnosis_orchestrator.get(diagnosis_id, advance=False)
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    conclusion = diagnosis.get("latest_conclusion") or {}
    instances = (diagnosis.get("target_scope") or {}).get("instances") or \
        (case.get("target_scope") or {}).get("instances") or []
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
            value = _extract_artifact_json(repo.artifacts, baseline_task.id, "sys_metrics")
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
    value = _extract_artifact_json(repo.artifacts, task.id, "sys_metrics")
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


@app.post("/api/v1/cases/{case_id}/manual-actions")
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


@app.post("/api/v1/cases/{case_id}/corrections")
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
    superseded_diagnosis_id = current.get("diagnosis_session_id")
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
    diagnosis_id = current.get("diagnosis_session_id")
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
    return APIResponse(data=result)


@app.post("/api/v1/cases/{case_id}/pause")
def pause_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "pause")


@app.post("/api/v1/cases/{case_id}/resume")
def resume_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "resume")


@app.post("/api/v1/cases/{case_id}/stop")
def stop_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "stop")


@app.post("/api/v1/cases/{case_id}/resolve")
def resolve_incident_case(
    case_id: str, payload: CaseTransitionRequest, request: Request,
) -> APIResponse:
    return _transition_case_from_api(case_id, payload, request, "resolve")


@app.post("/api/v1/sources/{source_id}/query")
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


@app.post("/api/v1/grants")
def create_authorization_grant(payload: CreateAuthorizationGrantRequest, request: Request) -> APIResponse:
    _require_role(request, "authorization_admin")
    if payload.tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="GRANT_TENANT_MISMATCH")
    selected = []
    for source_id in payload.source_ids:
        source = DEFAULT_SOURCE_REGISTRY.get(source_id)
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


@app.get("/api/v1/grants")
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


@app.delete("/api/v1/grants/{grant_id}")
def revoke_authorization_grant(grant_id: str, request: Request) -> APIResponse:
    _require_role(request, "authorization_admin")
    result = repo.revoke_authorization_grant(grant_id, _request_principal(request))
    if result is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    return APIResponse(data=result)


@app.post("/api/v1/policy/evaluate-source")
def evaluate_source_authorization(payload: AuthorizationEvaluationRequest, request: Request) -> APIResponse:
    _require_role(request, "authorization_admin")
    if payload.tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="SOURCE_TENANT_MISMATCH")
    grants = repo.list_authorization_grants(
        principal_id=payload.principal_id,
        tenant_id=payload.tenant_id,
        include_inactive=True,
    )
    result = evaluate_source_access(payload, grants)
    return APIResponse(data=result.model_dump(mode="json"))


@app.get("/api/v1/actions")
def list_registered_actions(request: Request) -> APIResponse:
    _require_role(request, "operator")
    items = [item.model_dump(mode="json") for item in DEFAULT_ACTION_REGISTRY.list()]
    return APIResponse(data={
        "schema_version": "action-registry.v1",
        "execution_enabled": any(item.get("implementation_status") == "executable" for item in items),
        "items": items,
    })


@app.post("/api/v1/actions/{action_id}/evaluate")
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


# ── 受控修复执行（Actuation Gateway 首个实例） ────────────────────


ACTUATION_GATEWAY = ActuationGateway(
    audit_callback=lambda detail: repo.record_audit(
        event_type=detail.pop("event_type", "ACTION_AUDIT"),
        message=detail.pop("message", ""),
        metadata=detail,
    ),
)


def _action_evaluation_allows(action_id: str, request: Request, payload: dict[str, Any]) -> None:
    """执行前必须通过确定性策略评估，不允许 DENIED。

    人工显式调用 execute 本身即满足 USER_APPROVAL / CHANGE_APPROVAL；
    但策略硬拒绝（环境不允许、目标超限、冗余不足、未注册）不可被绕过。
    """
    evaluation = evaluate_action(action_id, ActionEvaluationRequest(
        tenant_id=payload.get("tenant_id", _request_tenant()),
        environment=payload.get("environment", "production"),
        target_count=payload.get("target_count", 1),
        healthy_replicas_after_action=payload.get("healthy_replicas_after_action", 1),
        change_freeze=bool(payload.get("change_freeze", False)),
        rollback_ready=bool(payload.get("rollback_ready", True)),
        dry_run_passed=bool(payload.get("dry_run_passed", True)),
        parameters=payload.get("parameters", {}) or {},
    ))
    if evaluation.decision == AuthorizationDecision.DENIED:
        raise HTTPException(status_code=403, detail=f"ACTION_DENIED: {','.join(evaluation.reason_codes)}")


def _case_recovery_plan_or_404(case_id: str, tenant_id: str, plan_id: str) -> dict[str, Any]:
    plan = repo.get_case_recovery_plan(case_id, tenant_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="恢复方案不存在")
    return plan


@app.get("/api/v1/cases/{case_id}/recovery-plans")
def list_case_recovery_plans(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={
        "items": repo.list_case_recovery_plans(case_id, tenant_id),
    })


@app.post("/api/v1/cases/{case_id}/recovery-plans")
def create_case_recovery_plan(
    case_id: str,
    payload: CreateRecoveryPlanRequest,
    request: Request,
) -> APIResponse:
    """Create a durable, approval-gated recovery plan for an executable action."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    definition = DEFAULT_ACTION_REGISTRY.get(payload.action_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="ACTION_NOT_REGISTERED")
    if definition.implementation_status != "executable" or not is_executable(payload.action_id):
        raise HTTPException(status_code=409, detail="ACTION_POLICY_ONLY_NOT_EXECUTABLE")
    policy = evaluate_action(payload.action_id, ActionEvaluationRequest(
        tenant_id=tenant_id,
        environment=case["environment"],
        target_count=1,
        healthy_replicas_after_action=1,
        rollback_ready=bool(definition.rollback_action_id),
        dry_run_passed=False,
        parameters=payload.parameters,
    ))
    if policy.decision == AuthorizationDecision.DENIED:
        raise HTTPException(status_code=403, detail=f"ACTION_DENIED:{','.join(policy.reason_codes)}")
    try:
        plan = repo.create_case_recovery_plan(
            case_id,
            tenant_id,
            action_id=payload.action_id,
            parameters=payload.parameters,
            value_after_fix=payload.value_after_fix,
            verification_method=payload.verification_method,
            policy=policy.model_dump(mode="json"),
            created_by=_request_principal(request),
            expected_case_version=payload.expected_case_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=plan)


@app.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/dry-run")
def dry_run_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["status"] != "PROPOSED":
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_PROPOSED")
    try:
        dry = ACTUATION_GATEWAY.dry_run(plan["action_id"], plan["parameters"])
        definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
        policy = evaluate_action(plan["action_id"], ActionEvaluationRequest(
            tenant_id=tenant_id,
            environment=case["environment"],
            target_count=1,
            healthy_replicas_after_action=1,
            rollback_ready=bool(definition and definition.rollback_action_id),
            dry_run_passed=True,
            parameters=plan["parameters"],
        ))
        if policy.decision == AuthorizationDecision.DENIED:
            raise ActuationError(f"ACTION_DENIED:{','.join(policy.reason_codes)}")
        next_status = (
            "DRY_RUN_COMPLETED"
            if dry.get("dry_run", {}).get("candidate_count", 0) else "DRY_RUN_EMPTY"
        )
        updated = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status=next_status,
            actor_id=_request_principal(request),
            expected_plan_version=payload.expected_plan_version,
            updates={
                "policy_json": policy.model_dump(mode="json"),
                "dry_run_attempt_id": dry["attempt_id"],
                "dry_run_json": dry["dry_run"],
            },
        )
    except (ActuationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


@app.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/decision")
def decide_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanDecisionRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["status"] != "DRY_RUN_COMPLETED":
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_READY_FOR_DECISION")
    now = now_utc()
    updates = (
        {"approved_by": _request_principal(request), "approved_at": now}
        if payload.decision == "approve"
        else {"rejection_reason": payload.reason}
    )
    try:
        updated = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status="APPROVED" if payload.decision == "approve" else "REJECTED",
            actor_id=_request_principal(request),
            expected_plan_version=payload.expected_plan_version,
            updates=updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


@app.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/execute")
def execute_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["status"] not in {"APPROVED", "EXECUTING"} or not plan.get("approved_by"):
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_APPROVED")
    definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
    policy = evaluate_action(plan["action_id"], ActionEvaluationRequest(
        tenant_id=tenant_id,
        environment=case["environment"],
        target_count=1,
        healthy_replicas_after_action=1,
        rollback_ready=bool(definition and definition.rollback_action_id),
        dry_run_passed=True,
        parameters=plan["parameters"],
    ))
    if policy.decision == AuthorizationDecision.DENIED or not policy.executable:
        raise HTTPException(status_code=403, detail=f"ACTION_DENIED:{','.join(policy.reason_codes)}")
    try:
        if plan["status"] == "APPROVED":
            plan = repo.transition_case_recovery_plan(
                case_id, tenant_id, plan_id,
                to_status="EXECUTING",
                actor_id=_request_principal(request),
                expected_plan_version=payload.expected_plan_version,
            )
            if plan is None:
                raise ValueError("RECOVERY_PLAN_NOT_FOUND")
        elif plan["row_version"] != payload.expected_plan_version:
            raise ValueError("RECOVERY_PLAN_VERSION_CONFLICT")

        dry_items = (plan.get("dry_run") or {}).get("items") or []
        inferred = _infer_recovery_execution(plan)
        if dry_items and len(inferred) == len(dry_items):
            execution = {
                "attempt_id": str(plan["dry_run_attempt_id"]),
                "action_id": plan["action_id"],
                "stage": "COMPLETED",
                "executed": inferred,
                "reconciled_from_postconditions": True,
            }
        else:
            if ACTUATION_GATEWAY.get_attempt(str(plan["dry_run_attempt_id"])) is None:
                ACTUATION_GATEWAY.restore_dry_run_attempt(
                    attempt_id=str(plan["dry_run_attempt_id"]),
                    action_id=plan["action_id"],
                    items=dry_items,
                    parameters=plan["parameters"],
                )
            execution = ACTUATION_GATEWAY.execute(
                plan["action_id"],
                str(plan["dry_run_attempt_id"]),
                environment=case["environment"],
            )
            combined = {
                str(item.get("task_id") or item.get("source")): item
                for item in [*inferred, *(execution.get("executed") or [])]
            }
            execution["executed"] = list(combined.values())
        updated = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status="EXECUTED",
            actor_id=_request_principal(request),
            expected_plan_version=plan["row_version"],
            updates={"execution_json": execution, "policy_json": policy.model_dump(mode="json")},
        )
    except ActuationError as exc:
        current = repo.get_case_recovery_plan(case_id, tenant_id, plan_id)
        if current and current["status"] == "EXECUTING":
            attempt = ACTUATION_GATEWAY.get_attempt(str(current.get("dry_run_attempt_id")))
            failure = {
                "attempt_id": current.get("dry_run_attempt_id"),
                "stage": "FAILED",
                "executed": list(attempt.executed_items) if attempt else [],
                "error": str(exc),
            }
            try:
                repo.transition_case_recovery_plan(
                    case_id, tenant_id, plan_id,
                    to_status="FAILED",
                    actor_id=_request_principal(request),
                    expected_plan_version=current["row_version"],
                    updates={"execution_json": failure},
                )
            except ValueError:
                pass
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


def _infer_recovery_execution(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover an execution journal from deterministic filesystem postconditions."""
    dry_items = (plan.get("dry_run") or {}).get("items") or []
    if plan["action_id"] == "mini-drop.cleanup-expired-cache":
        destination_root = _Path(
            os.getenv("MINI_DROP_QUARANTINE_ROOT", "/tmp/mini-drop-quarantine"),
        ).expanduser().resolve()
        destination_field = "quarantine_path"
    elif plan["action_id"] == "mini-drop.restore-cache-quarantine":
        destination_root = _Path(
            os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop"),
        ).expanduser().resolve()
        destination_field = "restored_path"
    else:
        return []
    if not destination_root.is_dir():
        return []
    destinations = [item for item in destination_root.iterdir() if item.is_dir()]
    inferred: list[dict[str, Any]] = []
    for item in dry_items:
        source_value = str(item.get("path") or "").strip()
        if not source_value:
            continue
        source = _Path(source_value)
        task_id = str(item.get("task_id") or source.name)
        if source.exists():
            continue
        matches = [
            candidate for candidate in destinations
            if candidate.name == task_id or candidate.name.startswith(f"{task_id}-")
        ]
        if not matches:
            continue
        inferred.append({
            "task_id": task_id,
            "source": str(source),
            destination_field: str(sorted(matches)[-1]),
            "size_bytes": item.get("size_bytes", 0),
            "reconciled": True,
        })
    return inferred


def _rollback_case_recovery_plan(
    case_id: str,
    tenant_id: str,
    plan: dict[str, Any],
    *,
    actor_id: str,
) -> dict[str, Any]:
    definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
    rollback_id = definition.rollback_action_id if definition else None
    if not rollback_id or not is_executable(rollback_id):
        raise ActuationError(f"动作 {plan['action_id']} 没有可执行的回滚动作")
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise ActuationError("Case 不存在")
    dry = ACTUATION_GATEWAY.dry_run(rollback_id, plan.get("parameters") or {})
    if dry.get("dry_run", {}).get("candidate_count", 0):
        result = ACTUATION_GATEWAY.execute(
            rollback_id, dry["attempt_id"], environment=case["environment"],
        )
    else:
        if (plan.get("execution") or {}).get("executed"):
            raise ActuationError("ROLLBACK_TARGET_MISSING：已执行动作的回滚目标不存在")
        result = {"attempt_id": dry["attempt_id"], "stage": "NOTHING_TO_ROLLBACK", "executed": []}
    updated = repo.transition_case_recovery_plan(
        case_id, tenant_id, plan["recovery_plan_id"],
        to_status="ROLLED_BACK",
        actor_id=actor_id,
        expected_plan_version=plan["row_version"],
        updates={"rollback_json": result},
    )
    if updated is None:
        raise ActuationError("恢复方案不存在")
    return updated


@app.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/rollback")
def rollback_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["row_version"] != payload.expected_plan_version:
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_VERSION_CONFLICT")
    if plan["status"] not in {"EXECUTED", "VERIFICATION_FAILED", "FAILED"}:
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_ROLLBACKABLE")
    try:
        updated = _rollback_case_recovery_plan(
            case_id, tenant_id, plan, actor_id=_request_principal(request),
        )
    except (ActuationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


def _verify_local_recovery_postconditions(plan: dict[str, Any]) -> dict[str, Any]:
    """Verify executable maintenance actions without trusting client assertions."""
    executed = (plan.get("execution") or {}).get("executed") or []
    if not executed:
        return {"status": "indeterminate", "reason": "执行结果为空", "checks": []}
    checks: list[dict[str, Any]] = []
    if plan["action_id"] == "mini-drop.cleanup-expired-cache":
        for item in executed:
            source_value = str(item.get("source") or "").strip()
            quarantine_value = str(item.get("quarantine_path") or "").strip()
            source_absent = bool(source_value) and not _Path(source_value).exists()
            quarantine_present = bool(quarantine_value) and _Path(quarantine_value).is_dir()
            checks.append({
                "task_id": item.get("task_id"),
                "source_absent": source_absent,
                "quarantine_present": quarantine_present,
                "passed": source_absent and quarantine_present,
            })
    elif plan["action_id"] == "mini-drop.restore-cache-quarantine":
        for item in executed:
            source_value = str(item.get("source") or "").strip()
            restored_value = str(item.get("restored_path") or "").strip()
            source_absent = bool(source_value) and not _Path(source_value).exists()
            restored_present = bool(restored_value) and _Path(restored_value).is_dir()
            checks.append({
                "task_id": item.get("task_id"),
                "source_absent": source_absent,
                "restored_present": restored_present,
                "passed": source_absent and restored_present,
            })
    else:
        return {
            "status": "indeterminate",
            "reason": "该动作尚无注册的服务端验证器",
            "checks": [],
        }
    passed = bool(checks) and all(item["passed"] for item in checks)
    return {
        "status": "recovered" if passed else "not_recovered",
        "reason": f"服务端校验 {len(checks)} 项动作后置条件",
        "checks": checks,
    }


@app.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/verify")
def verify_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["row_version"] != payload.expected_plan_version:
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_VERSION_CONFLICT")
    if plan["status"] != "EXECUTED":
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_EXECUTED")
    judgment = _verify_local_recovery_postconditions(plan)
    try:
        transitioned = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status="VERIFIED" if judgment["status"] == "recovered" else "VERIFICATION_FAILED",
            actor_id=_request_principal(request),
            expected_plan_version=plan["row_version"],
            updates={"verification_json": judgment},
        )
        final_plan = transitioned
        if judgment["status"] != "recovered":
            definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
            if definition and definition.rollback_action_id and is_executable(definition.rollback_action_id):
                final_plan = _rollback_case_recovery_plan(
                    case_id, tenant_id, transitioned,
                    actor_id=_request_principal(request),
                )
    except (ActuationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={"judgment": judgment, "recovery_plan": final_plan})


@app.post("/api/v1/actions/{action_id}/dry-run")
def dry_run_registered_action(
    action_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """对注册动作执行只读预演，返回将影响的清单（不执行任何变更）。"""
    _require_role(request, "operator")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    if tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    try:
        result = ACTUATION_GATEWAY.dry_run(
            action_id,
            payload.get("parameters") or {},
        )
    except ActuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={
        **result,
        "tenant_id": tenant_id,
        "principal_id": _request_principal(request),
    })


@app.post("/api/v1/actions/{action_id}/execute")
def execute_registered_action(
    action_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """执行已通过 dry-run 与策略评估的修复动作（人工显式触发 = 批准）。"""
    _require_role(request, "operator")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    if tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    if not payload.get("dry_run_attempt_id"):
        raise HTTPException(status_code=400, detail="dry_run_attempt_id 必填：必须先 dry-run 再执行")
    _action_evaluation_allows(action_id, request, payload)
    try:
        result = ACTUATION_GATEWAY.execute(
            action_id,
            str(payload["dry_run_attempt_id"]),
            environment=str(payload.get("environment") or "production"),
        )
    except ActuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={**result, "tenant_id": tenant_id})


@app.post("/api/v1/actions/{action_id}/rollback")
def rollback_registered_action(
    action_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """回滚已执行的可逆动作（当前支持从隔离区恢复 Mini-Drop 缓存）。"""
    _require_role(request, "operator")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    if tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    definition = DEFAULT_ACTION_REGISTRY.get(action_id)
    rollback_id = definition.rollback_action_id if definition else None
    if not rollback_id or not is_executable(rollback_id):
        raise HTTPException(status_code=409, detail=f"动作 {action_id} 没有可执行的回滚动作")
    try:
        dry = ACTUATION_GATEWAY.dry_run(rollback_id, payload.get("parameters") or {})
        if not dry.get("dry_run", {}).get("candidate_count", 0):
            return APIResponse(data={"attempt_id": dry["attempt_id"], "stage": "NOTHING_TO_ROLLBACK", "executed": []})
        result = ACTUATION_GATEWAY.execute(rollback_id, dry["attempt_id"], str(payload.get("environment") or "production"))
    except ActuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={**result, "tenant_id": tenant_id})


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


def _extract_top_functions(artifacts: list[dict]) -> list[dict]:
    """Read TopN JSON, or derive it from an available flamegraph SVG."""

    top_functions = _extract_artifact_json(artifacts, "top_json")
    if isinstance(top_functions, list) and top_functions:
        return top_functions

    for artifact in artifacts:
        if artifact.get("artifact_type") != "flamegraph_svg":
            continue
        try:
            svg_text = read_artifact_bytes(artifact).decode("utf-8", errors="replace")
            derived = extract_top_functions_from_svg(svg_text)
            if derived:
                return derived
        except Exception as exc:
            log_event(
                "warning",
                "flamegraph_svg_top_parse_failed",
                artifact_type="flamegraph_svg",
                error=type(exc).__name__,
            )
    return []


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


# ── NLP 自然语言采集 ────────────────────────────────────────────


@app.post("/api/nlp/parse")
def nlp_parse_intent(body: dict) -> APIResponse:
    """将用户自然语言描述解析为结构化任务参数。"""
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    if len(query) > 500:
        raise HTTPException(status_code=400, detail="query 不能超过 500 字符")

    intent = parse_intent(query)
    candidates = resolve_pid(intent.process_name)

    return APIResponse(data={
        "process_name": intent.process_name,
        "collector_type": intent.collector_type,
        "duration_sec": intent.duration_sec,
        "sample_rate": intent.sample_rate,
        "reasoning": intent.reasoning,
        "candidate_pids": [c.to_dict() for c in candidates],
    })


@app.post("/api/nlp/summarize")
def nlp_summarize_task(body: dict) -> APIResponse:
    """对已完成任务的结果进行 AI 总结并生成追问建议。"""
    task_id = body.get("task_id", "")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id 不能为空")

    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    artifacts = repo.artifacts.get(task_id, [])
    top_functions = _extract_top_functions(artifacts)
    ebpf_metrics = _extract_artifact_json(artifacts, "ebpf_metrics")
    suggestions = []

    # 从 top_functions 中提取提示
    for func in top_functions[:5]:
        name = func.get("name", "").lower()
        if "fib" in name:
            suggestions.append("检测到递归 Fibonacci 热点，建议改用迭代 + 记忆化或查表法替代")
        elif "sort" in name:
            suggestions.append("排序开销较高，检查数据集大小，考虑原地排序或基数排序替代")
        elif "json" in name:
            suggestions.append("JSON 编解码占用 CPU 显著，检查是否存在不必要的重复序列化")
        elif "malloc" in name:
            suggestions.append("malloc 调用频繁，考虑使用内存池或 jemalloc 分配器")

    summary = summarize(top_functions, list(set(suggestions))[:3])
    collector = task.collector_type if hasattr(task, "collector_type") else "perf_cpu"
    questions = suggest_followup(top_functions, collector, ebpf_metrics)

    return APIResponse(data={
        "task_id": task_id,
        "summary": summary,
        "followup_questions": questions,
    })


# ── 启动入口 ──────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8191")),
    )
