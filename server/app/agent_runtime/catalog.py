"""Canonical Mini-Drop Agent Tool Catalog.

The catalog is discovery metadata, never an authority grant.  The Tool Gateway
always resolves the local registered spec again before accepting a call.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


ToolPolicy = Literal["READ_ONLY", "PROPOSE_ONLY", "WRITE"]


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        **({"required": required} if required else {}),
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    internal_path: str
    policy: ToolPolicy
    enabled_by_default: bool = True
    experimental: bool = False
    needs_approval: bool = False

    def public_dict(self, *, include_internal_path: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_internal_path:
            payload.pop("internal_path", None)
        return payload


_CASE = {"case_id": {"type": "string", "minLength": 1, "maxLength": 256}}
_EVIDENCE_IDS = {
    "evidence_ids": {
        "type": "array", "items": {"type": "string", "minLength": 1},
        "minItems": 1, "maxItems": 50,
    },
}
_STEP_SCHEMA = _object({
    "kind": {"type": "string"},
    "collector_id": {"type": "string"},
    "purpose": {"type": "string"},
    "risk": {"type": "string"},
    "priority": {"type": "integer", "minimum": 0, "maximum": 1000},
    "target_refs": {"type": "array", "items": {"type": "string"}},
    "hypothesis_refs": {"type": "array", "items": {"type": "string"}},
    "selection_strategy": {"type": "string"},
    "depends_on": {"type": "array", "items": {"type": "string"}},
}, ["kind", "collector_id", "purpose", "risk", "priority"])


TOOL_CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_case_snapshot", "Return Case goal, revisions, plan and evidence inventory.",
        _object(_CASE, ["case_id"]), "/internal/agent/tools/case-snapshot", "READ_ONLY",
    ),
    ToolSpec(
        "list_case_evidence", "List canonical Case Evidence and projection hashes.",
        _object({**_CASE, "filters": {"type": "object"}, "cursor": {"type": "string"}}, ["case_id"]),
        "/internal/agent/tools/list-case-evidence", "READ_ONLY",
    ),
    ToolSpec(
        "get_evidence_projection", "Expand bounded EvidenceProjection content.",
        _object({
            **_CASE, **_EVIDENCE_IDS,
            "projection_kinds": {"type": "array", "items": {"type": "string"}},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 262144, "default": 131072},
        }, ["case_id", "evidence_ids"]),
        "/internal/agent/tools/get-evidence-projection", "READ_ONLY",
    ),
    ToolSpec(
        "compare_evidence", "Compare Evidence by signal, target, time and quality.",
        _object({
            **_CASE, **_EVIDENCE_IDS,
            "dimensions": {"type": "array", "items": {"type": "string"}},
        }, ["case_id", "evidence_ids"]),
        "/internal/agent/tools/compare-evidence", "READ_ONLY",
    ),
    ToolSpec(
        "search_knowledge", "Search indexed Knowledge; results are not current Evidence.",
        _object({"query": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]),
        "/internal/agent/tools/search-knowledge", "READ_ONLY",
    ),
    ToolSpec(
        "get_causal_graph", "Read the current verified CausalGraph revision.",
        _object(_CASE, ["case_id"]), "/internal/agent/tools/get-causal-graph", "READ_ONLY",
    ),
    ToolSpec(
        "get_evidence_gaps", "Read precise open EvidenceGap records.",
        _object(_CASE, ["case_id"]), "/internal/agent/tools/get-evidence-gaps", "READ_ONLY",
    ),
    ToolSpec(
        "find_reusable_evidence", "Find scope- and window-compatible reusable Evidence.",
        _object({
            **_CASE,
            "missing_fact": {"type": "string", "minLength": 1},
            "target": {"type": "string", "minLength": 1},
        }, ["case_id", "missing_fact", "target"]),
        "/internal/agent/tools/reusable-evidence", "READ_ONLY",
    ),
    ToolSpec(
        "list_operations", "List registered acquisition operations allowed by the gateway.",
        _object(_CASE), "/internal/agent/tools/list-operations", "READ_ONLY",
    ),
    ToolSpec(
        "evaluate_hypotheses", "Run deterministic analyzers over current Evidence.",
        _object(_CASE, ["case_id"]), "/internal/agent/tools/evaluate-hypotheses", "READ_ONLY",
    ),
    ToolSpec(
        "rca_candidate_analysis", "Run deterministic candidate attribution over structured evidence.",
        _object({
            "task_metadata": {"type": "object"},
            "top_functions": {"type": "array", "items": {"type": "object"}},
        }, ["task_metadata", "top_functions"]),
        "/internal/agent/tools/rca-analysis", "READ_ONLY",
    ),
    ToolSpec(
        "propose_plan_revision", "Propose an immutable Plan revision; the server validates CAS and dependencies.",
        _object({
            **_CASE,
            "goal": {"type": "string", "minLength": 1},
            "expected_case_row_version": {"type": "integer", "minimum": 1},
            "expected_scope_revision": {"type": "integer", "minimum": 1},
            "expected_plan_revision": {"type": "integer", "minimum": 0},
            "steps": {"type": "array", "items": _STEP_SCHEMA, "maxItems": 50},
        }, [
            "case_id", "goal", "expected_case_row_version", "expected_scope_revision",
            "expected_plan_revision", "steps",
        ]),
        "/internal/agent/tools/plan", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "request_operation", "Request a registered low-risk acquisition operation.",
        _object({
            **_CASE,
            "operation": {"type": "string", "minLength": 1},
            "target_selector": {"type": "object"},
            "parameters": {"type": "object"},
            "idempotency_key": {"type": "string"},
            "runtime_generation": {"type": "integer"},
            "expected_control_revision": {"type": "integer"},
            "expected_scope_revision": {"type": "integer"},
        }, ["case_id", "operation"]),
        "/internal/agent/tools/query", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "finish_investigation", "Submit an evidence-bound conclusion for deterministic verification.",
        _object({
            **_CASE, **_EVIDENCE_IDS,
            "summary": {"type": "string", "minLength": 1},
            "state": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "object"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
        }, ["case_id", "summary", "evidence_ids"]),
        "/internal/agent/tools/finish", "PROPOSE_ONLY",
    ),
)

TOOL_CATALOG_BY_NAME = {item.name: item for item in TOOL_CATALOG}
if len(TOOL_CATALOG_BY_NAME) != len(TOOL_CATALOG):
    raise RuntimeError("duplicate ToolSpec.name in TOOL_CATALOG")

READ_ONLY_TOOL_NAMES = frozenset(
    item.name for item in TOOL_CATALOG if item.policy == "READ_ONLY" and item.enabled_by_default
)
PROPOSE_ONLY_TOOL_NAMES = frozenset(
    item.name for item in TOOL_CATALOG if item.policy == "PROPOSE_ONLY" and item.enabled_by_default
)
WRITE_TOOL_NAMES = frozenset(
    item.name for item in TOOL_CATALOG if item.policy == "WRITE" and item.enabled_by_default
)


def get_tool_spec(name: str) -> ToolSpec | None:
    return TOOL_CATALOG_BY_NAME.get(name)


def tool_catalog_payload(*, include_internal_path: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "tool-catalog.v1",
        "tools": [
            item.public_dict(include_internal_path=include_internal_path)
            for item in TOOL_CATALOG
        ],
    }
