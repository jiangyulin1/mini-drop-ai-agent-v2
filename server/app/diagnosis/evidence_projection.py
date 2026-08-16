"""Deterministic, versioned EvidenceProjection parsers (v6 5.2).

The parser never invents numbers.  It extracts values present in the Artifact
metadata and emits a bounded projection that the Pi Runtime can read through
the read-only Tool Gateway.  Interpretation hints are marked as derived and
are excluded from claim verification unless a concrete field binding is made.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

MAX_SAMPLES = 20
MAX_TOP_ITEMS = 10
MAX_LOG_EVENTS = 12
MAX_ERRORS = 10


def _utc(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except Exception:
        return str(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rounded(value: Any, digits: int = 4) -> float | None:
    number = _number(value)
    if number is None or not math.isfinite(number):
        return None
    return round(number, digits)


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    for key in keys:
        for suffix in ("_p95", "_avg", "_mean", "_max", "_min", "_total"):
            candidate = f"{key}{suffix}"
            if candidate in data and data[candidate] is not None:
                return data[candidate]
    return None


def _samples_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("samples", "metrics", "series", "history"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)][:MAX_SAMPLES]
    return []


def _errors_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("errors", "error_events", "failures"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)][:MAX_ERRORS]
    return []


def _top_items_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("top_items", "top", "processes", "connections", "listeners", "routes", "containers", "filesystems", "hotspots"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)][:MAX_TOP_ITEMS]
    return []


def _window_from(data: dict[str, Any]) -> dict[str, Any]:
    window: dict[str, Any] = {
        "start": _utc(data.get("window_start") or data.get("started_at") or data.get("start")),
        "end": _utc(data.get("window_end") or data.get("finished_at") or data.get("end")),
        "source": "artifact_metadata",
    }
    if not window["start"]:
        window["start"] = _utc(data.get("created_at"))
    return {key: value for key, value in window.items() if value}


def _signal_map(artifact_type: str, data: dict[str, Any]) -> dict[str, Any]:
    signals: dict[str, Any] = {}
    specs: dict[str, tuple[str, ...]] = {
        "sys_metrics": ("cpu_percent", "cpu_pct", "process_cpu_cores", "rss_bytes", "rss_mb", "load_1m", "load_5m", "memory_usage_ratio", "iowait_ratio", "tcp_retransmit_ratio", "tcp_timeout_delta", "oom_kill_delta"),
        "memory_json": ("rss_bytes", "rss_mb", "heap_used", "memory_usage_ratio", "oom_kill_delta"),
        "runtime_metrics": ("gc_pause_ms_p99", "gc_pause_ms", "thread_count", "heap_used", "cpu_percent", "lock_wait_ms"),
        "process_scan": ("process_count", "thread_count", "fd_count", "cpu_percent", "rss_bytes"),
        "connection_probe": ("latency_ms", "connect_latency_ms", "http_status", "success", "retransmit_ratio"),
        "log_scan": ("error_count", "warning_count", "exception_count", "matched_lines"),
        "network_metrics": ("tcp_retransmit_ratio", "tcp_timeout_delta", "rx_bytes", "tx_bytes", "connection_count"),
        "database_metrics": ("active_connections", "connection_count", "lock_wait_ms", "query_latency_ms", "deadlocks"),
        "flamegraph": ("total_samples", "hotspot_self_pct", "hotspot_total_pct"),
        "ebpf_metrics": ("cpu_percent", "rss_bytes", "syscall_rate", "block_io_latency_ms"),
    }
    keys = specs.get(artifact_type, tuple())
    for key in keys:
        value = _rounded(_first_value(data, (key,)))
        if value is not None:
            signals[key] = value
    # Include any other known numeric fields already carrying explicit units.
    for key, value in data.items():
        if key in signals or not isinstance(key, str):
            continue
        if not key.endswith(("_p95", "_p99", "_avg", "_max", "_min", "_delta", "_ratio", "_count", "_ms", "_mb", "_bytes")):
            continue
        number = _rounded(value)
        if number is not None and len(signals) < 16:
            signals[key] = number
    return signals


def build_evidence_projection(
    artifact_type: str,
    metadata: dict[str, Any],
    *,
    source_bytes: int = 0,
    raw_locator: str | None = None,
) -> dict[str, Any]:
    """Return a complete projection envelope (kind, content, projected bytes)."""
    artifact_type = str(artifact_type or "raw")
    data = dict(metadata or {})
    signals = _signal_map(artifact_type, data)
    top_items = _top_items_from(data)
    samples = _samples_from(data)
    errors = _errors_from(data)
    logs = []
    for key in ("log_events", "logs", "lines"):
        value = data.get(key)
        if isinstance(value, list):
            logs = [item for item in value if isinstance(item, dict)][:MAX_LOG_EVENTS]
            break

    kind = "MODEL_SUMMARY"
    if artifact_type == "flamegraph" or top_items and any("stack" in item or "hotspot" in item for item in top_items):
        kind = "FLAMEGRAPH_HOTSPOTS" if artifact_type == "flamegraph" else "TOP_ITEMS"
    elif logs:
        kind = "LOG_EVENTS"
    elif artifact_type in {"sys_metrics", "memory_json", "runtime_metrics", "network_metrics", "database_metrics", "ebpf_metrics"}:
        kind = "TIMESERIES" if samples else "MODEL_SUMMARY"
    elif artifact_type in {"process_scan", "connection_probe"}:
        kind = "TOP_ITEMS"
    else:
        kind = "RAW_PREVIEW"

    signal_parts = ", ".join(f"{key}={value}" for key, value in list(signals.items())[:5])
    summary = (
        f"{artifact_type} 投影"
        + (f"；关键值 {signal_parts}" if signal_parts else "")
        + (f"；{len(top_items)} 个 Top 项" if top_items else "")
        + (f"；{len(samples)} 个样本" if samples else "")
        + (f"；{len(errors)} 个错误" if errors else "")
    )
    interpretation_hints = [
        {"kind": "derived", "text": summary, "source": "deterministic-parser"}
    ]
    content: dict[str, Any] = {
        "artifact_type": artifact_type,
        "summary": summary,
        "signals": signals,
        "top_items": top_items,
        "samples": samples,
        "log_events": logs,
        "errors": errors,
        "coverage": data.get("coverage") or {},
        "window": _window_from(data),
        "target": data.get("target_ref") or data.get("target"),
        "quality": data.get("quality"),
        "interpretation_hints": interpretation_hints,
        "raw_ref": {"locator": raw_locator, "artifact_type": artifact_type},
    }
    projected_json = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    projected_bytes = len(projected_json.encode("utf-8"))
    # Enforce the single-projection budget by dropping the largest optional list
    # first, then truncating the JSON body deterministically.
    max_bytes = 512 * 1024
    truncated = source_bytes > max_bytes or projected_bytes > max_bytes
    while projected_bytes > max_bytes:
        if top_items:
            top_items.pop()
        elif samples:
            samples.pop()
        elif logs:
            logs.pop()
        elif errors:
            errors.pop()
        else:
            for key in ("top_items", "samples", "log_events", "errors"):
                content[key] = []
            content["summary"] = content["summary"][:max_bytes // 4]
            content["signals"] = dict(list(content["signals"].items())[:8])
            break
        projected_json = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        projected_bytes = len(projected_json.encode("utf-8"))
    return {
        "projection_kind": kind,
        "content": content,
        "projection_hash": hashlib.sha256(projected_json.encode("utf-8")).hexdigest(),
        "truncated": truncated,
        "source_bytes": int(source_bytes or projected_bytes),
        "projected_bytes": projected_bytes,
        "projection_schema": "evidence-projection.v1",
        "projection_version": 1,
    }


def project_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata") or {}
    raw_locator = artifact.get("identity_key") or artifact.get("object_key") or str(artifact.get("id") or "")
    return build_evidence_projection(
        artifact.get("artifact_type") or "raw",
        metadata,
        source_bytes=int(artifact.get("size_bytes") or artifact.get("size") or 0),
        raw_locator=str(raw_locator),
    )
