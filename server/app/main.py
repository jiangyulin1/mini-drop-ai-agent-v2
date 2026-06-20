"""
Mini-Drop HTTP API 入口。

启动 FastAPI 服务（端口 8191），同时在后台线程运行 gRPC server（端口 50051）。
两者共享同一个 SqlRepository 实例——Agent 通过 gRPC 上报的数据，
Web 通过 HTTP API 即时可见。
"""

from __future__ import annotations

import server.app._env  # noqa: F401 — 自动加载 .env

import json as _json_mod
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path as _Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import asyncio
import json as _json
import queue as _queue
from typing import Optional

from server.app.common_utils import status_value
from server.app.database import init_db, new_session
from server.app.chatops import init_chatops
from server.app.event_bus import BUS, notify_diagnosis_complete
from server.app.prometheus_metrics import record_diagnosis, record_http_request, REGISTRY
from server.app.grpc_server import serve_in_background
from server.app.logging_utils import log_event
from server.app.nlp.intent_parser import parse_intent
from server.app.nlp.process_resolver import resolve_pid
from server.app.nlp.summarizer import summarize, suggest_followup
from server.app.rca.report import run_diagnosis_context
from server.app.schemas import (
    APIResponse,
    CreateTaskRequest,
    MAX_SAMPLE_RATE,
    MAX_TASK_DURATION_SEC,
    RCAFeedbackRequest,
    TaskView,
)
from server.app.sql_repository import SqlRepository
from server.app import storage as store

repo = SqlRepository()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用生命周期：启动时拉起 gRPC，关闭时停止。"""
    init_db()
    if os.getenv("MINIO_AUTO_CREATE_BUCKET", "0") == "1":
        _ensure_minio_bucket_with_retry(os.getenv("MINIO_BUCKET", "mini-drop"))
    _grpc = serve_in_background(repo)
    _offline_task = asyncio.create_task(_offline_sweeper())
    init_chatops()
    try:
        yield
    finally:
        _offline_task.cancel()
        try:
            await _offline_task
        except asyncio.CancelledError:
            pass
        _grpc.stop(grace=None).wait(timeout=5)


async def _offline_sweeper() -> None:
    timeout_sec = int(os.getenv("AGENT_OFFLINE_TIMEOUT_SEC", "30"))
    interval_sec = max(1, min(timeout_sec // 2, 15))
    while True:
        repo.mark_offline_agents(timeout_sec=timeout_sec)
        if hasattr(repo, "persist_agent_metric_snapshots"):
            repo.persist_agent_metric_snapshots()
        await asyncio.sleep(interval_sec)


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
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["x-request-id"] = rid
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
    if _requires_api_auth(request):
        expected = os.getenv("MINI_DROP_API_KEY", "")
        token = _extract_api_token(request)
        if not expected:
            return JSONResponse(
                status_code=500,
                content={"detail": "API auth enabled but MINI_DROP_API_KEY is empty"},
            )
        if not token or not secrets.compare_digest(token, expected):
            return JSONResponse(status_code=401, content={"detail": "无效 API Key"})
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
        request_params=record.request_params,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _requires_api_auth(request: Request) -> bool:
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    path = request.url.path
    return path.startswith("/api/") and path not in {"/api/healthz", "/api/metrics", "/api/auth/set-cookie", "/api/auth/clear-cookie"}


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
            # 先发送历史事件（如果客户端提供了 since 时间戳）
            for event in BUS.get_history(since if since else None):
                yield f"event: {event['event']}\ndata: {_json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"

            # 持续推送新事件
            while True:
                try:
                    event = await asyncio.to_thread(queue.get, True, 30.0)
                    yield f"event: {event['event']}\ndata: {_json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
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
def healthz() -> APIResponse:
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

    all_ok = all(c["status"] == "ok" for c in checks.values())
    return APIResponse(data={
        "service": "mini-drop-server",
        "version": "0.1.0",
        "healthy": all_ok,
        "checks": checks,
    })


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


@app.post("/api/tasks")
def create_task(payload: CreateTaskRequest) -> APIResponse:
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
    try:
        task = repo.create_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return APIResponse(data={"task_id": task.id, "status": status_value(task.status)})


@app.get("/api/tasks")
def list_tasks(
    limit: int = 1000,
    offset: int = 0,
) -> APIResponse:
    """返回任务列表。支持分页。

    可通过 ?limit=50&offset=0 分页。
    """
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    all_items = [_task_view(t).model_dump() for t in repo.tasks.values()]
    total = len(all_items)
    page = all_items[offset:offset + limit] if offset < total else []
    return APIResponse(data={"items": page, "total": total, "offset": offset, "limit": limit})


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> APIResponse:
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=_task_view(task).model_dump())


@app.get("/api/tasks/{task_id}/events")
def get_task_events(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    return APIResponse(data=items)


@app.get("/api/tasks/{task_id}/artifacts")
def get_task_artifacts(task_id: str) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.artifacts.get(task_id, []))


@app.get("/api/tasks/{task_id}/artifacts/{artifact_type}/content")
def get_task_artifact_content(task_id: str, artifact_type: str, index: int | None = None) -> APIResponse:
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
                return APIResponse(data=_json_mod.loads(text))
            return APIResponse(data={"text": text})
        if path is None:
            raise HTTPException(status_code=404, detail="本地产物不存在")
        if artifact_type.endswith("_json") or artifact.get("content_type") == "application/json":
            return APIResponse(data=_json_mod.loads(path.read_text(encoding="utf-8")))
        return APIResponse(data={"text": path.read_text(encoding="utf-8", errors="replace")})
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
    top_functions = _extract_artifact_json(artifacts, "top_json")
    ebpf_metrics = _extract_artifact_json(artifacts, "ebpf_metrics")

    task_events = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    agent_record = repo.agents.get(task.agent_id)
    model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    diagnosis_id = repo.create_diagnosis_run(task_id, model_name)

    outcome = run_diagnosis_context(
        task_id=task_id,
        task_record=task,
        top_functions=top_functions,
        ebpf_metrics=ebpf_metrics,
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


def _extract_artifact_json(artifacts: list[dict], artifact_type: str) -> dict | None:
    """从 artifacts 列表中提取指定类型的 JSON 数据。"""
    for art in artifacts:
        if art.get("artifact_type") == artifact_type:
            local_path = art.get("local_path", "")
            try:
                path = _resolve_artifact_path_or_none(local_path)
                if path is not None:
                    return _json_mod.loads(path.read_text(encoding="utf-8"))
                if art.get("object_key"):
                    return _json_mod.loads(_read_artifact_object_text(art))
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
    top_functions = _extract_artifact_json(artifacts, "top_json") or []
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


# ── ChatOps 测试（通过 Server 持有的 WS 连接发送）─────────────


@app.post("/api/chatops/test")
def chatops_test_endpoint(body: Optional[dict] = None) -> APIResponse:
    """通过 Server 持有的 ChatOps 连接发送测试消息。"""
    from server.app.chatops.dispatcher import is_enabled as _co_enabled, _get_provider_name, _get_webhook_url
    from server.app.chatops.providers import PROVIDERS
    from server.app.chatops.base import ChatopsMessage
    from datetime import datetime, timezone

    if not _co_enabled():
        return APIResponse(code=400, message="ChatOps 未启用")

    provider = PROVIDERS[_get_provider_name()]
    webhook_url = _get_webhook_url()
    msg = ChatopsMessage(
        title="Mini-Drop ChatOps 连接测试",
        content="这是一条来自 Mini-Drop 性能诊断平台的测试消息。\n\n如果你收到这条消息，说明 ChatOps 配置正确 ✅",
        level="info",
        extra_fields=[
            {"label": "平台", "value": _get_provider_name()},
            {"label": "时间", "value": datetime.now(timezone.utc).isoformat()},
        ],
    )
    ok = provider.send(msg, webhook_url)
    return APIResponse(data={"ok": ok, "provider": _get_provider_name()})


@app.post("/api/chatops/notify")
def chatops_notify_endpoint(body: dict) -> APIResponse:
    """通过 Server 持有的 ChatOps 连接发送自定义通知。"""
    from server.app.chatops.dispatcher import is_enabled as _co_enabled, _get_provider_name, _get_webhook_url
    from server.app.chatops.providers import PROVIDERS
    from server.app.chatops.base import ChatopsMessage

    if not _co_enabled():
        return APIResponse(code=400, message="ChatOps 未启用")

    provider = PROVIDERS[_get_provider_name()]
    webhook_url = _get_webhook_url()
    msg = ChatopsMessage(
        title=body.get("title", "通知"),
        content=body.get("content", ""),
        level=body.get("level", "info"),
    )
    ok = provider.send(msg, webhook_url)
    return APIResponse(data={"ok": ok, "provider": _get_provider_name()})


# ── 启动入口 ──────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8191)
