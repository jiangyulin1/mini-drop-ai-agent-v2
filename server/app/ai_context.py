"""Deterministic preparation of complex system data for model input.

Raw telemetry is retained by the evidence store.  This module only builds a
bounded, redacted and signal-oriented projection for an LLM; it is not an
evidence source and must never be used to rewrite the original artifacts.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


_SENSITIVE_KEY = re.compile(
    r"(^|[_-])(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|secret|cookie|private[_-]?key|credential)([_-]|$)",
    re.IGNORECASE,
)
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ERROR_TERMS = (
    "fatal", "panic", "error", "exception", "failed", "failure", "timeout",
    "denied", "oom", "killed", "unavailable", "refused", "错误", "失败", "超时",
)


@dataclass(frozen=True)
class ContextBudget:
    max_chars: int = 24_000
    max_items_per_list: int = 20
    max_string_chars: int = 800
    max_depth: int = 7
    top_functions: int = 12
    log_events: int = 20

    @classmethod
    def from_environment(cls) -> "ContextBudget":
        return cls(
            max_chars=_bounded_env_int("MINI_DROP_AI_CONTEXT_MAX_CHARS", 24_000, 4_000, 200_000),
            max_items_per_list=_bounded_env_int("MINI_DROP_AI_CONTEXT_MAX_ITEMS", 20, 5, 200),
            max_string_chars=_bounded_env_int("MINI_DROP_AI_CONTEXT_MAX_STRING_CHARS", 800, 80, 8_000),
        )


@dataclass
class OptimizationStats:
    original_chars: int = 0
    optimized_chars: int = 0
    redacted_fields: int = 0
    truncated_strings: int = 0
    duplicate_items_removed: int = 0
    list_items_dropped: int = 0
    metric_samples_compacted: int = 0
    sections_dropped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "original_chars": self.original_chars,
            "optimized_chars": self.optimized_chars,
            "redacted_fields": self.redacted_fields,
            "truncated_strings": self.truncated_strings,
            "duplicate_items_removed": self.duplicate_items_removed,
            "list_items_dropped": self.list_items_dropped,
            "metric_samples_compacted": self.metric_samples_compacted,
            "sections_dropped": self.sections_dropped,
        }


@dataclass
class OptimizedContext:
    payload: dict[str, Any]
    stats: OptimizationStats


CASE_CONTEXT_FIELDS = (
    "schema_version",
    "case_goal",
    "scope",
    "current_iteration",
    "active_hypotheses",
    "evidence_manifest",
    "signal_projection",
    "contradictions",
    "missing_evidence",
    "knowledge_refs",
    "current_understanding",
    "recent_decisions",
    "recent_changes",
    "policy_capabilities",
    "budget_remaining",
    "required_output_schema",
)


def optimize_case_context_packet(
    payload: dict[str, Any],
    *,
    budget: ContextBudget | None = None,
) -> OptimizedContext:
    """Project ``case-context.v1`` while preserving every contract field.

    Unlike the legacy evidence optimizer, this function never drops a top-level
    Case contract key. Oversized sections are fitted within explicit per-section
    budgets, and the returned stats make every truncation auditable.
    """
    effective = budget or ContextBudget.from_environment()
    if payload.get("schema_version") != "case-context.v1":
        raise ValueError("ContextPacket schema_version 必须为 case-context.v1")
    unknown = set(payload) - set(CASE_CONTEXT_FIELDS)
    missing = set(CASE_CONTEXT_FIELDS) - set(payload)
    if unknown or missing:
        raise ValueError(
            f"ContextPacket 字段不匹配: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )

    stats = OptimizationStats(original_chars=_json_size(payload))
    sanitized = {
        key: _sanitize(value, key, 0, effective, stats)
        for key, value in payload.items()
    }
    # The proportions follow the design allocation while leaving fixed fields
    # intact. Empty sections remain explicit so model and audit consumers can
    # distinguish "none" from an omitted contract field.
    shares = {
        "case_goal": 0.10,
        "scope": 0.10,
        "active_hypotheses": 0.16,
        "evidence_manifest": 0.20,
        "signal_projection": 0.20,
        "contradictions": 0.05,
        "missing_evidence": 0.05,
        "knowledge_refs": 0.04,
        "recent_decisions": 0.05,
        "policy_capabilities": 0.025,
        "budget_remaining": 0.025,
    }
    fixed_reserve = 900
    usable = max(1_000, effective.max_chars - fixed_reserve)
    result: dict[str, Any] = {}
    for key in CASE_CONTEXT_FIELDS:
        value = sanitized[key]
        if key in shares:
            section_budget = max(96, int(usable * shares[key]))
            fitted = _fit_value(value, section_budget)
            if fitted is None:
                fitted = [] if isinstance(value, list) else {}
                stats.sections_dropped.append(key)
            elif _json_size(fitted) < _json_size(value):
                stats.sections_dropped.append(key)
            result[key] = fitted
        else:
            result[key] = value

    # A very small global budget can still be exceeded by JSON key overhead.
    # Refit the largest variable sections one item at a time while retaining
    # their top-level presence and the safety-critical fixed fields.
    shrink_order = (
        "signal_projection", "evidence_manifest", "active_hypotheses",
        "recent_decisions", "knowledge_refs", "contradictions", "missing_evidence",
    )
    while _json_size(result) > effective.max_chars:
        changed = False
        for key in shrink_order:
            value = result[key]
            if isinstance(value, list) and value:
                result[key] = value[:-1]
                stats.list_items_dropped += 1
                changed = True
                break
            if isinstance(value, dict) and value:
                last_key = next(reversed(value))
                result[key] = {item_key: item for item_key, item in value.items() if item_key != last_key}
                stats.sections_dropped.append(f"{key}.{last_key}")
                changed = True
                break
        if not changed:
            raise ValueError("ContextPacket 安全必需字段超过上下文预算")

    stats.optimized_chars = _json_size(result)
    return OptimizedContext(payload=result, stats=stats)


def optimize_evidence_context(
    payload: dict[str, Any],
    *,
    budget: ContextBudget | None = None,
    focus_terms: Iterable[str] = (),
) -> OptimizedContext:
    """Build a safe LLM projection while retaining stable top-level paths."""
    effective_budget = budget or ContextBudget.from_environment()
    stats = OptimizationStats(original_chars=_json_size(payload))
    terms = tuple(term.strip().lower() for term in focus_terms if term and term.strip())

    prepared: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "top_functions" and isinstance(value, list):
            prepared[key] = _compact_top_functions(value, effective_budget, stats)
        elif key == "sys_metrics" and isinstance(value, dict):
            prepared[key] = _compact_metrics(value, effective_budget, stats)
        elif key in {"logs", "events", "failure_events"} and isinstance(value, list):
            prepared[key] = _compact_events(value, effective_budget, stats, terms)
        else:
            prepared[key] = _sanitize(value, key, 0, effective_budget, stats)

    selected = _select_sections(prepared, effective_budget, stats)
    selected["_context_meta"] = {
        "schema_version": "mini-drop-ai-context.v1",
        "projection_only": True,
        "raw_evidence_unchanged": True,
        "do_not_cite_as_evidence": True,
        "optimization": stats.as_dict(),
    }
    selected = _ordered_payload(selected)
    stats.optimized_chars = _json_size(selected)
    selected["_context_meta"]["optimization"] = stats.as_dict()

    # Updating the metadata changes the byte count by a few digits.  Refit if
    # the caller selected an unusually tight budget.
    if _json_size(selected) > effective_budget.max_chars:
        selected = _hard_fit(selected, effective_budget.max_chars, stats)
        selected = _ordered_payload(selected)
    stats.optimized_chars = _json_size(selected)
    if "_context_meta" in selected:
        selected["_context_meta"]["optimization"] = stats.as_dict()
        stats.optimized_chars = _json_size(selected)
        selected["_context_meta"]["optimization"]["optimized_chars"] = stats.optimized_chars
    return OptimizedContext(payload=selected, stats=stats)


def _compact_top_functions(
    values: list[Any], budget: ContextBudget, stats: OptimizationStats,
) -> list[Any]:
    clean = [_sanitize(item, "top_functions", 1, budget, stats) for item in values]
    clean = _deduplicate(clean, stats)

    def score(item: Any) -> float:
        if not isinstance(item, dict):
            return 0.0
        for key in ("percent", "percentage", "samples", "self", "value"):
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return 0.0

    clean.sort(key=score, reverse=True)
    if len(clean) > budget.top_functions:
        stats.list_items_dropped += len(clean) - budget.top_functions
        clean = clean[:budget.top_functions]
    return clean


def _compact_metrics(
    value: dict[str, Any], budget: ContextBudget, stats: OptimizationStats,
) -> dict[str, Any]:
    result = {
        key: _sanitize(item, f"sys_metrics.{key}", 1, budget, stats)
        for key, item in value.items()
        if key != "samples"
    }
    samples = value.get("samples")
    if not isinstance(samples, list) or not samples:
        return result

    numeric: dict[str, list[float]] = {}
    for sample in samples:
        if isinstance(sample, dict):
            for path, number in _numeric_leaves(sample):
                numeric.setdefault(path, []).append(number)
    summaries: dict[str, dict[str, float | int]] = {}
    for path, numbers in sorted(numeric.items()):
        if not numbers:
            continue
        summaries[path] = {
            "count": len(numbers),
            "min": _round_number(min(numbers)),
            "max": _round_number(max(numbers)),
            "avg": _round_number(sum(numbers) / len(numbers)),
            "last": _round_number(numbers[-1]),
            "slope_per_sample": _round_number(
                (numbers[-1] - numbers[0]) / max(1, len(numbers) - 1),
            ),
        }
    result["sample_projection"] = {
        "original_sample_count": len(samples),
        "numeric_series": summaries,
        "first": _sanitize(samples[0], "sys_metrics.samples.first", 2, budget, stats),
        "last": _sanitize(samples[-1], "sys_metrics.samples.last", 2, budget, stats),
    }
    stats.metric_samples_compacted += len(samples)
    return result


def _compact_events(
    values: list[Any],
    budget: ContextBudget,
    stats: OptimizationStats,
    focus_terms: tuple[str, ...],
) -> list[Any]:
    clean = [_sanitize(item, "events", 1, budget, stats) for item in values]
    clean = _deduplicate(clean, stats)
    ranked = []
    for index, item in enumerate(clean):
        text = json.dumps(item, ensure_ascii=False, default=str).lower()
        relevance = sum(term in text for term in focus_terms) * 5
        severity = sum(term in text for term in _ERROR_TERMS) * 3
        ranked.append((relevance + severity, index, item))
    # Keep severe/relevant events first and use recency as the tie breaker.
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    kept = [item for _, _, item in ranked[:budget.log_events]]
    if len(clean) > len(kept):
        stats.list_items_dropped += len(clean) - len(kept)
    return kept


def _sanitize(
    value: Any,
    path: str,
    depth: int,
    budget: ContextBudget,
    stats: OptimizationStats,
) -> Any:
    if depth >= budget.max_depth:
        stats.list_items_dropped += 1
        return "[MAX_DEPTH_REACHED]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return _round_number(value) if math.isfinite(value) else str(value)
    if isinstance(value, str):
        text = _CONTROL_CHARS.sub("", _ANSI_ESCAPE.sub("", value))
        text = re.sub(r"[ \t]+", " ", text).strip()
        if len(text) > budget.max_string_chars:
            omitted = len(text) - budget.max_string_chars
            text = f"{text[:budget.max_string_chars]}…[TRUNCATED {omitted} chars]"
            stats.truncated_strings += 1
        return text
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY.search(key):
                result[key] = "[REDACTED]"
                stats.redacted_fields += 1
            else:
                result[key] = _sanitize(item, f"{path}.{key}", depth + 1, budget, stats)
        return result
    if isinstance(value, (list, tuple, set)):
        items = [_sanitize(item, path, depth + 1, budget, stats) for item in value]
        items = _deduplicate(items, stats)
        if len(items) > budget.max_items_per_list:
            stats.list_items_dropped += len(items) - budget.max_items_per_list
            if _looks_like_event_path(path):
                items = items[-budget.max_items_per_list:]
            else:
                items = items[:budget.max_items_per_list]
        return items
    return _sanitize(str(value), path, depth, budget, stats)


def _deduplicate(items: list[Any], stats: OptimizationStats) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in items:
        fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint in seen:
            stats.duplicate_items_removed += 1
            continue
        seen.add(fingerprint)
        result.append(item)
    return result


def _select_sections(
    prepared: dict[str, Any], budget: ContextBudget, stats: OptimizationStats,
) -> dict[str, Any]:
    # Allocate space by diagnostic value, then restore stable prompt ordering.
    priority = (
        "task_metadata", "baseline_diff", "sys_metrics", "top_functions",
        "ebpf_metrics", "tool_results", "failure_events", "events", "logs",
        "suggestions", "agent_stats",
    )
    keys = list(priority) + [key for key in prepared if key not in priority]
    selected: dict[str, Any] = {}
    metadata_reserve = 900
    for key in keys:
        if key not in prepared or prepared[key] in (None, [], {}):
            continue
        current_size = _json_size(selected)
        remaining = budget.max_chars - current_size - metadata_reserve
        if remaining < 128:
            stats.sections_dropped.append(key)
            continue
        fitted = _fit_value(prepared[key], remaining - len(key) - 8)
        if fitted is None:
            stats.sections_dropped.append(key)
            continue
        selected[key] = fitted
    return selected


def _hard_fit(payload: dict[str, Any], max_chars: int, stats: OptimizationStats) -> dict[str, Any]:
    meta = payload.get("_context_meta", {})
    result: dict[str, Any] = {"_context_meta": meta}
    for key in (
        "task_metadata", "baseline_diff", "sys_metrics", "top_functions", "ebpf_metrics",
        "tool_results", "failure_events", "events", "logs", "suggestions", "agent_stats",
    ):
        if key not in payload:
            continue
        remaining = max_chars - _json_size(result) - len(key) - 16
        fitted = _fit_value(payload[key], remaining)
        if fitted is None:
            stats.sections_dropped.append(key)
        else:
            result[key] = fitted
    return result


def _fit_value(value: Any, max_chars: int) -> Any | None:
    if max_chars < 16:
        return None
    if _json_size(value) <= max_chars:
        return value
    if isinstance(value, str):
        if max_chars < 24:
            return None
        return value[:max_chars - 18] + "…[BUDGET_TRIMMED]"
    if isinstance(value, list):
        result = []
        for item in value:
            remaining = max_chars - _json_size(result) - 2
            fitted = _fit_value(item, remaining)
            if fitted is None:
                break
            result.append(fitted)
        return result or None
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            remaining = max_chars - _json_size(result) - len(str(key)) - 8
            fitted = _fit_value(item, remaining)
            if fitted is None:
                continue
            result[str(key)] = fitted
        return result or None
    return value if _json_size(value) <= max_chars else None


def _ordered_payload(value: dict[str, Any]) -> dict[str, Any]:
    # Preserve the legacy order relied on by prompts/tests: top_functions is
    # before eBPF, while stronger incident/baseline signals remain later.
    order = (
        "task_metadata", "_context_meta", "top_functions", "ebpf_metrics", "sys_metrics",
        "baseline_diff", "agent_stats", "tool_results", "suggestions", "failure_events",
        "events", "logs",
    )
    return {key: value[key] for key in order if key in value} | {
        key: item for key, item in value.items() if key not in order
    }


def _numeric_leaves(value: dict[str, Any], prefix: str = ""):
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)):
            yield path, float(item)
        elif isinstance(item, dict):
            yield from _numeric_leaves(item, path)


def _round_number(value: float) -> float | int:
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded


def _looks_like_event_path(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in ("log", "event", "failure", "error"))


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)
