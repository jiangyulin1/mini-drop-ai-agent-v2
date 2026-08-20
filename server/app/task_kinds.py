"""TaskKind metadata exposed to API and Web clients.

The collector implementation remains owned by the Agent.  This registry is the
control-plane contract for forms, validation hints, capability matching and
result presentation, so browser clients do not need to duplicate parameter
bounds and defaults.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from mini_drop_contracts import list_collector_specs


def _legacy_task_kind(spec: Any) -> dict[str, Any]:
    properties = deepcopy((spec.parameter_schema or {}).get("properties") or {})
    if "duration_sec" in properties:
        properties["duration_sec"].update({
            "unit": "秒", "help": "采集持续时间；任务运行期间目标进程必须保持存活。",
        })
    if "sample_rate" in properties:
        properties["sample_rate"].update({
            "unit": "Hz", "help": "每秒采样频率；频率越高，结果越精细，额外开销也越大。",
        })
    if "target_pid" in properties:
        properties["target_pid"]["help"] = "目标 Linux 进程 PID。"
    return {
        "key": spec.collector_id,
        "display_name": spec.display_name,
        "result_label": spec.result_label,
        "description": spec.description,
        "capability": spec.collector_id,
        "parameter_schema": properties,
        "defaults": {
            "duration_sec": spec.default_duration,
            "sample_rate": spec.default_sample_rate,
        },
        "permission_requirements": list(spec.required_capabilities),
        "presentation": deepcopy(spec.presentation),
        "collector_spec_version": spec.spec_version,
        "information_goals": list(spec.information_goals),
        "risk_level": spec.risk_level,
    }


TASK_KINDS: tuple[dict[str, Any], ...] = tuple(
    _legacy_task_kind(spec) for spec in list_collector_specs()
)


def list_task_kinds(capabilities: set[str] | None = None) -> list[dict[str, Any]]:
    """Return a defensive copy, optionally limited to Agent capabilities."""

    items = TASK_KINDS
    if capabilities is not None:
        items = tuple(item for item in items if item["capability"] in capabilities)
    return deepcopy(list(items))
