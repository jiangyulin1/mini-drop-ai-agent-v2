"""Legacy route layer extracted from ``server.app.main``.

All modules in this package decorate the shared FastAPI ``app`` object from
``server.app.main``.  Import order is maintained at the bottom of ``main`` so
later modules can reuse helper names re-exported by earlier modules.
"""

from __future__ import annotations


from server.app.main import (  # noqa: F401
    _json,
    _queue,
    _request_principal,
    _request_tenant,
    APIResponse,
    Any,
    BUS,
    HTTPException,
    REGISTRY,
    Request,
    app,
    asyncio,
    env_bool,
    os,
    secrets,
)

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


@app.get("/api/me")
def current_user(request: Request) -> APIResponse:
    principal_id = _request_principal(request)
    roles = sorted(getattr(request.state, "principal_roles", set()))
    return APIResponse(data={
        "user_id": principal_id,
        "name": principal_id,
        "role": roles[0] if roles else "operator",
        "roles": roles,
        "tenant_id": _request_tenant(),
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
    if len(api_key) > 4096:
        raise HTTPException(status_code=400, detail="api_key 长度超过限制")
    if env_bool("MINI_DROP_API_AUTH_ENABLED"):
        expected = os.getenv("MINI_DROP_API_KEY", "")
        if not expected:
            raise HTTPException(status_code=503, detail="API 认证配置不完整")
        if not secrets.compare_digest(api_key, expected):
            raise HTTPException(status_code=401, detail="无效 API Key")
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    secure_override = os.getenv("MINI_DROP_AUTH_COOKIE_SECURE", "").strip()
    secure_cookie = (
        secure_override.lower() in {"1", "true", "yes", "on", "enabled"}
        if secure_override
        else request.url.scheme == "https" or forwarded_proto == "https"
    )
    resp = _JsonResp(content={"code": 0, "message": "ok", "data": None})
    resp.set_cookie(
        key="mini_drop_api_key",
        value=api_key,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
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



__all__ = [name for name in list(globals()) if not name.startswith("__")]
