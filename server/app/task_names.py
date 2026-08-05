"""Readable task-name normalization shared by task creation entry points."""

from __future__ import annotations

import re


_UNREADABLE_MARKERS = ("�", "锟斤拷", "烫烫烫")
_QUESTION_RUN = re.compile(r"[?？]{2,}")
# 控制字符与表格分隔符：任务名会流入 markdown 表格、日志和 SSE 事件，
# 需剥离避免表格注入/日志伪造。
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f|\t\r\n]+")

_COLLECTOR_LABELS = {
    "perf_cpu": "CPU 火焰图采集",
    "pyspy": "Python 火焰图采集",
    "continuous_perf": "持续火焰图采集",
    "java_async": "Java 火焰图采集",
    "go_pprof": "Go CPU 剖析",
    "ebpf_io": "I/O 延迟采集",
    "memory_smaps": "内存趋势采集",
    "sys_metrics": "系统指标采集",
}


def is_unreadable_task_name(value: str | None) -> bool:
    """Return whether a supplied name is empty or visibly encoding-damaged."""

    name = (value or "").strip()
    return (
        not name
        or bool(_QUESTION_RUN.search(name))
        or any(marker in name for marker in _UNREADABLE_MARKERS)
    )


def normalize_task_name(
    name: str | None,
    *,
    collector_type: str,
    agent_id: str,
    target_pid: int,
) -> str:
    """Keep readable names and derive a stable, descriptive fallback otherwise."""

    stripped = _CONTROL_CHARS.sub(" ", (name or "").strip())
    if not is_unreadable_task_name(stripped):
        return stripped

    collector_label = _COLLECTOR_LABELS.get(collector_type, f"{collector_type} 采集")
    return f"{collector_label} · {agent_id} · PID {target_pid}"
