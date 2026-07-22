"""Compatibility normalizer for rolling sys_metrics.v1 -> v2 upgrades."""

from __future__ import annotations

import os
from typing import Any


def normalize_sys_metrics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema_version") == "sys_metrics.v2":
        if not all(isinstance(payload.get(name), dict) for name in ("host", "process", "container")):
            raise ValueError("sys_metrics.v2 缺少 host/process/container 命名空间")
        return {
            "schema_version": "sys_metrics.v2",
            "host": payload["host"],
            "process": payload["process"],
            "container": payload["container"],
            "normalized_from": "v2",
        }
    if os.getenv("MINI_DROP_SYS_METRICS_STRICT_V2", "0").lower() in {"1", "true", "yes"}:
        raise ValueError("严格 v2 模式拒绝 legacy sys_metrics")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
    return {
        "schema_version": "sys_metrics.v2",
        "host": {
            "cpu": {
                "user_ratio": _ratio(summary.get("avg_cpu_user_pct")),
                "system_ratio": _ratio(summary.get("avg_cpu_sys_pct")),
                "iowait_ratio": _ratio(summary.get("avg_cpu_iowait_pct")),
                "core_count": int(summary.get("host_core_count", 0) or 0),
            },
            "load": {"load1": _num(summary.get("load1m"))},
            "memory": {},
            "psi": {},
            "network": {"scope": "host", "rx_bytes_per_second": _num(summary.get("net_rx_kbps")) * 1024,
                        "tx_bytes_per_second": _num(summary.get("net_tx_kbps")) * 1024},
        },
        "process": {
            "pid": payload.get("pid"),
            "start_time_ticks": summary.get("process_start_time_ticks"),
            "cpu": {"normalized_core_usage": _num(summary.get("process_cpu_core_usage"))},
            "memory": {"rss_bytes": int(_num(summary.get("vmrss_mb")) * 1024 * 1024),
                       "rss_slope_bytes_per_second": _num(summary.get("vmrss_slope_bytes_per_second"))},
            "fd": {"count": int(_num(summary.get("fd_count"))),
                   "growth_per_minute": _num(summary.get("fd_growth_per_minute"))},
            "io": {"read_bytes_per_second": _num(summary.get("process_read_bytes_per_second")),
                   "write_bytes_per_second": _num(summary.get("process_write_bytes_per_second"))},
            "threads": {"count": int(_num(summary.get("thread_count"))),
                        "growth_per_minute": _num(summary.get("thread_growth_per_minute"))},
        },
        "container": {},
        "normalized_from": "legacy.v1",
    }


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ratio(value: Any) -> float:
    return max(0.0, min(_num(value) / 100.0, 1.0))
