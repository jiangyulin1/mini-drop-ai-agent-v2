"""Versioned Collector catalog shared by the control plane and node Agent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class CollectorSpec:
    collector_id: str
    spec_version: str
    implementation_version: str
    display_name: str
    description: str
    result_label: str
    information_goals: tuple[str, ...]
    output_signals: tuple[str, ...]
    target_types: tuple[str, ...]
    parameter_schema: dict[str, Any]
    risk_level: str
    required_capabilities: tuple[str, ...]
    default_duration: int
    max_duration: int
    default_sample_rate: int
    estimated_overhead: dict[str, Any]
    max_result_bytes: int
    artifact_types: tuple[str, ...]
    projection_kind: str
    projection_version: str
    preview_modes: tuple[str, ...]
    download_supported: bool
    enabled: bool
    presentation: dict[str, Any]
    semantic_operation: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CollectorSpec":
        tuple_fields = {
            "information_goals", "output_signals", "target_types",
            "required_capabilities", "artifact_types", "preview_modes",
        }
        normalized = dict(value)
        for key in tuple_fields:
            normalized[key] = tuple(normalized.get(key) or [])
        return cls(**normalized)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_catalog() -> tuple[dict[str, Any], tuple[CollectorSpec, ...]]:
    path = files("mini_drop_contracts").joinpath("catalog/collectors.v1.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "collector-catalog.v1":
        raise RuntimeError("unsupported Collector catalog schema")
    specs = tuple(CollectorSpec.from_dict(item) for item in raw.get("collectors") or [])
    ids = [item.collector_id for item in specs]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate collector_id in Collector catalog")
    if not specs or any(not item.information_goals for item in specs):
        raise RuntimeError("every CollectorSpec must declare information_goals")
    return raw, specs


_RAW_CATALOG, _SPECS = _load_catalog()
_BY_ID = {item.collector_id: item for item in _SPECS}
_CANONICAL_BYTES = json.dumps(
    _RAW_CATALOG, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
).encode("utf-8")


def list_collector_specs(*, enabled_only: bool = True) -> tuple[CollectorSpec, ...]:
    if not enabled_only:
        return _SPECS
    return tuple(item for item in _SPECS if item.enabled)


def get_collector_spec(collector_id: str) -> CollectorSpec | None:
    return _BY_ID.get(collector_id)


def catalog_hash() -> str:
    return hashlib.sha256(_CANONICAL_BYTES).hexdigest()


def catalog_payload(*, enabled_only: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "collector-catalog.v1",
        "catalog_hash": catalog_hash(),
        "collectors": [item.to_dict() for item in list_collector_specs(enabled_only=enabled_only)],
    }
