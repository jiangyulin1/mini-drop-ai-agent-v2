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

_PLAN_STEP = _object({
    "step_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "kind": {"type": "string", "enum": ["COLLECTION"]},
    "collector_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "target_refs": {"type": "array", "items": {"type": "string"}},
    "purpose": {"type": "string", "minLength": 1, "maxLength": 500},
    "hypothesis_refs": {"type": "array", "items": {"type": "string"}},
    "expected_information": {"type": "string", "maxLength": 1000},
    "priority": {"type": "integer"},
    "priority_source": {"type": "string", "enum": ["AI", "USER", "SYSTEM"]},
    "user_locked": {"type": "boolean"},
    "depends_on": {"type": "array", "items": {"type": "string"}},
    "risk": {"type": "string", "enum": ["READ_LOW", "READ_HIGH", "WRITE", "R0", "R1", "R2", "R3"]},
    "selection_strategy": {"type": "string"},
    "status": {"type": "string", "enum": ["DRAFT", "QUEUED", "WAITING_APPROVAL"]},
}, ["collector_id", "purpose"])


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
        "get_causal_graph", "Read the latest model-proposed and verifier-owned causal graph revision.",
        _object(_CASE, ["case_id"]), "/internal/agent/tools/get-causal-graph", "READ_ONLY",
    ),
    ToolSpec(
        "get_dependency_graph", "Read the evidence-backed communication graph; dependency is not causality.",
        _object(_CASE, ["case_id"]), "/internal/agent/tools/get-dependency-graph", "READ_ONLY",
    ),
    ToolSpec(
        "get_evidence_gaps", "Read explicit unresolved and resolved Evidence gaps.",
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
        "list_collectors", "List versioned CollectorSpecs, information goals, risk and cost.",
        _object(_CASE), "/internal/agent/tools/collectors", "READ_ONLY",
    ),
    ToolSpec(
        "propose_collection", "Propose one catalog-backed collection for deterministic validation and dispatch.",
        _object({
            **_CASE,
            "collector_id": {"type": "string", "minLength": 1},
            "target_selector": {"type": "object"},
            "parameters": {"type": "object"},
            "information_goal": {"type": "string", "minLength": 1, "maxLength": 500},
            "reason_summary": {"type": "string", "maxLength": 1000},
            "time_window": {"type": "object"},
            "input_evidence_refs": {"type": "array", "items": {"type": "string"}},
            "discovery_run_id": {
                "type": "string",
                "pattern": r"^discovery-[0-9a-f]{20}$",
                "description": (
                    "Required when the target agent_id + pid pair is outside the "
                    "original Case process scope, including a newly discovered "
                    "PID on the same Agent; use the exact completed discovery run ID"
                ),
            },
            "discovery_evidence_refs": {
                "type": "array", "maxItems": 32,
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Active canonical dependency Evidence proving the discovered "
                    "agent_id + pid target in discovery_run_id"
                ),
            },
            "idempotency_key": {"type": "string", "maxLength": 256},
            "runtime_generation": {"type": "integer", "minimum": 1},
            "expected_control_revision": {"type": "integer", "minimum": 1},
            "expected_scope_revision": {"type": "integer", "minimum": 1},
        }, ["case_id", "collector_id", "target_selector", "parameters", "information_goal"]),
        "/internal/agent/tools/collection-proposal", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "discover_topology",
        (
            "Start or advance bounded Case-scoped network topology discovery. "
            "On the first call omit run_id; never invent one. On later calls, "
            "reuse only the exact discovery-* run_id returned by Mini-Drop. "
            "COLLECTING waits for an Evidence wakeup; COMPLETED or PARTIAL returns "
            "an evidence-backed dependency graph, never a causal conclusion."
        ),
        _object({
            **_CASE,
            "run_id": {
                "type": "string",
                "pattern": r"^discovery-[0-9a-f]{20}$",
                "description": (
                    "Exact run_id returned by a previous discover_topology call; "
                    "omit this field entirely on the first call"
                ),
            },
            "seed_agent_id": {"type": "string", "maxLength": 128},
            "seed_pid": {"type": "integer", "minimum": 1, "maximum": 4194304},
            "max_hops": {"type": "integer", "minimum": 0, "maximum": 4, "default": 2},
            "max_hosts": {"type": "integer", "minimum": 1, "maximum": 32, "default": 12},
            "max_processes": {"type": "integer", "minimum": 1, "maximum": 200, "default": 40},
            "max_edges": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
            "max_parallel_tasks": {"type": "integer", "minimum": 1, "maximum": 8, "default": 8},
            "include_loopback": {"type": "boolean", "default": False},
            "collect_registered_peers": {"type": "boolean", "default": True},
            "wait_timeout_sec": {"type": "integer", "minimum": 0, "maximum": 45, "default": 0},
            "idempotency_key": {"type": "string", "maxLength": 256},
            "runtime_generation": {"type": "integer", "minimum": 1},
            "expected_control_revision": {"type": "integer", "minimum": 1},
            "expected_scope_revision": {"type": "integer", "minimum": 1},
        }, ["case_id"]),
        "/internal/agent/tools/topology-discovery", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "propose_plan_revision", "Propose a revision-locked investigation plan; Mini-Drop owns step execution.",
        _object({
            **_CASE,
            "goal": {"type": "string", "minLength": 1, "maxLength": 1000},
            "steps": {"type": "array", "items": _PLAN_STEP, "maxItems": 50},
            "expected_case_row_version": {"type": "integer", "minimum": 0},
            "expected_scope_revision": {"type": "integer", "minimum": 1},
            "expected_plan_revision": {"type": "integer", "minimum": 0},
            "source": {"type": "string"},
        }, ["case_id", "goal", "steps", "expected_case_row_version", "expected_scope_revision", "expected_plan_revision"]),
        "/internal/agent/tools/plan", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "propose_hypothesis_revision", "Propose evidence-bound hypotheses. Use hypothesis_id and statement; contradictions belong in contradicting_evidence_refs.",
        _object({
            **_CASE,
            "hypotheses": {"type": "array", "minItems": 1, "maxItems": 30, "items": _object({
                "hypothesis_id": {"type": "string", "minLength": 1},
                "statement": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": [
                    "PROPOSED", "ACTIVE", "SUPPORTED", "WEAKENED",
                    "RULED_OUT", "CONFIRMED", "UNKNOWN",
                ]},
                "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}},
                "contradicting_evidence_refs": {"type": "array", "items": {"type": "string"}},
                "missing_evidence": {"type": "array", "items": {"type": "string"}},
                "alternatives": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            }, ["hypothesis_id", "statement", "status"])},
            "edges": {"type": "array", "maxItems": 60, "items": _object({
                "source_hypothesis_id": {"type": "string", "minLength": 1},
                "target_hypothesis_id": {"type": "string", "minLength": 1},
                "relation": {"type": "string", "minLength": 1},
            }, ["source_hypothesis_id", "target_hypothesis_id", "relation"])},
            "expected_scope_revision": {"type": "integer", "minimum": 1},
        }, ["case_id", "hypotheses", "expected_scope_revision"]),
        "/internal/agent/tools/hypotheses", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "record_evidence_gaps", "Persist concrete missing facts. Use required_fact for the missing observation and next_best_action for its resolution.",
        _object({
            **_CASE,
            "gaps": {"type": "array", "minItems": 1, "maxItems": 30, "items": _object({
                "gap_id": {"type": "string"},
                "required_fact": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["OPEN", "BLOCKING", "RESOLVED"]},
                "blocked_claim": {"type": "string"},
                "target": {"type": "string"},
                "reason_code": {"type": "string"},
                "observed_evidence": {"type": "array", "items": {"type": "string"}},
                "conflicting_evidence_refs": {"type": "array", "items": {"type": "string"}},
                "what_it_supports": {"type": "string"},
                "what_it_does_not_support": {"type": "string"},
                "retryable": {"type": "boolean"},
                "next_best_action": {"type": "string"},
            }, ["required_fact", "status"])},
            "expected_scope_revision": {"type": "integer", "minimum": 1},
        }, ["case_id", "gaps", "expected_scope_revision"]),
        "/internal/agent/tools/evidence-gaps", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "propose_causal_graph", "Propose a bounded causal graph only when Evidence establishes a causal mechanism. Dependency-only observations stay in the DependencyGraph and must not create an UNKNOWN causal node. Nodes require node_id, mechanism and a closed role; edges require source_node_id, target_node_id and a closed relation. Evidence references are verified server-side.",
        _object({
            **_CASE,
            "nodes": {
                "type": "array", "minItems": 1, "maxItems": 60,
                "items": _object({
                    "node_id": {"type": "string", "minLength": 1},
                    "entity_ref": {"type": "string"},
                    "mechanism": {"type": "string", "minLength": 1},
                    "role": {"type": "string", "enum": [
                        "PRIMARY_CAUSE", "PRIMARY_ROOT_CAUSE", "CONTRIBUTING_FACTOR",
                        "AMPLIFIER", "PROPAGATED_EFFECT", "SYMPTOM",
                        "COINCIDENTAL_ANOMALY", "UNKNOWN",
                    ]},
                    "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "opposing_evidence_refs": {"type": "array", "items": {"type": "string"}},
                }, ["node_id", "mechanism", "role"]),
            },
            "edges": {
                "type": "array", "maxItems": 120,
                "items": _object({
                    "source_node_id": {"type": "string", "minLength": 1},
                    "target_node_id": {"type": "string", "minLength": 1},
                    "relation": {"type": "string", "enum": [
                        "CAUSES", "CONTRIBUTES_TO", "AMPLIFIES",
                        "PROPAGATES_TO", "CORRELATES_WITH",
                    ]},
                    "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}},
                }, ["source_node_id", "target_node_id", "relation"]),
            },
            "expected_scope_revision": {"type": "integer", "minimum": 1},
            "expected_evidence_watermark": {"type": "integer", "minimum": 0},
            "investigation_run_id": {"type": "string"},
        }, ["case_id", "nodes", "edges", "expected_scope_revision", "expected_evidence_watermark"]),
        "/internal/agent/tools/causal-graph", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "get_collection_status", "Read accepted/rejected proposals and authoritative CollectionRequests.",
        _object({
            **_CASE,
        }, ["case_id"]),
        "/internal/agent/tools/collection-status", "READ_ONLY",
    ),
    ToolSpec(
        "submit_evidence_analysis", "Persist evidence-bound facts after deterministic citation validation.",
        _object({
            **_CASE,
            "analysis_run_id": {"type": "string", "minLength": 1},
            "facts": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "certainty": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                    "citations": {"type": "array", "minItems": 1, "items": {
                        "type": "object", "additionalProperties": True,
                        "properties": {
                            "evidence_id": {"type": "string", "minLength": 1},
                            "projection_hash": {"type": "string", "minLength": 1},
                            "field_path": {"type": "string", "minLength": 1},
                        },
                        "required": ["evidence_id", "projection_hash", "field_path"],
                    }},
                },
                "required": ["claim", "certainty", "citations"],
            }},
            "anomalies": {"type": "array", "items": {"type": "object"}},
            "interpretations": {"type": "array", "items": {"type": "object"}},
            "conflicts": {"type": "array", "items": {"type": "object"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "next_collection_proposals": {"type": "array", "items": {"type": "object"}},
            "token_usage": {"type": "object"},
            "latency_ms": {"type": "integer", "minimum": 0},
        }, ["case_id", "analysis_run_id", "facts"]),
        "/internal/agent/tools/evidence-analysis", "PROPOSE_ONLY",
    ),
    ToolSpec(
        "get_evidence_analyses", "Read persisted EvidenceAnalysisRuns and stale-input state.",
        _object({**_CASE, "evidence_id": {"type": "string"}}, ["case_id"]),
        "/internal/agent/tools/evidence-analyses", "READ_ONLY",
    ),
    ToolSpec(
        "finish_investigation", "Submit an evidence-bound conclusion for deterministic verification. Recommendations are optional; when present each requires concrete_action, target and cause_or_edge_ref.",
        _object({
            **_CASE, **_EVIDENCE_IDS,
            "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 50},
            "summary": {"type": "string", "minLength": 1},
            "state": {
                "type": "string",
                "enum": ["CONFIRMED", "PARTIALLY_CONFIRMED", "INSUFFICIENT_EVIDENCE"],
            },
            "claims": {"type": "array", "items": _object({
                "claim": {"type": "string"},
                "evidence_id": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "projection_hash": {"type": "string"},
                "field_path": {"type": "string"},
                "predicate": {"type": "object"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            })},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "abstention_reason": {"type": "string"},
            "primary_root_causes": {"type": "array", "items": {"type": "object"}},
            "contributing_factors": {"type": "array", "items": {"type": "object"}},
            "amplifiers": {"type": "array", "items": {"type": "object"}},
            "propagated_effects": {"type": "array", "items": {"type": "object"}},
            "symptoms": {"type": "array", "items": {"type": "object"}},
            "coincidental_anomalies": {"type": "array", "items": {"type": "object"}},
            "ruled_out": {"type": "array", "items": {"type": "object"}},
            "recommendations": {
                "type": "array", "maxItems": 20,
                "items": _object({
                    "recommendation_id": {"type": "string"},
                    "cause_or_edge_ref": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                    "concrete_action": {"type": "string", "minLength": 1},
                    "rationale": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "verification_operations": {"type": "array", "items": {"type": "string"}},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "rollback_or_failure_condition": {"type": "string"},
                }, ["cause_or_edge_ref", "target", "concrete_action"]),
            },
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
