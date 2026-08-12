"""Deterministic evidence curation before root-cause analysis.

Raw operational data is never treated as independent votes merely because it
arrived in multiple artifacts.  This module removes exact/retry duplicates,
labels weak or stale observations, detects high-quality conflicts and exposes
source-family independence to the cluster assessor.  It never fabricates a
replacement value and retains an audit record for every suppressed item.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from statistics import median
from typing import Any


COLLECTOR_FAMILIES = {
    "sys_metrics": "procfs_metrics",
    "memory_smaps": "procfs_metrics",
    "runtime_snapshot": "procfs_runtime",
    "perf_cpu": "sampling_profile",
    "continuous_perf": "sampling_profile",
    "java_async": "language_profile",
    "go_pprof": "language_profile",
    "pyspy": "language_profile",
    "ebpf_io": "kernel_trace",
    "log_scan": "application_log",
}

TRUSTED_FAILURE_KINDS = {
    "artifact_upload_failed",
    "agent_disconnected",
    "probe_timeout",
}

# A normalized pressure map contains default False values for domains a
# collector cannot observe. Those defaults are absence of evidence, not a
# contradiction of a domain-specific collector.
SIGNAL_AUTHORITIES = {
    "cpu": {"procfs_metrics", "sampling_profile", "language_profile"},
    "io_wait": {"procfs_metrics", "kernel_trace"},
    "host_iowait_high": {"procfs_metrics", "kernel_trace"},
    "block_latency_high": {"kernel_trace"},
    "process_io_rate_high": {"procfs_metrics", "kernel_trace"},
    "memory": {"procfs_metrics", "language_profile"},
    "fd": {"procfs_metrics"},
    "thread": {"procfs_metrics", "procfs_runtime", "language_profile"},
    "load": {"procfs_metrics"},
    "disk_full": {"procfs_metrics", "application_log"},
    "network_loss": {"procfs_metrics", "kernel_trace", "application_log"},
    "oom": {"procfs_metrics", "application_log"},
    "runtime_lock": {"procfs_runtime", "language_profile"},
}


def curate_observations(
    observations: list[dict[str, Any]],
    *,
    incident_end: datetime | str | None = None,
    max_age_seconds: int = 900,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return curated observations plus a machine-readable review report."""
    reference_time = _as_datetime(incident_end) or datetime.now(timezone.utc)
    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    for original in observations:
        item = dict(original)
        collector = str(item.get("collector_type") or "unknown")
        item["source_family"] = COLLECTOR_FAMILIES.get(collector, collector)
        warnings: list[str] = []
        quality = _quality_score(item)
        observed_at = _as_datetime(item.get("observed_at"))
        if observed_at is not None:
            age = max(0.0, (reference_time - observed_at).total_seconds())
            item["evidence_age_seconds"] = round(age, 3)
            if age > max_age_seconds:
                warnings.append("STALE_OUTSIDE_INCIDENT_WINDOW")
                quality *= 0.25

        failure_kind = item.get("failure_kind")
        if item.get("collection_status") == "FAILED" and failure_kind not in TRUSTED_FAILURE_KINDS:
            warnings.append("FAILED_COLLECTION_NOT_ROOT_CAUSE_EVIDENCE")
            quality *= 0.2

        fingerprint = _fingerprint(item)
        if fingerprint in seen:
            suppressed.append({
                "task_id": item.get("task_id"),
                "reason": "DUPLICATE_OBSERVATION",
                "duplicate_of": seen[fingerprint],
                "evidence_refs": item.get("evidence_refs", []),
            })
            continue
        seen[fingerprint] = str(item.get("task_id") or fingerprint[:12])
        item["evidence_weight"] = round(max(0.0, min(1.0, quality)), 3)
        item["evidence_warnings"] = warnings
        kept.append(item)

    conflicts = _detect_conflicts(kept)
    conflict_tasks = {
        task_id
        for conflict in conflicts
        for task_id in conflict.get("task_ids", [])
    }
    for item in kept:
        if item.get("task_id") in conflict_tasks:
            item["evidence_warnings"] = list(dict.fromkeys(
                item.get("evidence_warnings", []) + ["HIGH_QUALITY_SOURCE_CONFLICT"]
            ))
            item["evidence_weight"] = round(float(item.get("evidence_weight", 1.0)) * 0.7, 3)

    families = sorted({str(item.get("source_family")) for item in kept})
    effective_refs = list(dict.fromkeys(
        ref for item in kept if float(item.get("evidence_weight", 0)) >= 0.4
        for ref in item.get("evidence_refs", [])
    ))
    report = {
        "input_observation_count": len(observations),
        "effective_observation_count": len(kept),
        "suppressed_observation_count": len(suppressed),
        "suppressed": suppressed,
        "conflicts": conflicts,
        "source_families": families,
        "source_independence_count": len(families),
        "effective_evidence_refs": effective_refs,
        "quality_gate_passed": bool(kept) and any(
            float(item.get("evidence_weight", 0)) >= 0.4 for item in kept
        ),
    }
    return kept, report


def independent_source_count(observations: list[dict[str, Any]]) -> int:
    return len({
        str(item.get("source_family") or COLLECTOR_FAMILIES.get(
            str(item.get("collector_type") or "unknown"),
            str(item.get("collector_type") or "unknown"),
        ))
        for item in observations
        if float(item.get("evidence_weight", 1.0)) >= 0.4
    })


def _quality_score(item: dict[str, Any]) -> float:
    if item.get("collection_status") == "DONE":
        score = 0.85
    elif item.get("failure_kind") in TRUSTED_FAILURE_KINDS:
        score = 0.65
    else:
        score = 0.35
    duration = int(item.get("duration_sec") or 0)
    if duration and duration < 3:
        score -= 0.15
    if not item.get("evidence_refs"):
        score -= 0.25
    if not (item.get("facts") or item.get("pressure") or item.get("failure_kind")):
        score -= 0.25
    return score


def _fingerprint(item: dict[str, Any]) -> str:
    target = item.get("target") or {}
    payload = {
        "target": {
            "instance_id": target.get("instance_id"),
            "agent_id": target.get("agent_id"),
            "pid": target.get("pid"),
        },
        "family": item.get("source_family"),
        "facts": _stable_projection(item.get("facts") or {}),
        "pressure": item.get("pressure") or {},
        "log": _stable_projection(item.get("log") or {}),
        "failure_kind": item.get("failure_kind"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _stable_projection(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in sorted(value.items()):
        if key in {"ts", "timestamp", "created_at", "updated_at"}:
            continue
        if isinstance(item, float):
            result[key] = round(item, 4)
        elif isinstance(item, (str, int, bool)) or item is None:
            result[key] = item
        elif isinstance(item, list):
            result[key] = item[:10]
        elif isinstance(item, dict):
            result[key] = _stable_projection(item)
    return result


def _detect_conflicts(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in observations:
        if float(item.get("evidence_weight", 0)) < 0.6:
            continue
        target = item.get("target") or {}
        target_id = str(target.get("instance_id") or target.get("agent_id") or target.get("pid") or "unknown")
        for signal, flagged in (item.get("pressure") or {}).items():
            family = str(item.get("source_family") or "unknown")
            authorities = SIGNAL_AUTHORITIES.get(str(signal))
            if authorities is not None and family not in authorities:
                continue
            # A log collector can prove a signal when it finds a matching
            # error, but an empty/partial log tail cannot prove that the
            # signal is absent. Treating that default False as a vote caused
            # real cgroup OOM metrics to be downgraded by a log scan that had
            # simply found no open log file.
            if family == "application_log" and not bool(flagged):
                continue
            grouped.setdefault((target_id, signal), []).append({
                "task_id": item.get("task_id"),
                "source_family": family,
                "flagged": bool(flagged),
                "evidence_refs": item.get("evidence_refs", []),
            })
    conflicts: list[dict[str, Any]] = []
    for (target_id, signal), rows in grouped.items():
        families = {str(row["source_family"]) for row in rows}
        values = {bool(row["flagged"]) for row in rows}
        if len(families) < 2 or len(values) < 2:
            continue
        conflicts.append({
            "target_ref": target_id,
            "signal": signal,
            "task_ids": [row["task_id"] for row in rows],
            "source_families": sorted(families),
            "evidence_refs": list(dict.fromkeys(
                ref for row in rows for ref in row["evidence_refs"]
            )),
        })
    return conflicts


def robust_outlier_flags(values: list[float], *, threshold: float = 4.5) -> list[bool]:
    """MAD-based helper used by analyzers without allowing one spike to vote twice."""
    if len(values) < 4:
        return [False] * len(values)
    center = median(values)
    deviations = [abs(value - center) for value in values]
    mad = median(deviations)
    if mad <= 0:
        return [False] * len(values)
    return [0.6745 * abs(value - center) / mad > threshold for value in values]


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
