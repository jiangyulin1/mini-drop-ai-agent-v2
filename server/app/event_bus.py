"""轻量级异步事件总线。

支持 Server-Sent Events (SSE) 实时推送到前端：
任务状态变更、Agent 上下线、诊断完成等事件即时通知，
减少无效轮询请求。

使用 weakref 避免僵尸订阅者堆积：
当 SSE 客户端断开连接后，队列被 GC 回收时自动从订阅列表移除。
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import weakref
from datetime import datetime, timezone
from typing import Any

# 每类事件最多保留的历史条数
MAX_HISTORY = 64


class EventBus:
    """线程安全的发布-订阅事件总线（weakref 防泄漏）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[weakref.ref] = []
        self._history: list[dict[str, Any]] = []

    def _dead_collect(self) -> None:
        """清理已被 GC 回收的订阅者（调用前需持有 _lock）。"""
        self._subscribers = [r for r in self._subscribers if r() is not None]

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """发布事件。通知所有订阅者并记录历史。"""
        event = {
            "event": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._history.append(event)
            if len(self._history) > MAX_HISTORY:
                self._history = self._history[-MAX_HISTORY:]
            self._dead_collect()
            dead: list[weakref.ref] = []
            for r in self._subscribers:
                q = r()
                if q is None:
                    dead.append(r)
                    continue
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(r)
            for r in dead:
                try:
                    self._subscribers.remove(r)
                except ValueError:
                    pass

    def subscribe(self) -> queue.Queue:
        """注册一个订阅者队列。调用方负责取消订阅时 clean up。

        返回的队列使用 weakref 跟踪——当调用方丢弃引用后，
        下次 publish() 时会自动从订阅列表移除。
        """
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._dead_collect()
            self._subscribers.append(weakref.ref(q))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """取消订阅（主动取消比等待 GC 更快）。"""
        with self._lock:
            self._dead_collect()
            # 移除匹配的 weakref
            self._subscribers = [r for r in self._subscribers if r() is not q]

    def get_history(self, since: str | None = None) -> list[dict[str, Any]]:
        """获取历史事件（可选地：仅返回 since 时间戳之后的事件）。"""
        with self._lock:
            if since is None:
                return list(self._history)
            return [e for e in self._history if e["timestamp"] > since]


# 全局单例
BUS = EventBus()


# ── 便捷发布函数（供 Server/gRPC 服务调用） ──────────────


def notify_task_changed(task_id: str, from_status: str, to_status: str, reason: str = "") -> None:
    BUS.publish("task_changed", {
        "task_id": task_id,
        "from_status": from_status,
        "to_status": to_status,
        "reason": reason,
    })


def notify_agent_status(agent_id: str, status: str, ip_addr: str = "") -> None:
    BUS.publish("agent_status", {
        "agent_id": agent_id,
        "status": status,
        "ip_addr": ip_addr,
    })


def notify_diagnosis_complete(task_id: str, diagnosis_id: str, status: str) -> None:
    BUS.publish("diagnosis_complete", {
        "task_id": task_id,
        "diagnosis_id": diagnosis_id,
        "status": status,
    })
