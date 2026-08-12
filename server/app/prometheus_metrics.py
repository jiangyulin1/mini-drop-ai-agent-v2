"""Prometheus 指标暴露。

提供基于 Counter / Gauge / Histogram 的轻量指标收集，
通过 /api/metrics 端点以 Prometheus 文本格式暴露。
不依赖 prometheus_client 库，自行生成合规文本行。
"""

from __future__ import annotations

import threading
import time
import math
from collections import defaultdict


class MetricsRegistry:
    """线程安全的指标注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # name -> {labels_key -> value}
        self._counters: dict[str, dict[str, float]] = defaultdict(dict)
        self._gauges: dict[str, dict[str, float]] = defaultdict(dict)
        # name -> [values]
        self._histograms: dict[str, list[float]] = defaultdict(list)

    def counter_inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = _labels_key(labels)
        with self._lock:
            self._counters[name][key] = self._counters[name].get(key, 0.0) + value

    def gauge_set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = _labels_key(labels)
        with self._lock:
            self._gauges[name][key] = float(value)

    def histogram_observe(self, name: str, value: float) -> None:
        with self._lock:
            self._histograms[name].append(float(value))
            # 只保留最近 10000 个值
            if len(self._histograms[name]) > 10000:
                self._histograms[name] = self._histograms[name][-5000:]

    def clear(self) -> None:
        """清空所有指标，主要供测试隔离使用。"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def generate(self) -> str:
        """生成 Prometheus 文本格式。"""
        lines: list[str] = []
        ts = int(time.time() * 1000)
        with self._lock:
            for name, entries in sorted(self._counters.items()):
                lines.append(f"# HELP {name} Counter")
                lines.append(f"# TYPE {name} counter")
                for key, val in sorted(entries.items()):
                    label_str = f"{{{key}}}" if key else ""
                    lines.append(f"{name}{label_str} {val} {ts}")
            for name, entries in sorted(self._gauges.items()):
                lines.append(f"# HELP {name} Gauge")
                lines.append(f"# TYPE {name} gauge")
                for key, val in sorted(entries.items()):
                    label_str = f"{{{key}}}" if key else ""
                    lines.append(f"{name}{label_str} {val} {ts}")
            for name, values in sorted(self._histograms.items()):
                if not values:
                    continue
                # We retain raw observations and expose client-side quantiles,
                # which is Prometheus's Summary data model (not Histogram;
                # histograms require cumulative ``le`` buckets).
                lines.append(f"# HELP {name} Summary")
                lines.append(f"# TYPE {name} summary")
                sorted_vals = sorted(values)
                lines.append(f"{name}_count {len(sorted_vals)} {ts}")
                lines.append(f"{name}_sum {sum(sorted_vals)} {ts}")
                for quantile in (0.5, 0.95, 0.99):
                    rank = max(0, math.ceil(quantile * len(sorted_vals)) - 1)
                    lines.append(
                        f'{name}{{quantile="{quantile:g}"}} {sorted_vals[rank]} {ts}'
                    )
        lines.append("")
        return "\n".join(lines)


def _labels_key(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    return ",".join(
        f'{key}="{_escape_label_value(str(value))}"'
        for key, value in sorted(labels.items())
    )


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


# 全局单例
REGISTRY = MetricsRegistry()


def record_http_request(method: str, path: str, status_code: int, latency_ms: float) -> None:
    """记录一次 HTTP 请求指标。"""
    REGISTRY.counter_inc("mini_drop_http_requests_total", {"method": method, "status": str(status_code)})
    REGISTRY.histogram_observe("mini_drop_http_request_latency_ms", latency_ms)


def record_diagnosis(status: str) -> None:
    """记录一次诊断结果。"""
    REGISTRY.counter_inc("mini_drop_diagnosis_total", {"status": status})


def record_source_access(source_id: str, outcome: str, latency_ms: float, result_bytes: int = 0) -> None:
    """Record bounded-cardinality Source Gateway telemetry."""
    REGISTRY.counter_inc(
        "mini_drop_ai_source_access_total",
        {"source": source_id, "outcome": outcome},
    )
    REGISTRY.histogram_observe("mini_drop_ai_source_access_latency_ms", latency_ms)
    if result_bytes > 0:
        REGISTRY.histogram_observe("mini_drop_ai_source_result_bytes", float(result_bytes))


def record_context_optimization(
    pipeline: str,
    *,
    original_chars: int,
    optimized_chars: int,
    redacted_fields: int,
) -> None:
    labels = {"pipeline": pipeline}
    REGISTRY.counter_inc("mini_drop_ai_context_original_chars_total", labels, original_chars)
    REGISTRY.counter_inc("mini_drop_ai_context_optimized_chars_total", labels, optimized_chars)
    REGISTRY.counter_inc("mini_drop_ai_context_redacted_fields_total", labels, redacted_fields)


def record_task_transition(from_status: str, to_status: str) -> None:
    """记录一次任务状态迁移。"""
    REGISTRY.counter_inc("mini_drop_task_transitions_total", {"from": from_status, "to": to_status})


def record_agent_status(status: str) -> None:
    """记录 Agent 状态变更事件（Counter）。"""
    REGISTRY.counter_inc("mini_drop_agent_status_changes_total", {"status": status})


def set_agent_count(online: int, offline: int) -> None:
    """设置 Agent 在线/离线数量。"""
    REGISTRY.gauge_set("mini_drop_agents_online", float(online))
    REGISTRY.gauge_set("mini_drop_agents_offline", float(offline))


def set_task_count_by_status(status: str, count: int) -> None:
    """设置各状态任务数。"""
    REGISTRY.gauge_set("mini_drop_tasks_by_status", float(count), {"status": status})


def record_maintenance_step(step: str, outcome: str) -> None:
    """Record bounded maintenance-loop outcomes and the latest success time."""
    labels = {"step": step, "outcome": outcome}
    REGISTRY.counter_inc("mini_drop_maintenance_runs_total", labels)
    if outcome == "success":
        REGISTRY.gauge_set(
            "mini_drop_maintenance_last_success_unixtime",
            time.time(),
            {"step": step},
        )
