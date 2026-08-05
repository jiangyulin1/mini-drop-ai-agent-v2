"""
任务状态机：定义状态枚举、合法迁移路径、迁移校验和事件构造。

状态流转：
    PENDING  → RUNNING   → UPLOADING  → ANALYZING  → DONE
       │          │            │             │
       ├──────────┴────────────┴─────────────┘→ FAILED
       └──────────┴────────────┴─────────────┘→ CANCELLED

每次状态迁移必须提供 reason，且写入 task_status_events 表。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Any


@unique
class TaskStatus(str, Enum):
    """任务主状态枚举。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    UPLOADING = "UPLOADING"
    ANALYZING = "ANALYZING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@unique
class CollectionStatus(str, Enum):
    """Durable state of the Agent-side collection phase."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    UPLOADING = "UPLOADING"
    COLLECTED = "COLLECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@unique
class AnalysisStatus(str, Enum):
    """Durable state of the asynchronous analysis phase."""

    WAITING = "WAITING"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@unique
class Actor(str, Enum):
    """触发状态迁移的角色。用于审计追踪。"""

    WEB = "web"
    SERVER = "server"
    AGENT = "agent"
    ANALYZER = "analyzer"
    AI = "ai"


# 合法迁移表：from_status → 可以迁移到的 status 集合。
# None 表示初始状态（任务创建时的 first transition）。
ALLOWED_TRANSITIONS: dict[TaskStatus | None, set[TaskStatus]] = {
    None: {TaskStatus.PENDING},
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.UPLOADING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.UPLOADING: {TaskStatus.ANALYZING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.ANALYZING: {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

# 终态集合：进入这些状态后不再允许任何迁移。
TERMINAL_STATES: frozenset[TaskStatus] = frozenset({
    TaskStatus.DONE,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
})


@dataclass(frozen=True)
class StatusEvent:
    """一次状态迁移事件，对应 task_status_events 表的一行。"""

    task_id: str
    from_status: TaskStatus | None
    to_status: TaskStatus
    reason: str
    actor: Actor
    metadata: dict[str, Any]
    created_at: datetime


def now_utc() -> datetime:
    """返回 UTC 时间戳，统一所有事件和审计日志的时间源。"""
    return datetime.now(timezone.utc)


def validate_transition(
    from_status: TaskStatus | None,
    to_status: TaskStatus,
    reason: str,
) -> None:
    """校验一次状态迁移是否合法。

    Args:
        from_status: 迁移前的状态，任务创建时为 None。
        to_status: 迁移后的状态。
        reason: 迁移原因，不能为空或纯空白。

    Raises:
        ValueError: 当迁移路径不合法或 reason 为空时。
    """
    if reason is None or not reason.strip():
        raise ValueError("状态迁移必须提供非空的 reason 字段")

    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"非法的状态迁移: {from_status} -> {to_status}。"
            f"允许的目标状态: {sorted(allowed, key=lambda s: s.value) if allowed else '无（终态）'}"
        )


def is_terminal(status: TaskStatus) -> bool:
    """判断是否为终态。"""
    return status in TERMINAL_STATES


def build_status_event(
    task_id: str,
    from_status: TaskStatus | None,
    to_status: TaskStatus,
    reason: str,
    actor: Actor,
    metadata: dict[str, Any] | None = None,
) -> StatusEvent:
    """校验迁移合法性后构造一条 StatusEvent。

    Args:
        task_id: 任务唯一标识。
        from_status: 迁移前状态，首次迁移传 None。
        to_status: 迁移后状态。
        reason: 迁移原因（如 "agent heartbeat pulled pending task"）。
        actor: 触发迁移的角色。
        metadata: 附加信息（耗时、错误详情、产物路径等）。

    Returns:
        校验通过后返回 StatusEvent 实例。

    Raises:
        ValueError: 迁移不合法或 reason 为空。
    """
    validate_transition(from_status, to_status, reason)
    return StatusEvent(
        task_id=task_id,
        from_status=from_status,
        to_status=to_status,
        reason=reason.strip(),
        actor=actor,
        metadata=metadata or {},
        created_at=now_utc(),
    )
