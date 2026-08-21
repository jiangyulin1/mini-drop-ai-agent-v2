"""Deterministic, versioned EvidenceProjection parsers (v6 5.2).

The parser never invents numbers.  It extracts values present in the stored
Artifact body (falling back to Artifact metadata when the object cannot be
read) and emits a bounded projection that the Pi Runtime can read through the
read-only Tool Gateway.  Interpretation hints are marked as derived and are
excluded from claim verification unless a concrete field binding is made.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from server.app.diagnosis.network_discovery import build_network_discovery_projection

MAX_SAMPLES = 20
MAX_TOP_ITEMS = 10
MAX_LOG_EVENTS = 12
MAX_ERRORS = 10
MAX_TOPOLOGY_NODES = 40
MAX_TOPOLOGY_EDGES = 80
MAX_IDENTITY_ASSERTIONS = 40
# Upper bound for pulling a stored artifact body into memory for projection.
# Larger objects keep the metadata-only projection and stay reachable through
# the raw_ref locator.
MAX_RAW_FETCH_BYTES = 8 * 1024 * 1024


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
        "network_discovery": ("process_count", "socket_count", "event_count", "listener_count", "connection_count", "established_count", "unresolved_socket_count"),
        "dependency_graph": ("node_count", "edge_count", "managed_target_count", "external_endpoint_count", "virtual_endpoint_count", "coverage_ratio"),
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


def _topology_from(
    artifact_type: str,
    data: dict[str, Any],
    *,
    projection_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if artifact_type not in {"network_discovery", "dependency_graph"}:
        return None
    topology: dict[str, Any] = {
        "schema_version": data.get("schema_version"),
        "graph_digest": data.get("graph_digest") or data.get("digest"),
        "discovery_run_id": data.get("discovery_run_id"),
        "membership_snapshot_id": data.get("membership_snapshot_id"),
        "seed_ref": data.get("seed_ref"),
        "coverage": data.get("coverage") or {},
        "limitations": list(data.get("limitations") or [])[:12],
    }
    if artifact_type == "dependency_graph":
        raw_nodes = [item for item in data.get("nodes") or [] if isinstance(item, dict)]
        node_by_id = {
            str(item.get("entity_id") or ""): item
            for item in raw_nodes if item.get("entity_id")
        }
        retained_edges: list[dict[str, Any]] = []
        referenced_nodes: set[str] = set()
        for edge in [item for item in data.get("edges") or [] if isinstance(item, dict)]:
            source = str(edge.get("source_entity") or "")
            target = str(edge.get("target_entity") or "")
            if source not in node_by_id or target not in node_by_id:
                continue
            expanded = referenced_nodes | {source, target}
            if len(expanded) > MAX_TOPOLOGY_NODES or len(retained_edges) >= MAX_TOPOLOGY_EDGES:
                continue
            referenced_nodes = expanded
            retained_edges.append(edge)
        retained_nodes = [
            node_by_id[node_id] for node_id in sorted(referenced_nodes)
        ]
        if len(retained_nodes) < MAX_TOPOLOGY_NODES:
            for node_id in sorted(node_by_id):
                if node_id in referenced_nodes:
                    continue
                retained_nodes.append(node_by_id[node_id])
                if len(retained_nodes) >= MAX_TOPOLOGY_NODES:
                    break
        topology.update({
            "nodes": retained_nodes,
            "edges": retained_edges,
            "identity_assertions": [
                item for item in data.get("identity_assertions") or [] if isinstance(item, dict)
            ][:MAX_IDENTITY_ASSERTIONS],
            "frontier": data.get("frontier") or {},
        })
        canonical_evidence_id = str((projection_context or {}).get("evidence_id") or "").strip()
        if canonical_evidence_id:
            # Dependency graph artifacts can be produced by the discovery
            # coordinator after the original network snapshots.  Normalize
            # each edge's citation namespace at this boundary: canonical Case
            # Evidence IDs remain usable by the verifier/UI, while collector
            # event IDs are retained as non-addressable lineage.
            normalized_edges = []
            for edge in topology["edges"]:
                edge = dict(edge)
                refs = [str(item) for item in edge.get("evidence_refs") or [] if item]
                event_refs = [str(item) for item in edge.get("event_refs") or [] if item]
                canonical_refs = [
                    item for item in refs
                    if item.startswith(("ev-", "eval:", "evidence:"))
                ]
                event_refs.extend(item for item in refs if item not in canonical_refs)
                if canonical_evidence_id not in canonical_refs:
                    canonical_refs.append(canonical_evidence_id)
                edge["evidence_refs"] = sorted(set(canonical_refs))
                edge["event_refs"] = sorted(set(event_refs))
                normalized_edges.append(edge)
            topology["edges"] = normalized_edges
    else:
        topology.update({
            "agent": data.get("agent") or {},
            "processes": [item for item in data.get("processes") or [] if isinstance(item, dict)][:20],
            "listeners": [item for item in data.get("listeners") or [] if isinstance(item, dict)][:20],
            "connections": [item for item in data.get("connections") or [] if isinstance(item, dict)][:40],
        })
    return topology


def build_evidence_projection(
    artifact_type: str,
    metadata: dict[str, Any],
    *,
    source_bytes: int = 0,
    raw_locator: str | None = None,
    projection_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a complete projection envelope (kind, content, projected bytes)."""
    artifact_type = str(artifact_type or "raw")
    data = dict(metadata or {})
    if artifact_type == "network_discovery":
        return build_network_discovery_projection(
            data,
            source_bytes=source_bytes,
            raw_locator=raw_locator,
            projection_context=projection_context,
        )
    signals = _signal_map(artifact_type, data)
    top_items = _top_items_from(data)
    samples = _samples_from(data)
    errors = _errors_from(data)
    topology = _topology_from(
        artifact_type,
        data,
        projection_context=projection_context,
    )
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
    elif artifact_type in {"network_discovery", "dependency_graph"}:
        kind = "TOPOLOGY_GRAPH"
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
        "topology": topology,
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


def _load_raw_artifact_body(artifact: dict[str, Any]) -> dict[str, Any] | None:
    """Read the stored artifact JSON body, or ``None`` when it is unavailable.

    Artifact ``metadata`` only carries scalar counters recorded at upload time
    (e.g. ``process_count``); the detail arrays that make a projection useful
    (``processes``, ``connections``, ``samples`` …) only exist in the stored
    object.  Projecting metadata alone silently produced empty ``top_items``,
    so the model could never see the offending process.

    Storage is best-effort here: a missing or unreadable object degrades to the
    metadata-only projection rather than failing evidence materialization.
    """

    size = int(artifact.get("size_bytes") or artifact.get("size") or 0)
    if size > MAX_RAW_FETCH_BYTES:
        return None
    try:
        if artifact.get("local_path"):
            from server.app.artifact_service import read_artifact_bytes

            raw = read_artifact_bytes(artifact)
        else:
            from server.app.storage import read_object_bytes

            bucket = str(artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop"))
            object_key = str(artifact.get("object_key") or "")
            if not object_key:
                return None
            # Projection reads are already bounded by the persisted size. Avoid
            # a separate object_size probe so temporary storage-control outages
            # do not hide an otherwise readable Artifact body.
            raw = read_object_bytes(bucket, object_key)
        if len(raw) > MAX_RAW_FETCH_BYTES:
            return None
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def project_artifact(
    artifact: dict[str, Any],
    *,
    projection_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = artifact.get("metadata") or {}
    raw_locator = artifact.get("identity_key") or artifact.get("object_key") or str(artifact.get("id") or "")
    # Prefer the stored body; metadata stays as the fallback and as a source of
    # fields the collector records outside the artifact payload.
    raw_body = _load_raw_artifact_body(artifact)
    if raw_body is not None:
        source = {**raw_body, **{k: v for k, v in metadata.items() if k not in raw_body}}
    else:
        source = metadata
    return build_evidence_projection(
        artifact.get("artifact_type") or "raw",
        source,
        source_bytes=int(artifact.get("size_bytes") or artifact.get("size") or 0),
        raw_locator=str(raw_locator),
        projection_context=projection_context,
    )
