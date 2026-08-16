"""Legacy route layer extracted from ``server.app.main``.

All modules in this package decorate the shared FastAPI ``app`` object from
``server.app.main``.  Import order is maintained at the bottom of ``main`` so
later modules can reuse helper names re-exported by earlier modules.
"""

from __future__ import annotations


from server.app.main import (  # noqa: F401
    _json,
    _read_artifact_object_text,
    _resolve_artifact_path_or_none,
    APIResponse,
    Any,
    CreateTaskRequest,
    HTTPException,
    Request,
    app,
    repo,
    status_value,
    time,
)

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



__all__ = [name for name in list(globals()) if not name.startswith("__")]
