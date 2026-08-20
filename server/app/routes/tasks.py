"""Task and task-artifact HTTP routes.

This module is an explicit router and receives the repository through the
application container.  It intentionally does not import the bootstrap module.
"""

from __future__ import annotations


import json
import os
from pathlib import Path
from typing import Annotated, Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from mini_drop_contracts import catalog_payload as collector_catalog_payload

from server.app import storage
from server.app.application.task_views import task_view
from server.app.artifact_service import inspect_artifact
from server.app.common_utils import status_value
from server.app.http.dependencies import get_repository_application_service
from server.app.logging_utils import log_event
from server.app.schemas import (
    APIResponse,
    CancelTaskRequest,
    CreateTaskRequest,
    MAX_SAMPLE_RATE,
    MAX_TASK_DURATION_SEC,
    RCAFeedbackRequest,
    RetryTaskRequest,
)
from server.app.state_machine import Actor, TaskStatus
from server.app.task_kinds import list_task_kinds
from server.app.task_names import normalize_task_name


router = APIRouter()
Repository = Annotated[Any, Depends(get_repository_application_service)]


def _artifact_root() -> Path:
    return Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def _resolve_artifact_path_or_none(local_path: str | None) -> Path | None:
    if not local_path:
        return None
    root = _artifact_root()
    candidate = Path(local_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="产物路径不在允许目录内")
    return resolved if resolved.is_file() else None


def _validate_presign_request(bucket: str, key: str) -> str:
    if bucket != os.getenv("MINIO_BUCKET", "mini-drop"):
        raise HTTPException(status_code=403, detail="bucket 不在允许范围内")
    if not key:
        raise HTTPException(status_code=400, detail="key 参数不能为空")
    normalized = key.replace("\\", "/")
    if normalized.startswith("/") or any(
        part in {"", ".", ".."} for part in normalized.split("/")
    ):
        raise HTTPException(status_code=400, detail="key 路径不合法")
    if not normalized.startswith("tasks/"):
        raise HTTPException(status_code=403, detail="key 不在任务产物目录内")
    return normalized


def _read_artifact_object_text(artifact: dict) -> str:
    bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
    key = _validate_presign_request(bucket, artifact.get("object_key", ""))
    try:
        return storage.read_object_bytes(bucket, key).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_event(
            "warning",
            "artifact_object_read_failed",
            bucket=bucket,
            object_key=key,
            error=type(exc).__name__,
        )
        raise HTTPException(status_code=404, detail="对象存储产物不存在") from exc


def _safe_download_filename(value: str) -> str:
    filename = Path(value.replace("\\", "/")).name
    filename = "".join(ch for ch in filename if ch >= " " and ch not in {'"', ";"})
    return filename[:255] or "artifact.bin"

# ── 任务 ──────────────────────────────────────────────────────


@router.get("/api/task-kinds")
def get_task_kinds(repo: Repository, agent_id: str = "") -> APIResponse:
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


@router.get("/api/v1/collectors")
def get_collectors() -> APIResponse:
    """Versioned CollectorSpec discovery; metadata does not grant execution authority."""
    return APIResponse(data=collector_catalog_payload())


def _validate_task_agent_capability(
    repo: Any,
    agent_id: str,
    collector_type: str,
) -> None:
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


@router.post("/api/tasks")
def create_task(
    payload: CreateTaskRequest,
    request: Request,
    repo: Repository,
) -> APIResponse:
    _validate_task_agent_capability(repo, payload.agent_id, payload.collector_type)
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


@router.get("/api/tasks")
def list_tasks(
    repo: Repository,
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

    all_items = [task_view(t).model_dump() for t in repo.tasks.values()]

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


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str, repo: Repository) -> APIResponse:
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=task_view(task).model_dump())


@router.get("/api/tasks/{task_id}/attempts")
def get_task_attempts(task_id: str, repo: Repository) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_task_attempts(task_id))


@router.get("/api/tasks/{task_id}/analysis-jobs")
def get_task_analysis_jobs(task_id: str, repo: Repository) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_task_analysis_jobs(task_id))


@router.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, repo: Repository) -> APIResponse:
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


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    payload: CancelTaskRequest,
    repo: Repository,
) -> APIResponse:
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


@router.post("/api/tasks/{task_id}/retry")
def retry_task(
    task_id: str,
    payload: RetryTaskRequest,
    request: Request,
    repo: Repository,
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
        repo,
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


@router.get("/api/tasks/{task_id}/events")
def get_task_events(task_id: str, repo: Repository) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = [repo.as_dict(e) for e in repo.events if e.task_id == task_id]
    return APIResponse(data=items)


@router.get("/api/tasks/{task_id}/artifacts")
def get_task_artifacts(
    task_id: str,
    repo: Repository,
    verify: bool = True,
) -> APIResponse:
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


@router.get("/api/tasks/{task_id}/artifacts/{artifact_type}/content")
def get_task_artifact_content(
    task_id: str,
    artifact_type: str,
    repo: Repository,
    index: Optional[int] = None,
) -> APIResponse:
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
                return APIResponse(data=json.loads(text))
            return APIResponse(data={"text": text})
        if path is None:
            raise HTTPException(status_code=404, detail="本地产物不存在")
        if artifact_type.endswith("_json") or artifact.get("content_type") == "application/json":
            return APIResponse(data=json.loads(path.read_text(encoding="utf-8")))
        return APIResponse(data={"text": path.read_text(encoding="utf-8", errors="replace")})
    raise HTTPException(status_code=404, detail="产物不存在")


@router.get("/api/tasks/{task_id}/artifacts/{artifact_type}/download")
def download_task_artifact(
    task_id: str,
    artifact_type: str,
    repo: Repository,
    index: Optional[int] = None,
):
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
        stored_size = storage.object_size(bucket, key)
        if stored_size is None:
            raise HTTPException(status_code=404, detail="产物文件已不存在，请下载结构化证据 JSON")
        expected_size = artifact.get("size_bytes") or 0
        if expected_size and stored_size != expected_size:
            raise HTTPException(status_code=409, detail="产物完整性检查失败：对象大小与登记值不一致")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(stored_size),
        }
        return StreamingResponse(
            storage.stream_object(bucket, key),
            media_type=media_type,
            headers=headers,
        )
    raise HTTPException(status_code=404, detail="产物不存在")


@router.get("/api/storage/presign")
def presign_url(bucket: str = "mini-drop", key: str = "", expires: int = 3600) -> APIResponse:
    """生成 MinIO 预签名下载 URL。"""
    key = _validate_presign_request(bucket, key)
    try:
        url = storage.presigned_get_url(bucket, key, expires)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return APIResponse(data={"url": url, "expires_sec": expires})


@router.post("/api/tasks/{task_id}/diagnose")
def diagnose_task(task_id: str, repo: Repository) -> APIResponse:
    """E9：旧「一次性诊断」编排已退役。

    Task 结果页的入口已收敛为「创建调查 Case」（POST /api/v1/cases，initial_tasks
    携带本 Task 作为初始证据），由持续调查管线产出结论。保留本端点仅返回 410，
    明确指向替代路径，避免旧前端/脚本静默失效。
    """
    task = repo.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    raise HTTPException(
        status_code=410,
        detail=(
            "旧一次性诊断已退役；请创建调查 Case 走持续调查管线："
            "POST /api/v1/cases，initial_tasks=[task_id]"
        ),
    )


@router.get("/api/tasks/{task_id}/diagnoses")
def list_task_diagnoses(task_id: str, repo: Repository) -> APIResponse:
    if task_id not in repo.tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    return APIResponse(data=repo.list_diagnoses_for_task(task_id))


@router.get("/api/diagnoses")
def list_diagnosis_history(
    repo: Repository,
    limit: int = 500,
    offset: int = 0,
) -> APIResponse:
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


@router.get("/api/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: str, repo: Repository) -> APIResponse:
    item = repo.get_diagnosis(diagnosis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="诊断不存在")
    return APIResponse(data=item)


@router.post("/api/diagnoses/{diagnosis_id}/feedback")
def submit_diagnosis_feedback(
    diagnosis_id: str,
    payload: RCAFeedbackRequest,
    repo: Repository,
) -> APIResponse:
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



__all__ = [name for name in list(globals()) if not name.startswith("__")]
