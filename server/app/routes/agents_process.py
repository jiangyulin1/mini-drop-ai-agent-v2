"""Agent discovery and process-scan endpoints."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from server.app.artifact_service import extract_artifact_json
from server.app.common_utils import status_value
from server.app.http.dependencies import get_repository_application_service
from server.app.schemas import APIResponse, CreateTaskRequest


router = APIRouter()
Repository = Annotated[Any, Depends(get_repository_application_service)]

# ── Agent（查询面） ────────────────────────────────────────────


@router.get("/api/agents")
def list_agents(
    repo: Repository,
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


@router.post("/api/agents/{agent_id}/processes/scan")
def scan_agent_processes(
    agent_id: str,
    payload: dict[str, Any],
    request: Request,
    repo: Repository,
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

    processes = _read_scan_artifact(repo, task.id)
    return APIResponse(data={
        "task_id": task.id,
        "status": "DONE",
        "processes": processes,
        "message": f"找到 {len(processes)} 个候选进程",
    })


def _read_scan_artifact(repo: Any, task_id: str) -> list[dict[str, Any]]:
    """读取 process_scan 任务的进程清单产物。"""
    for artifact in repo.artifacts.get(task_id, []):
        if artifact.get("artifact_type") != "process_scan":
            continue
        value = extract_artifact_json([artifact], "process_scan")
        return value.get("processes", []) if isinstance(value, dict) else []
    return []


@router.get("/api/audit-logs")
def list_audit_logs(
    repo: Repository,
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



__all__ = ["router", "scan_agent_processes"]
