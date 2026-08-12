"""服务级历史基线：从历史 sys_metrics 样本计算分位基线并检测异常。

对照实施方案 P5「服务级历史基线」：缺失基线时降低强度，不使用固定阈值冒充环境基线。
"""

from __future__ import annotations

import statistics
from typing import Any


def build_baseline(samples: list[float]) -> dict[str, Any]:
    """由历史样本计算基线（均值/中位/p95/p99/标准差）。"""
    if not samples:
        return {"available": False, "count": 0}
    values = sorted(float(item) for item in samples if item is not None)
    if not values:
        return {"available": False, "count": 0}
    return {
        "available": True,
        "count": len(values),
        "mean": round(statistics.mean(values), 4),
        "p50": round(_percentile(values, 0.5), 4),
        "p95": round(_percentile(values, 0.95), 4),
        "p99": round(_percentile(values, 0.99), 4),
        "stddev": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(values[0], 4),
        "max": round(values[-1], 4),
    }


def detect_anomaly(value: float | None, baseline: dict[str, Any]) -> dict[str, Any]:
    """判断单次测量相对历史基线是否异常（> p99 且显著高于均值）。"""
    if value is None or not baseline.get("available"):
        return {"anomalous": False, "reason": "no_baseline"}
    baseline_value = float(baseline.get("p99") or baseline.get("mean") or 0)
    mean = float(baseline.get("mean") or 0)
    if baseline_value <= 0:
        return {"anomalous": False, "reason": "baseline_zero"}
    ratio = float(value) / baseline_value
    over_mean = float(value) / max(mean, 1e-9) if mean > 0 else ratio
    anomalous = ratio >= 2.0 or over_mean >= 3.0
    worst = max(ratio, over_mean)
    return {
        "anomalous": anomalous,
        "value": value,
        "baseline_p99": baseline_value,
        "baseline_mean": mean,
        "ratio_vs_p99": round(ratio, 3),
        "ratio_vs_mean": round(over_mean, 3),
        "severity": "critical" if worst >= 3.0 else ("warning" if anomalous else "normal"),
    }


def rolling_window(samples: list[float], window: int = 60) -> list[list[float]]:
    """把样本序列切成固定窗口，供多窗口稳健统计。"""
    if window <= 0:
        return [samples] if samples else []
    return [samples[i:i + window] for i in range(0, len(samples), window)]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    index = (len(values) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight
