"""v6 machine policy and deterministic verification helpers.

This module owns turn disposition routing, READ_ONLY tool enforcement and the
factual part of CausalGraphVerifier/ReportVerifier.  The model may propose
roles and edges; these functions decide the persisted verification state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from server.app.agent_runtime.catalog import (
    PROPOSE_ONLY_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
)
from server.app.agent_runtime.policy import RuntimePolicy, resolve_runtime_policy

READ_ONLY_TOOLS = set(READ_ONLY_TOOL_NAMES)

PROPOSE_ONLY_TOOLS = set(PROPOSE_ONLY_TOOL_NAMES)

ALLOWED_DISPOSITIONS = {
    "ANSWER_ONLY", "ATTACH_EVIDENCE", "INVESTIGATE", "CORRECT_CONTEXT",
    "CONTROL", "DEPLOYMENT_ASSESSMENT",
}

POLICIES = {"READ_ONLY", "PROPOSE_ONLY", "AUTO_READ_LOW"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def route_disposition(
    message: str,
    *,
    requested_disposition: str | None = None,
    execute_safe_tools: bool = False,
    case_state: str = "OPEN",
) -> tuple[str, str, bool]:
    """Return (disposition, side_effect_policy, needs_user_confirmation)."""
    if requested_disposition in ALLOWED_DISPOSITIONS:
        disposition = requested_disposition
    else:
        normalized = " ".join(str(message or "").lower().split())
        if any(marker in normalized for marker in ("为什么", "这张图", "这条证据", "解释", "说明什么", "判断证据", "只解释", "基于这些数据解释")):
            disposition = "ANSWER_ONLY"
        elif any(marker in normalized for marker in ("暂停", "恢复", "停止", "取消", "重排", "禁用", "纠正", "改成", "排除", "降低")):
            disposition = "CONTROL"
        elif any(marker in normalized for marker in ("部署", "容量", "承载", "扩容")):
            disposition = "DEPLOYMENT_ASSESSMENT"
        else:
            disposition = "INVESTIGATE"

    if disposition == "ANSWER_ONLY":
        return disposition, "READ_ONLY", False
    if disposition in {"CONTROL", "CORRECT_CONTEXT"}:
        return disposition, "READ_ONLY", False
    if disposition == "ATTACH_EVIDENCE":
        return disposition, "READ_ONLY", True
    if disposition == "DEPLOYMENT_ASSESSMENT":
        return disposition, "PROPOSE_ONLY", True
    if disposition == "INVESTIGATE":
        policy = "AUTO_READ_LOW" if execute_safe_tools else "PROPOSE_ONLY"
        return disposition, policy, not execute_safe_tools
    return disposition, "READ_ONLY", True


def tool_policy_error(tool_name: str, policy: str | RuntimePolicy | dict[str, Any]) -> str | None:
    try:
        resolved = (
            policy if isinstance(policy, RuntimePolicy)
            else resolve_runtime_policy(
                {"side_effect_policy": policy} if isinstance(policy, str) else policy,
            )
        )
    except ValueError:
        return "RUNTIME_POLICY_INVALID"
    if not resolved.allows_tool(tool_name):
        if resolved.side_effect_policy == "READ_ONLY":
            return "TURN_READ_ONLY"
        if resolved.side_effect_policy == "PROPOSE_ONLY":
            return "TURN_PROPOSE_ONLY"
        return "TOOL_DISABLED_BY_RUNTIME_POLICY"
    return None


def field_matches_projection(content: dict[str, Any], field_path: str | None, expected: Any = None) -> tuple[bool, Any]:
    """Resolve dotted field_path inside a projection content dict."""
    if not field_path:
        return False, None
    current: Any = content
    for part in field_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    if expected is None:
        return True, current
    try:
        op = str(expected.get("operator") or "eq")
        target = expected.get("value")
        if op == "gte":
            return float(current) >= float(target), current
        if op == "gt":
            return float(current) > float(target), current
        if op == "lte":
            return float(current) <= float(target), current
        if op == "lt":
            return float(current) < float(target), current
        if op == "eq":
            return current == target, current
        if op == "neq":
            return current != target, current
        if op == "in":
            return current in target, current
        return current == target, current
    except (TypeError, ValueError):
        return False, current


def verify_claim_binding(
    evidence: dict[str, Any],
    projections: list[dict[str, Any]],
    claim: dict[str, Any],
) -> tuple[bool, str]:
    """Factual verification of one ClaimEvidenceBinding."""
    evidence_id = claim.get("evidence_id")
    if not evidence_id or evidence.get("status") not in {"ACTIVE"}:
        return False, "EVIDENCE_NOT_ACTIVE"
    if evidence.get("stale_for_current_revision"):
        return False, "EVIDENCE_REVISION_STALE"
    projection_hash = claim.get("projection_hash")
    matching = [
        item for item in projections
        if item.get("evidence_id") == evidence_id
        and item.get("projection_hash") == projection_hash
    ]
    if not matching:
        return False, "PROJECTION_HASH_NOT_FOUND"
    if claim.get("projection_version") is not None:
        if int(claim["projection_version"]) != int(matching[0].get("projection_version") or 0):
            return False, "PROJECTION_VERSION_MISMATCH"
    for field, code in (
        ("target_ref", "TARGET_REF_MISMATCH"),
        ("resource_incarnation", "RESOURCE_INCARNATION_MISMATCH"),
    ):
        supplied = claim.get(field)
        if supplied is not None and str(supplied) != str(evidence.get(field) or ""):
            return False, code
    requested_window = claim.get("event_window") or {}
    evidence_window = evidence.get("time_window") or {}
    if requested_window:
        if any(
            requested_window.get(key) is not None
            and str(requested_window.get(key)) != str(evidence_window.get(key))
            for key in ("start", "end")
        ):
            return False, "EVENT_WINDOW_MISMATCH"
    field_path = claim.get("field_path")
    if field_path:
        predicate = claim.get("predicate") or {}
        ok, observed = field_matches_projection(matching[0].get("content") or {}, field_path, predicate)
        if not ok:
            return False, "PROJECTION_PREDICATE_FAILED"
        claim["observed_value"] = {"field_path": field_path, "value": observed}
    claim["verifier_result"] = "VERIFIED"
    return True, "VERIFIED"


def verify_primary_confirmation(
    graph: dict[str, Any],
    conclusion_state: str,
    *,
    blocker_gaps: int = 0,
    required_edge_missing: int = 0,
    alternative_primary_not_distinguished: bool = False,
) -> str:
    """Machine downgrade rule; Verifier owns final state, never model payload."""
    if conclusion_state != "CONFIRMED":
        return conclusion_state
    if blocker_gaps or required_edge_missing or alternative_primary_not_distinguished:
        return "PARTIALLY_CONFIRMED"
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    primary = [
        node for node in nodes
        if node.get("verifier_role") in {"PRIMARY_CAUSE", "PRIMARY_ROOT_CAUSE"}
    ]
    required = [edge for edge in edges if edge.get("verification_state") not in {"OBSERVED", "SUPPORTED"}]
    if not primary or required:
        return "PARTIALLY_CONFIRMED"
    return "CONFIRMED"


def side_effect_delta(
    repo: Any,
    case_id: str,
    tenant_id: str,
    *,
    plan_revision: int = 0,
    campaign_revision: int = 0,
    task_count: int = 0,
    source_call_count: int = 0,
    execution_unit_count: int = 0,
    acquisition_wakeup_count: int = 0,
) -> dict[str, Any]:
    """Computed side-effect counters for a Turn's machine assertion."""
    return {
        "plan_revision_delta": int(plan_revision or 0),
        "campaign_revision_delta": int(campaign_revision or 0),
        "execution_unit_delta": int(execution_unit_count or 0),
        "task_delta": int(task_count or 0),
        "source_call_delta": int(source_call_count or 0),
        "acquisition_wakeup_delta": int(acquisition_wakeup_count or 0),
    }
