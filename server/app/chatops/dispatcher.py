"""ChatOps 事件分发器。

从 EventBus 订阅事件，转换为 ChatopsMessage，通过配置的 IM 平台发送。
"""

from __future__ import annotations

import os
import threading
from typing import Any

from server.app.chatops.base import ChatopsMessage
from server.app.chatops.providers import PROVIDERS
from server.app.logging_utils import log_event

# ── 配置 ──────────────────────────────────────────────────


def _get_provider_name() -> str | None:
    raw = os.getenv("MINI_DROP_CHATOPS_PROVIDER", "").strip().lower()
    return raw if raw in PROVIDERS else None


def _get_webhook_url() -> str:
    return os.getenv("MINI_DROP_CHATOPS_WEBHOOK_URL", "").strip()


def is_enabled() -> bool:
    """判断 ChatOps 是否启用。"""
    if os.getenv("MINI_DROP_CHATOPS_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    return bool(_get_provider_name()) and bool(_get_webhook_url())


# ── 消息格式化 ────────────────────────────────────────────


def _format_task_message(event_type: str, data: dict[str, Any]) -> ChatopsMessage | None:
    """将任务事件格式化为 IM 消息。"""
    task_id = data.get("task_id", "")
    to_status = data.get("to_status", "")
    reason = data.get("reason", "")

    if to_status == "DONE":
        return ChatopsMessage(
            title=f"采集任务完成",
            content=f"任务 `{task_id}` 已完成。\n{reason}",
            level="success",
            extra_fields=[
                {"label": "任务 ID", "value": task_id},
                {"label": "状态", "value": "DONE ✅"},
            ],
            link_text="查看火焰图与诊断",
        )
    elif to_status == "FAILED":
        return ChatopsMessage(
            title="采集任务失败",
            content=f"任务 `{task_id}` 执行失败。\n原因：{reason}",
            level="error",
            extra_fields=[
                {"label": "任务 ID", "value": task_id},
                {"label": "失败原因", "value": reason},
            ],
        )
    elif to_status == "RUNNING":
        return ChatopsMessage(
            title="采集任务开始执行",
            content=f"任务 `{task_id}` 已开始采集。",
            level="info",
            extra_fields=[{"label": "任务 ID", "value": task_id}],
        )
    return None


def _format_agent_message(event_type: str, data: dict[str, Any]) -> ChatopsMessage | None:
    """将 Agent 事件格式化为 IM 消息。"""
    agent_id = data.get("agent_id", "")
    status = data.get("status", "")

    if status == "OFFLINE":
        return ChatopsMessage(
            title="Agent 离线",
            content=f"Agent `{agent_id}` 心跳超时，已标记为离线。",
            level="warning",
            extra_fields=[
                {"label": "Agent", "value": agent_id},
                {"label": "IP", "value": data.get("ip_addr", "-")},
            ],
        )
    return None


def _format_diagnosis_message(event_type: str, data: dict[str, Any]) -> ChatopsMessage | None:
    """将诊断事件格式化为 IM 消息。"""
    task_id = data.get("task_id", "")
    diag_id = data.get("diagnosis_id", "")
    status = data.get("status", "")

    if status == "DONE":
        return ChatopsMessage(
            title="AI 诊断完成",
            content=f"任务 `{task_id}` 的智能归因分析已完成。",
            level="info",
            extra_fields=[
                {"label": "诊断 ID", "value": diag_id},
                {"label": "任务 ID", "value": task_id},
            ],
            link_text="查看诊断报告",
        )
    elif status == "FAILED":
        return ChatopsMessage(
            title="AI 诊断失败",
            content=f"任务 `{task_id}` 的智能归因分析未通过验证。",
            level="warning",
            extra_fields=[
                {"label": "诊断 ID", "value": diag_id},
            ],
        )
    return None


_FORMATTERS = {
    "task_changed": _format_task_message,
    "agent_status": _format_agent_message,
    "diagnosis_complete": _format_diagnosis_message,
}


# ── 调度 ──────────────────────────────────────────────────


def dispatch_event(event_type: str, data: dict[str, Any]) -> None:
    """将事件分发到 ChatOps 渠道（如果已配置）。

    可在任意位置直接调用此函数，即使 ChatOps 未启用也不会有副作用。
    """
    if not is_enabled():
        return

    formatter = _FORMATTERS.get(event_type)
    if formatter is None:
        return

    try:
        message = formatter(event_type, data)
        if message is None:
            return

        provider = PROVIDERS[_get_provider_name()]
        webhook_url = _get_webhook_url()

        ok = provider.send(message, webhook_url)
        if ok:
            log_event("info", "chatops_message_sent", event_type=event_type, provider=_get_provider_name())
        else:
            log_event("warning", "chatops_send_failed", event_type=event_type, provider=_get_provider_name())
    except Exception as exc:
        log_event("error", "chatops_dispatch_error", event_type=event_type, error=type(exc).__name__, detail=str(exc)[:200])


def dispatch_event_async(event_type: str, data: dict[str, Any]) -> None:
    """异步版本：在后台线程中发送，不阻塞调用方。"""
    if not is_enabled():
        return
    t = threading.Thread(target=dispatch_event, args=(event_type, data), daemon=True)
    t.start()


# ── 应用启动时初始化 ──────────────────────────────────────


def init_chatops() -> None:
    """应用启动时初始化 ChatOps：订阅 EventBus 并启动后台监听线程。

    如果未配置 ChatOps（webhook URL 为空或 provider 无效），
    此函数不执行任何操作。
    """
    if not is_enabled():
        log_event("info", "chatops_disabled")
        return

    provider_name = _get_provider_name()
    provider = PROVIDERS[provider_name]
    webhook_url = _get_webhook_url()

    # 校验 webhook URL 格式
    if not provider.validate_webhook_url(webhook_url):
        log_event("error", "chatops_invalid_webhook_url", provider=provider_name, url=webhook_url[:80])
        return

    # qqbot 额外校验：必须设置目标群号
    if provider_name == "qqbot" and not os.getenv("MINI_DROP_QQBOT_TARGET_ID", "").strip():
        log_event("error", "chatops_qqbot_no_target", hint="请设置 MINI_DROP_QQBOT_TARGET_ID 为目标群号或 QQ 号")
        return

    # qqbot WebSocket 模式：启动内嵌 WS 服务端
    if provider_name == "qqbot" and webhook_url.startswith("ws"):
        from server.app.chatops.providers.qqbot import QQBotProvider
        QQBotProvider.start_ws_server(webhook_url)

    log_event("info", "chatops_initialized", provider=provider_name)

    # 订阅事件总线，后台线程持续监听
    def _listen_loop() -> None:
        from server.app.event_bus import BUS
        queue = BUS.subscribe()
        while True:
            try:
                event = queue.get()
                dispatch_event(event["event"], event["data"])
            except Exception:
                log_event("error", "chatops_listen_loop_error", detail="unhandled exception in event dispatch loop")

    t = threading.Thread(target=_listen_loop, daemon=True)
    t.start()
