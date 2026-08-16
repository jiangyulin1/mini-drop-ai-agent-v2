"""Deterministic recovery-metric helpers.

This module contains no HTTP registration and has no dependency on the
application bootstrap layer.
"""

from __future__ import annotations

from typing import Any

from server.app.common_utils import status_value

# ── 恢复验证与人工动作回填（多轮诊断闭环） ────────────────────


VERIFICATION_TASK_DURATION_SEC = 10


def _read_sys_metrics_artifact_keys(artifact_value: Any) -> dict[str, float]:
    """从 sys_metrics.v2 产物提取验证可对比的关键指标。"""
    if not isinstance(artifact_value, dict):
        return {}
    normalized = artifact_value.get("normalized")
    if not isinstance(normalized, dict):
        normalized = artifact_value
    result: dict[str, float] = {}
    process = normalized.get("process") or {}
    cpu = process.get("cpu") or {}
    mem = process.get("memory") or {}
    host = normalized.get("host") or {}
    host_cpu = host.get("cpu") or {}
    host_network = host.get("network") or {}
    tcp = host_network.get("tcp") or {}
    filesystems = host.get("filesystems") or {}
    root_fs = filesystems.get("/") or {}
    target_fs = filesystems.get("target_root") or {}
    container = normalized.get("container") or {}
    memory_events = container.get("memory_event_deltas") or {}
    try:
        result["process_cpu_cores"] = float(cpu.get("normalized_core_usage", 0.0) or 0.0)
        result["iowait_ratio"] = float(host_cpu.get("iowait_ratio", 0.0) or 0.0)
        result["rss_bytes"] = float(mem.get("rss_bytes", 0.0) or 0.0)
        result["container_memory_usage_ratio"] = float(container.get("memory_usage_ratio", 0.0) or 0.0)
        result["oom_kill_delta"] = float(memory_events.get("oom_kill", 0.0) or 0.0)
        result["filesystem_used_ratio"] = max(
            float(root_fs.get("used_ratio", 0.0) or 0.0),
            float(target_fs.get("used_ratio", 0.0) or 0.0),
        )
        result["tcp_retransmit_ratio"] = float(tcp.get("retransmit_ratio", 0.0) or 0.0)
        result["tcp_timeout_delta"] = float(tcp.get("TCPTimeouts", 0.0) or 0.0)
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
        key for key in (
            "process_cpu_cores", "iowait_ratio", "rss_bytes",
            "container_memory_usage_ratio", "oom_kill_delta",
            "filesystem_used_ratio", "tcp_retransmit_ratio", "tcp_timeout_delta",
        )
        if baseline.get(key) is not None and current.get(key) is not None
    ]
    if not keys:
        return {"status": "indeterminate", "reason": "缺少可对比的关键指标", "metrics": {}}
    metrics: dict[str, Any] = {}
    for key in keys:
        b = float(baseline[key])
        c = float(current[key])
        ratio = (c / b) if b > 0 else (0.0 if c <= 0.02 else 1.0)
        if key == "oom_kill_delta":
            verdict = "recovered" if c == 0 and b > 0 else "normal" if c == 0 else "degraded"
        elif key == "filesystem_used_ratio":
            verdict = "recovered" if b >= 0.95 and c < 0.90 else "normal" if c < 0.90 else "degraded"
        elif key == "tcp_retransmit_ratio":
            verdict = "recovered" if b >= 0.05 and c < 0.01 else "normal" if c < 0.01 else "degraded"
        elif key == "tcp_timeout_delta":
            verdict = "recovered" if b > 0 and c == 0 else "normal" if c == 0 else "degraded"
        elif key == "container_memory_usage_ratio":
            verdict = "recovered" if b >= 0.9 and c < 0.8 else "normal" if c < 0.8 else "degraded"
        elif b <= 0.02 and c <= 0.02:
            verdict = "normal"
        elif ratio < 0.5:
            verdict = "recovered"
        elif ratio > 1.5 and c > 0.02:
            verdict = "degraded"
        else:
            verdict = "unchanged"
        metrics[key] = {"baseline": round(b, 4), "current": round(c, 4), "ratio": round(ratio, 2), "verdict": verdict}
    verdicts = [item["verdict"] for item in metrics.values()]
    guard_keys = {
        "container_memory_usage_ratio", "oom_kill_delta",
        "filesystem_used_ratio", "tcp_retransmit_ratio", "tcp_timeout_delta",
    }
    guard_verdicts = [metrics[key]["verdict"] for key in guard_keys if key in metrics]
    if "degraded" in verdicts:
        status = "degraded"
    elif verdicts and all(item in ("recovered", "normal") for item in verdicts):
        status = "recovered"
    elif guard_verdicts and all(item in ("recovered", "normal") for item in guard_verdicts):
        # An unchanged unbounded value such as RSS is not a regression when
        # all absolute resource guards are healthy. Business probes decide
        # service recovery; this result reports resource safety only.
        status = "recovered"
    elif "recovered" in verdicts:
        status = "partially_recovered"
    else:
        status = "not_recovered"
    return {"status": status, "reason": f"对比 {len(keys)} 项关键指标", "metrics": metrics}



__all__ = [
    "VERIFICATION_TASK_DURATION_SEC",
    "_find_diagnosis_sys_metrics_task",
    "_judge_recovery",
    "_read_sys_metrics_artifact_keys",
]
