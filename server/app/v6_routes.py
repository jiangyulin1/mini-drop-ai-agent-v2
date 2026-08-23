"""v6 Agent HTTP surface exposed through an explicit router."""

from __future__ import annotations

import hashlib
import json as _json
import math
import os
import re
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from mini_drop_contracts import (
    catalog_payload as collector_catalog_payload,
    get_collector_spec,
)
from server.app.agent_runtime.config import agent_max_active_cases
from server.app.agent_runtime.catalog import get_tool_spec, tool_catalog_payload
from server.app.agent_runtime.options import (
    RuntimeOptions,
    resolve_runtime_options,
)
from server.app.agent_runtime.policy import (
    RuntimePolicy,
    constrain_side_effect_policy,
    resolve_runtime_policy,
)
from server.app.agent_runtime.port import CaseContextSnapshot
from server.app.application.task_views import task_view as _task_view
from server.app.diagnosis.actuation import ActuationError, enforce_runtime_execution_policy
from server.app.diagnosis.collection_supervisor import CollectionSupervisor
from server.app.diagnosis.investigation_plan import PlanUpdateInput
from server.app.diagnosis.knowledge import retrieve_knowledge
from server.app.diagnosis.knowledge_memory import retrieve_case_memory, retrieve_user_knowledge
from server.app.diagnosis.network_discovery import case_dependency_graph_snapshot
from server.app.diagnosis.query_registry import QUERY_REGISTRY
from server.app.diagnosis.skill_registry import SKILL_REGISTRY
from server.app.diagnosis.strategies.registry import get_strategy
from server.app.diagnosis.v6_policy import READ_ONLY_TOOLS
from server.app.agent_runtime.shadow import (
    build_deterministic_plan,
    compare_plans,
    request_shadow_plan,
)
from server.app.http.auth import (
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.logging_utils import log_event
from server.app.routes.topology_discovery import (
    AdvanceTopologyDiscoveryRequest,
    StartTopologyDiscoveryRequest,
    advance_topology_discovery_run,
    start_topology_discovery_run,
)
from server.app.runtime_services import (
    case_evidence_service,
    collection_supervisor,
    diagnosis_orchestrator,
    evidence_analysis_service,
    evidence_attachment_service,
    investigation_plan_service,
    investigation_state_service,
    repo,
)
from server.app.schemas import APIResponse, CreateTaskRequest
from server.app.diagnosis.v6_policy import (
    tool_policy_error,
    verify_claim_binding,
    verify_primary_confirmation,
)


router = APIRouter()


def _resolve_query_target(
    case: dict[str, Any],
    target_ref: str,
) -> dict[str, Any] | None:
    instances = (case.get("target_scope") or {}).get("instances") or []
    if target_ref:
        for item in instances:
            if target_ref in {
                str(item.get("instance_id") or ""),
                str(item.get("agent_id") or ""),
                str(item.get("pid") or ""),
            }:
                return {
                    "agent_id": str(item.get("agent_id") or ""),
                    "pid": int(item.get("pid") or 1),
                }
    for item in instances:
        return {
            "agent_id": str(item.get("agent_id") or ""),
            "pid": int(item.get("pid") or 1),
        }
    for agent in getattr(repo, "agents", {}).values():
        if isinstance(agent, dict):
            agent_id = str(agent.get("agent_id") or agent.get("id") or "")
            status = str(agent.get("status") or "ONLINE")
        else:
            agent_id = str(getattr(agent, "id", "") or "")
            status = str(getattr(agent, "status", "") or "ONLINE")
        if status == "ONLINE" and agent_id:
            return {"agent_id": agent_id, "pid": 1}
    return None


class QueryError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _create_case_query_task(
    case: dict[str, Any],
    tenant_id: str,
    principal_id: str,
    operation_id: str,
    parameters: dict[str, Any],
    *,
    idempotency_key: str | None = None,
):
    operation = QUERY_REGISTRY.get(operation_id)
    if operation is None:
        raise QueryError(400, "UNKNOWN_QUERY_OPERATION")
    errors = QUERY_REGISTRY.validate_parameters(operation_id, parameters)
    if errors:
        raise QueryError(400, f"INVALID_QUERY_PARAMETERS:{','.join(errors)}")
    for forbidden in ("executable", "cwd", "env", "argv", "shell"):
        if forbidden in parameters:
            raise QueryError(400, f"FORBIDDEN_QUERY_PARAM:{forbidden}")
    target_ref = str(parameters.get("target_ref") or "")
    target = _resolve_query_target(case, target_ref)
    if target is None:
        raise QueryError(409, "QUERY_TARGET_UNAVAILABLE")
    case_id = str(case.get("case_id") or "")
    task = repo.create_task(
        CreateTaskRequest(
            name=f"query:{operation_id}:{target['agent_id']}"[:120],
            agent_id=target["agent_id"],
            target_pid=target["pid"],
            collector_type=operation.collector_id,
            sample_rate=operation.default_sample_rate,
            duration_sec=operation.default_duration_sec,
            options={
                "source": "query_gateway",
                "case_id": case_id,
                "tenant_id": tenant_id,
                "risk": operation.risk,
                "query_operation": operation_id,
                "target_ref": target_ref,
                "created_by": principal_id,
            },
        ),
        idempotency_key=idempotency_key,
    )
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="case_query_task_created",
        payload={
            "task_id": task.id,
            "operation": operation_id,
            "collector_id": operation.collector_id,
            "risk": operation.risk,
            "agent_id": target["agent_id"],
        },
        actor_id=principal_id,
    )
    return task, operation_id
def _build_runtime_case_context(
    case: dict[str, Any],
    tenant_id: str,
    *,
    disposition: str = "INVESTIGATE",
    side_effect_policy: str = "AUTO_READ_LOW",
    turn_id: str | None = None,
    investigation_run_id: str | None = None,
    runtime_policy: RuntimePolicy | dict[str, Any] | None = None,
    runtime_options: RuntimeOptions | dict[str, Any] | None = None,
    strategy_id: str | None = None,
    intervention: dict[str, Any] | None = None,
) -> CaseContextSnapshot:
    """Build the L0/L1 Case projection handed to an AgentRuntimePort before a Turn."""
    case_id = str(case.get("case_id") or "")
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
    evidence_summary: list[dict[str, Any]] = []
    all_projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(
        repo, "list_evidence_projections",
    ) else []
    canonical_evidence = case_evidence_service.list_evidence(case_id, tenant_id, status=None)
    for item in canonical_evidence:
        if str(item.get("status") or item.get("lifecycle_status") or "ACTIVE").upper() in {
            "EXCLUDED", "SUPERSEDED", "INVALID",
        } or str(item.get("lifecycle_status") or "").upper() in {
            "EXCLUDED", "SUPERSEDED", "INVALID",
        }:
            continue
        projections = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=item.get("evidence_id"),
        ) if hasattr(repo, "list_evidence_projections") else []
        # Projection rows are persisted oldest-first. A runtime snapshot must
        # expose the newest row for each kind, otherwise post-review turns can
        # receive stale hashes and lifecycle state.
        latest_by_kind: dict[str, dict[str, Any]] = {}
        for projection in projections:
            kind = str(projection.get("projection_kind") or "default")
            latest_by_kind[kind] = projection
        for projection in sorted(
            latest_by_kind.values(),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        ):
            content = projection.get("content") or {}
            evidence_summary.append({
                "evidence_id": item.get("evidence_id"),
                "artifact_type": item.get("artifact_type"),
                "projection_kind": projection.get("projection_kind"),
                "projection_hash": projection.get("projection_hash"),
                "summary": content.get("summary", ""),
                "signals": content.get("signals") or {},
                "target_ref": item.get("target_ref"),
                "status": item.get("status"),
                "lifecycle_status": item.get("lifecycle_status") or item.get("status") or "ACTIVE",
                "trust_state": item.get("review_trust_state") or "UNREVIEWED",
                "review_revision": int(item.get("review_revision") or 0),
                "freshness": item.get("freshness"),
                "quality": item.get("quality"),
                "time_window": item.get("time_window") or {},
                "truncated": projection.get("truncated", False),
            })
    for attachment in evidence_attachment_service.list_attachments(case_id, tenant_id):
        if attachment.get("status") in {"EXCLUDED_BY_USER", "SUPERSEDED"}:
            continue
        evidence_summary.append({
            "attachment_id": attachment.get("attachment_id"),
            "resource_ref": attachment.get("resource_ref"),
            "evidence_ids": attachment.get("evidence_ids") or [],
            "status": attachment.get("status"),
            "freshness": attachment.get("freshness"),
            "quality": attachment.get("quality"),
        })
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    collection_proposals = repo.list_collection_proposals(case_id, tenant_id)
    collection_requests = repo.list_collection_requests(case_id, tenant_id)
    evidence_analyses = repo.list_evidence_analysis_runs(case_id, tenant_id)
    investigation_state = investigation_state_service.snapshot(case_id, tenant_id)
    hypothesis_graph = investigation_state["hypothesis_graph"]
    active_evidence_ids = {
        str(item.get("evidence_id")) for item in evidence_summary if item.get("evidence_id")
    }
    hypotheses = []
    for original in hypothesis_graph.get("hypotheses") or []:
        item = dict(original)
        support = [str(ref) for ref in item.get("supporting_evidence_refs") or []]
        contradiction = [str(ref) for ref in item.get("contradicting_evidence_refs") or []]
        item["invalidated_evidence_refs"] = sorted(
            set(support + contradiction) - active_evidence_ids,
        )
        item["supporting_evidence_refs"] = [ref for ref in support if ref in active_evidence_ids]
        item["contradicting_evidence_refs"] = [ref for ref in contradiction if ref in active_evidence_ids]
        hypotheses.append(item)
    current_support = [
        {
            "hypothesis_id": item.get("hypothesis_id"),
            "evidence_refs": list(item.get("supporting_evidence_refs") or []),
        }
        for item in hypotheses
        if item.get("supporting_evidence_refs")
    ]
    counterevidence = [
        {
            "hypothesis_id": item.get("hypothesis_id"),
            "evidence_refs": list(item.get("contradicting_evidence_refs") or []),
        }
        for item in hypotheses
        if item.get("contradicting_evidence_refs")
    ]
    sanitized_hypothesis_edges = []
    for edge in hypothesis_graph.get("edges") or []:
        edge_view = dict(edge)
        if "supporting_evidence_refs" in edge_view:
            edge_view["supporting_evidence_refs"] = [
                str(ref) for ref in edge_view.get("supporting_evidence_refs") or []
                if str(ref) in active_evidence_ids
            ]
        sanitized_hypothesis_edges.append(edge_view)
    causal_graph_view = dict(investigation_state["causal_graph"] or {})
    for key in ("nodes", "edges"):
        causal_items = []
        for original in causal_graph_view.get(key) or []:
            item = dict(original)
            for ref_key in ("supporting_evidence_refs", "opposing_evidence_refs"):
                if ref_key in item:
                    item[ref_key] = [
                        str(ref) for ref in item.get(ref_key) or []
                        if str(ref) in active_evidence_ids
                    ]
            causal_items.append(item)
        if key in causal_graph_view:
            causal_graph_view[key] = causal_items
    recommendations_view = []
    for original in investigation_state["recommendations"][:20]:
        item = dict(original)
        refs = [str(ref) for ref in item.get("evidence_refs") or []]
        item["invalidated_evidence_refs"] = sorted(set(refs) - active_evidence_ids)
        item["evidence_refs"] = [ref for ref in refs if ref in active_evidence_ids]
        recommendations_view.append(item)
    evidence_gaps = investigation_state["evidence_gaps"]
    information_goals: list[str] = []
    for analysis in evidence_analyses:
        if analysis.get("status") != "COMPLETED" or analysis.get("input_state") != "CURRENT":
            continue
        for item in analysis.get("next_collection_proposals") or []:
            goal = str(item.get("information_goal") if isinstance(item, dict) else item).strip()
            if goal and goal not in information_goals:
                information_goals.append(goal)
    missing_facts: list[str] = []
    for gap in evidence_gaps:
        if str(gap.get("status") or "") in {"OPEN", "BLOCKING"}:
            fact = str(gap.get("required_fact") or "").strip()
            if fact and fact not in missing_facts:
                missing_facts.append(fact)
    for hypothesis in hypotheses:
        for fact in hypothesis.get("missing_evidence") or []:
            fact = str(fact).strip()
            if fact and fact not in missing_facts:
                missing_facts.append(fact)
    for goal in information_goals:
        if goal not in missing_facts:
            missing_facts.append(goal)
    resolved_policy = constrain_side_effect_policy(
        resolve_runtime_policy(runtime_policy), side_effect_policy,
    )
    experimental = resolved_policy.execution_mode in {"dry_run", "sandbox"}
    resolved_options = resolve_runtime_options(runtime_options, experiment_mode=experimental)
    strategy_id = strategy_id or resolved_options.strategy_id
    strategy = get_strategy(strategy_id)
    skill_context = SKILL_REGISTRY.select_skills(
        goal=str(case.get("problem_description") or ""),
        target_scope=case.get("target_scope") or {},
        evidence_summary=evidence_summary,
        missing_facts=missing_facts,
    )
    tool_catalog = sorted(resolved_policy.effective_tools())
    consumed_duration = sum(
        CollectionSupervisor._reserved_duration(item) for item in collection_requests
    )
    running_task_ids = [
        str(item["task_id"])
        for item in collection_requests
        if item.get("task_id") and item.get("status") in {"ACCEPTED", "DISPATCHED", "RUNNING"}
    ]
    analysis_summary = [
        {
            "analysis_run_id": item.get("analysis_run_id"),
            "status": item.get("status"),
            "input_state": item.get("input_state"),
            "evidence_inputs": item.get("evidence_inputs") or [],
            "facts": (item.get("facts") or [])[:10],
            "conflicts": (item.get("conflicts") or [])[:10],
            "limitations": (item.get("limitations") or [])[:10],
            "next_collection_proposals": (item.get("next_collection_proposals") or [])[:10],
        }
        for item in evidence_analyses[-10:]
    ]
    return CaseContextSnapshot(
        case_id=case_id,
        tenant_id=tenant_id,
        case_goal=str(case.get("problem_description") or "")[:500],
        target_scope=case.get("target_scope") or {},
        autonomy_mode=str(case.get("run_mode") or "COLLABORATE"),
        case_command_revision=int(case.get("case_command_revision") or 1),
        control_revision=int(case.get("control_revision") or 1),
        plan_revision=int(plan.get("plan_revision") or 0),
        scope_revision=int(case.get("scope_revision") or 1),
        campaign_revision=0,
        evidence_watermark=len(all_projections),
        investigation_run_id=investigation_run_id,
        turn_id=turn_id,
        disposition=disposition,
        side_effect_policy=resolved_policy.side_effect_policy,
        diagnostic_strategy_id=strategy_id,
        strategy_params=resolved_options.strategy_params,
        strategy_guidance=strategy.render_prompt_guidance(),
        runtime_policy=resolved_policy.audit_summary(),
        runtime_options=resolved_options.audit_summary(),
        context_snapshot_id=None,
        runtime_generation=int(binding.get("runtime_generation") or 1) if binding else 1,
        runtime_session_id=str(binding.get("runtime_session_id") or "") if binding else "",
        collection_proposals=collection_proposals[-20:],
        collection_requests=collection_requests[-20:],
        evidence_analyses=analysis_summary,
        information_goals=information_goals[:20],
        hypotheses=hypotheses[:30],
        hypothesis_edges=sanitized_hypothesis_edges[:60],
        evidence_gaps=evidence_gaps[:30],
        causal_graph=causal_graph_view,
        conclusion=investigation_state["conclusion"] or {},
        recommendations=recommendations_view,
        evidence_summary=evidence_summary[:20],
        missing_facts=missing_facts[:20],
        running_task_ids=running_task_ids,
        budget={
            "max_active_cases": agent_max_active_cases(),
            "max_collection_requests": resolved_policy.max_collection_requests,
            "used_collection_requests": len(collection_requests),
            "remaining_collection_requests": max(
                0, resolved_policy.max_collection_requests - len(collection_requests),
            ),
            "max_collection_duration_sec": resolved_policy.max_collection_duration_sec,
            "reserved_collection_duration_sec": consumed_duration,
            "remaining_collection_duration_sec": max(
                0, resolved_policy.max_collection_duration_sec - consumed_duration,
            ),
        },
        recent_user_commands=[],
        tool_catalog_summary=tool_catalog,
        # Knowledge and retrospective memory are retrieved on demand through
        # search_knowledge. No document body is copied into every Pi prompt.
        knowledge_context=[],
        skill_context=skill_context,
        investigation_directive={
            "kind": "EVIDENCE_NATIVE_SUPERVISED_INVESTIGATOR",
            "authority": "AI proposes; deterministic gateway validates and dispatches",
            "next_information_goals": missing_facts[:20],
            "stop_conditions": [
                "evidence_sufficient",
                "collection_budget_exhausted",
                "duplicate_collection_would_add_no_information",
                "no_new_evidence_after_two_cycles",
                "scope_or_approval_required",
            ],
        },
        intervention=intervention or {},
        current_support=current_support[:30],
        counterevidence=counterevidence[:30],
    )
def _case_investigation_footprint(case_id: str, tenant_id: str) -> dict[str, int]:
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
    tasks = [
        task for task in getattr(repo, "tasks", {}).values()
        if ((getattr(task, "request_params", None) or {}).get("options") or {}).get("case_id") == case_id
    ]
    fanout_runs = repo.list_fanout_runs(case_id, tenant_id)
    return {
        "plan_revision": int(plan.get("plan_revision") or 0),
        "plan_step_count": len(plan.get("steps") or []),
        "case_task_count": len(tasks),
        "fanout_run_count": len(fanout_runs),
    }
# ── E3 内部 Tool Gateway（仅 Pi Sidecar 经内部 Token 调用）───────────
# 模型只能通过这些只读投影与受控计划写入触达 Mini-Drop；不能构造 URL/SQL/Shell。


def _require_internal_token(request: Request) -> None:
    expected = os.getenv("MINI_DROP_PI_INTERNAL_TOKEN", "")
    supplied = request.headers.get("X-Internal-Token", "")
    if not expected or supplied != expected:
        raise HTTPException(status_code=401, detail="INTERNAL_TOKEN_REQUIRED")


@router.get("/internal/agent/tools/catalog")
def internal_tool_catalog(request: Request) -> APIResponse:
    """Trusted Sidecar discovery surface; metadata never grants permission."""
    _require_internal_token(request)
    return APIResponse(data=tool_catalog_payload(include_internal_path=True))


def _tool_fence(
    case: dict[str, Any],
    tenant_id: str,
    payload: dict[str, Any],
    tool_name: str,
    *,
    read_only_tools: set[str] | None = None,
) -> str | None:
    """Machine-level Tool Gateway fence.  Returns a rejection code or None."""
    read_only_tools = READ_ONLY_TOOLS if read_only_tools is None else read_only_tools
    case_id = str(case.get("case_id") or "")
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    # The active Turn policy arrives in the Sidecar Tool Envelope.  Inferring
    # policy from the most recent persisted turn would fence legitimate later
    # turns; a missing envelope field is therefore treated as legacy
    # AUTO_READ_LOW for backward compatibility.
    if get_tool_spec(tool_name) is None:
        return "TOOL_NOT_REGISTERED"
    try:
        policy = resolve_runtime_policy(
            payload.get("runtime_policy") or {
                "side_effect_policy": str(payload.get("side_effect_policy") or "AUTO_READ_LOW"),
            },
        )
    except ValueError:
        return "RUNTIME_POLICY_INVALID"
    policy_error = tool_policy_error(tool_name, policy)
    if policy_error:
        return policy_error
    if tool_name not in read_only_tools and policy.execution_mode == "deny_write":
        return "WRITE_DENIED_BY_RUNTIME_POLICY"
    # An intervention is a protocol barrier, not merely a write permission.
    # The first model action must acknowledge the exact intervention before it
    # can read or mutate through any other tool.
    if tool_name != "acknowledge_intervention":
        intervention_id = str(payload.get("intervention_id") or "").strip()
        if intervention_id:
            acknowledged = False
            for event in repo.list_case_events(case_id, tenant_id, limit=500) or []:
                if event.get("event_type") != "intervention_acknowledged":
                    continue
                event_payload = event.get("payload") or {}
                if str(event_payload.get("intervention_id") or "") == intervention_id:
                    acknowledged = bool(event_payload.get("evidence_state_rechecked"))
            if not acknowledged:
                return "INTERVENTION_ACK_REQUIRED"
    supplied_generation = payload.get("runtime_generation")
    if supplied_generation is not None and binding is not None:
        if int(supplied_generation) != int(binding.get("runtime_generation") or 0):
            return "GENERATION_FENCED"
    state = str(case.get("state") or "")
    if tool_name not in read_only_tools:
        if state == "PAUSED":
            return "RUN_PAUSED"
        if state in {"STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
            return "RUN_TERMINAL"
    return None


@router.post("/internal/agent/tools/acknowledge-intervention")
def internal_tool_acknowledge_intervention(payload: dict[str, Any], request: Request) -> APIResponse:
    """Record the mandatory first response to an operator Evidence intervention."""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    intervention_id = str(payload.get("intervention_id") or "").strip()
    trust_state = str(payload.get("trust_state") or "").strip().upper()
    if not intervention_id:
        raise HTTPException(status_code=400, detail="INTERVENTION_ID_REQUIRED")
    if payload.get("evidence_state_rechecked") is not True:
        raise HTTPException(status_code=409, detail="EVIDENCE_STATE_RECHECK_REQUIRED")
    if not trust_state:
        raise HTTPException(status_code=400, detail="TRUST_STATE_REQUIRED")
    if trust_state not in {
        "RECHECK_REQUIRED", "EXCLUDED", "SUPERSEDED", "INVALID", "LOW_TRUST",
        "TRUSTED", "UNREVIEWED", "ACTIVE",
    }:
        raise HTTPException(status_code=409, detail="INTERVENTION_TRUST_STATE_INVALID")
    # Resolve the intervention against the durable wakeup and canonical
    # Evidence.  An arbitrary intervention_id/revision must never be enough
    # to unlock a write-capable tool.
    wakeup_id = intervention_id.removeprefix("intervention-")
    wakeup = None
    if hasattr(repo, "list_runtime_wakeups"):
        wakeup = next(
            (item for item in repo.list_runtime_wakeups(case_id, tenant_id)
             if str(item.get("wakeup_id") or "") == wakeup_id),
            None,
        )
    if wakeup is None or str(wakeup.get("reason_class") or "") not in {
        "EVIDENCE_REVIEWED", "EVIDENCE_ELIGIBILITY_CHANGED",
    }:
        raise HTTPException(status_code=409, detail="INTERVENTION_NOT_CURRENT")
    affected_ids: list[str] = []
    expected_revisions: dict[str, int] = {}
    for ref in wakeup.get("source_refs") or []:
        raw = str(ref)
        evidence_id = raw
        revision = None
        if raw.startswith("evidence-review:"):
            evidence_id = raw[len("evidence-review:"):].split(":r", 1)[0]
            try:
                revision = int(raw.rsplit(":r", 1)[1])
            except (IndexError, ValueError):
                revision = None
        elif raw.startswith("evidence:"):
            # Governance outbox events use the canonical evidence:<id> form;
            # unlike the legacy wakeup reference it carries no revision, so
            # the canonical row is checked below and the current revision is
            # used for the ACK contract.
            evidence_id = raw[len("evidence:"):].split(":", 1)[0]
        if evidence_id and evidence_id not in affected_ids:
            affected_ids.append(evidence_id)
        if revision is not None:
            expected_revisions[evidence_id] = revision
    evidence_rows = {
        str(item.get("evidence_id")): item
        for item in case_evidence_service.list_evidence(case_id, tenant_id)
    }
    if not affected_ids or any(item not in evidence_rows for item in affected_ids):
        raise HTTPException(status_code=409, detail="INTERVENTION_EVIDENCE_NOT_FOUND")
    actual_revisions = [
        int(evidence_rows[item].get("review_revision") or 0) for item in affected_ids
    ]
    observed_states = {
        state
        for evidence_id in affected_ids
        for state in (
            str(evidence_rows[evidence_id].get("status") or "").upper(),
            str(evidence_rows[evidence_id].get("lifecycle_status") or "").upper(),
            str(evidence_rows[evidence_id].get("review_trust_state") or "").upper(),
        )
        if state
    }
    if trust_state != "RECHECK_REQUIRED" and trust_state not in observed_states:
        raise HTTPException(status_code=409, detail="INTERVENTION_TRUST_STATE_MISMATCH")
    for evidence_id, expected in expected_revisions.items():
        if int(evidence_rows[evidence_id].get("review_revision") or 0) != expected:
            raise HTTPException(status_code=409, detail="INTERVENTION_REVISION_STALE")
    revision_after = int(payload.get("revision_after") or 0)
    expected_after = max(actual_revisions or [0])
    if revision_after != expected_after:
        raise HTTPException(status_code=409, detail="INTERVENTION_REVISION_AFTER_MISMATCH")
    revision_before = payload.get("revision_before")
    if revision_before is not None and int(revision_before) != max(0, expected_after - 1):
        raise HTTPException(status_code=409, detail="INTERVENTION_REVISION_BEFORE_MISMATCH")
    # The first tool call is the explicit intervention ACK. The terminal
    # finish gate below requires durable read receipts from both canonical read
    # tools, so this ACK cannot be used to bypass the re-read protocol.
    receipts: set[str] = set()
    if payload.get("runtime_generation") is not None:
        generation = int(payload.get("runtime_generation") or 0)
        for event in repo.list_case_events(case_id, tenant_id, limit=500) or []:
            if event.get("event_type") != "intervention_read_receipt":
                continue
            event_payload = event.get("payload") or {}
            if str(event_payload.get("intervention_id") or "") == intervention_id and int(event_payload.get("runtime_generation") or 0) == generation:
                receipts.add(str(event_payload.get("tool_name") or ""))
    ack = {
        "intervention_id": intervention_id,
        "trust_state": trust_state,
        "evidence_state_rechecked": True,
        "revision_before": revision_before,
        "revision_after": revision_after,
        "affected_evidence_ids": affected_ids,
        "evidence_states": {
            evidence_id: {
                "status": evidence_rows[evidence_id].get("status"),
                "lifecycle_status": evidence_rows[evidence_id].get("lifecycle_status"),
                "review_revision": int(evidence_rows[evidence_id].get("review_revision") or 0),
            }
            for evidence_id in affected_ids
        },
        "actor_id": "mini-drop-pi-runtime",
        "read_receipts": sorted(receipts) if payload.get("runtime_generation") is not None else [],
        "read_receipts_required": ["get_case_snapshot", "list_case_evidence"],
    }
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="intervention_acknowledged",
        payload=ack,
        actor_id="mini-drop-pi-runtime",
    )
    return APIResponse(data={"accepted": True, **ack})


def _runtime_policy_from_tool_payload(payload: dict[str, Any]) -> RuntimePolicy:
    return resolve_runtime_policy(
        payload.get("runtime_policy") or {
            "side_effect_policy": str(payload.get("side_effect_policy") or "AUTO_READ_LOW"),
        },
    )


def _runtime_visible_content(payload: dict[str, Any]) -> str:
    """Extract only user-visible assistant text from a Sidecar event."""
    if payload.get("has_tool_calls"):
        return ""
    visible_text = str(payload.get("visible_text") or "").strip()
    if visible_text:
        return visible_text[:12000]
    for key in ("text", "message", "content"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        if value in {"已提交", "已接受", "accepted"}:
            continue
        if "accepted" in value.lower()[:20]:
            continue
        try:
            parsed = _json.loads(value)
        except Exception:
            if value.startswith('{"role":"assistant"') or value.startswith('{"role": "assistant"'):
                return ""
            return value[:12000]
        if isinstance(parsed, dict):
            role = str(parsed.get("role") or "")
            content = parsed.get("content") or []
            if role == "assistant" and isinstance(content, list):
                texts = [
                    str(item.get("text") or "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
                ]
                if texts:
                    return " ".join(texts)[:12000]
            if isinstance(parsed.get("text"), str) and parsed["text"].strip():
                return parsed["text"].strip()[:12000]
    if payload.get("final") is True:
        value = payload.get("answer") or payload.get("summary")
        if isinstance(value, str) and value.strip():
            return value.strip()[:12000]
    return ""


def _intervention_audit_for_finish(
    case_id: str,
    tenant_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Project the acknowledged intervention into the terminal contract."""
    intervention_id = str(payload.get("intervention_id") or "").strip()
    if not intervention_id:
        return {}
    for event in reversed(repo.list_case_events(case_id, tenant_id, limit=500) or []):
        if event.get("event_type") != "intervention_acknowledged":
            continue
        ack = event.get("payload") or {}
        if str(ack.get("intervention_id") or "") != intervention_id:
            continue
        affected_ids = ack.get("affected_evidence_ids") or []
        validated_receipts = set()
        generation = int(payload.get("runtime_generation") or 0)
        if payload.get("runtime_generation") is not None:
            for receipt_event in repo.list_case_events(case_id, tenant_id, limit=500) or []:
                if receipt_event.get("event_type") != "intervention_read_receipt":
                    continue
                receipt = receipt_event.get("payload") or {}
                if str(receipt.get("intervention_id") or "") == intervention_id and int(receipt.get("runtime_generation") or 0) == generation:
                    validated_receipts.add(str(receipt.get("tool_name") or ""))
        invalidated_claims: set[str] = set()
        remaining_support: dict[str, list[str]] = {}
        for review_event in repo.list_case_events(case_id, tenant_id, limit=500) or []:
            if review_event.get("event_type") != "evidence_reviewed":
                continue
            review_payload = review_event.get("payload") or {}
            if str(review_payload.get("evidence_id") or "") not in {str(item) for item in affected_ids}:
                continue
            propagation = review_payload.get("propagation") or {}
            invalidated_claims.update(str(item) for item in propagation.get("invalidated_claims") or [])
            for claim_id, refs in (propagation.get("remaining_active_support") or {}).items():
                remaining_support[str(claim_id)] = [str(ref) for ref in refs or []]
        return {
            "intervention_ack": True,
            "intervention_id": intervention_id,
            "trust_state": ack.get("trust_state"),
            "evidence_state_rechecked": bool(ack.get("evidence_state_rechecked")),
            "revision_before": ack.get("revision_before"),
            "revision_after": ack.get("revision_after"),
            "excluded_evidence_used": False,
            "invalidated_evidence_ids": ack.get("affected_evidence_ids") or [],
            "invalidated_claims": sorted(invalidated_claims),
            "remaining_active_support": remaining_support,
            "read_receipts_validated": set(("get_case_snapshot", "list_case_evidence")).issubset(validated_receipts)
            if payload.get("runtime_generation") is not None else True,
        }
    return {
        "intervention_ack": False,
        "intervention_id": intervention_id,
        "evidence_state_rechecked": False,
        "excluded_evidence_used": False,
    }


def _business_symptom_missing_direct_evidence(
    graph: dict[str, Any], case_id: str, tenant_id: str,
) -> list[str]:
    """Return business symptoms supported only by indirect infrastructure data."""
    direct_collectors = {
        "connection_probe", "http_probe", "service_probe", "synthetic_probe",
        "log_scan", "request_trace", "distributed_trace",
    }
    symptom_terms = {
        "latency", "slow", "timeout", "error rate", "response time",
        "延迟", "变慢", "超时", "错误率", "响应时间", "接口",
    }
    missing: list[str] = []
    for node in graph.get("nodes") or []:
        role = str(node.get("verifier_role") or node.get("role") or "").upper()
        description = " ".join((
            str(node.get("mechanism") or ""), str(node.get("entity_ref") or ""),
        )).lower()
        if role != "SYMPTOM" or not any(term in description for term in symptom_terms):
            continue
        supported = False
        for evidence_id in node.get("supporting_evidence_refs") or []:
            evidence = repo.get_case_evidence(case_id, tenant_id, str(evidence_id))
            if evidence is None:
                continue
            collector = str(evidence.get("collector_id") or evidence.get("artifact_type") or "").lower()
            if collector in direct_collectors:
                supported = True
                break
            for projection in repo.list_evidence_projections(case_id, tenant_id, str(evidence_id)):
                content_keys = _json.dumps(
                    projection.get("content") or {}, ensure_ascii=False, sort_keys=True,
                ).lower()
                if any(term in content_keys for term in (
                    "latency_ms", "response_time", "http_status", "error_rate", "request_duration",
                )):
                    supported = True
                    break
            if supported:
                break
        if not supported:
            missing.append(str(node.get("node_id") or node.get("mechanism") or "SYMPTOM"))
    return missing


def _visible_evidence_ids(content: str, case_id: str, tenant_id: str) -> list[str]:
    """Return canonical evidence IDs that are both persisted and visibly cited."""
    known = [
        str(item.get("evidence_id") or "")
        for item in repo.list_case_evidence(case_id, tenant_id, status=None)
        if str(item.get("lifecycle_status") or item.get("status") or "ACTIVE").upper()
        not in {"EXCLUDED", "SUPERSEDED", "INVALID"}
    ]
    return [item for item in known if item and item in content]


def _nested_string_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _nested_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_string_values(item)
    elif isinstance(value, str):
        yield value


def _nested_scalar_values(value: Any):
    """Yield meaningful scalar values for the server-side Evidence firewall."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _nested_scalar_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_scalar_values(item)
    elif isinstance(value, str):
        normalized = value.strip()
        # Free-form labels such as "CPU" or "summary" are too common to be
        # useful firewall tokens. Keep distinctive strings that carry a value
        # (usually a metric, identifier, or timestamp) and all non-trivial
        # numeric scalars.
        if len(normalized) >= 6 and any(char.isdigit() for char in normalized):
            yield normalized
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if math.isfinite(numeric) and abs(numeric) >= 10:
            yield str(value)


def _excluded_projection_value_leaks(
    case_id: str,
    tenant_id: str,
    excluded_evidence_ids: set[str],
    conclusion: dict[str, Any],
) -> list[str]:
    """Find excluded projection values repeated in a proposed conclusion."""
    if not excluded_evidence_ids:
        return []
    serialized = _json.dumps(conclusion, ensure_ascii=False, sort_keys=True, default=str)
    leaks: list[str] = []
    for evidence_id in sorted(excluded_evidence_ids):
        projections = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=evidence_id,
        ) if hasattr(repo, "list_evidence_projections") else []
        for projection in projections:
            for value in _nested_scalar_values(projection.get("content") or {}):
                if value.isdigit() or re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                    pattern = rf"(?<!\d){re.escape(value)}(?!\d)"
                    matched = re.search(pattern, serialized)
                else:
                    matched = value in serialized
                if matched:
                    leaks.append(f"{evidence_id}:{value[:80]}")
    return sorted(set(leaks))


@router.post("/internal/agent/tools/case-snapshot")
def internal_tool_case_snapshot(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    intervention_id = str(payload.get("intervention_id") or "").strip()
    runtime_generation = int(payload.get("runtime_generation") or 0)
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {"plan_id": None, "steps": []}
    attachments = evidence_attachment_service.list_attachments(case_id, tenant_id)
    investigation_state = investigation_state_service.snapshot(case_id, tenant_id)
    evidence_items: list[dict[str, Any]] = []
    excluded_evidence: list[dict[str, Any]] = []
    projection_count = 0
    for item in case_evidence_service.list_evidence(case_id, tenant_id):
        if str(item.get("lifecycle_status") or item.get("status") or "ACTIVE").upper() in {
            "EXCLUDED", "SUPERSEDED", "INVALID",
        }:
            # Keep only a non-content audit marker. The Agent must not receive
            # the excluded projection or its original numerical values.
            excluded_evidence.append({
                "evidence_id": item.get("evidence_id"),
                "status": item.get("status"),
                "lifecycle_status": item.get("lifecycle_status"),
                "trust_state": item.get("review_trust_state"),
                "review_revision": item.get("review_revision"),
            })
            continue
        projections = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=item.get("evidence_id"),
        ) if hasattr(repo, "list_evidence_projections") else []
        projection_count += len(projections)
        evidence_items.append({
            "evidence_id": item.get("evidence_id"),
            "artifact_type": item.get("artifact_type"),
            "status": item.get("status"),
            "lifecycle_status": item.get("lifecycle_status") or item.get("status"),
            "trust_state": item.get("review_trust_state") or "UNREVIEWED",
            "review_revision": int(item.get("review_revision") or 0),
            "freshness": item.get("freshness"),
            "quality": item.get("quality"),
            "target_ref": item.get("target_ref"),
            "time_window": item.get("time_window") or {},
            "projections": [
                {
                    "projection_id": projection.get("projection_id"),
                    "projection_kind": projection.get("projection_kind"),
                    "projection_hash": projection.get("projection_hash"),
                    "summary": (projection.get("content") or {}).get("summary"),
                    "signals": (projection.get("content") or {}).get("signals") or {},
                    "top_items": (projection.get("content") or {}).get("top_items") or [],
                    "samples": (projection.get("content") or {}).get("samples") or [],
                    "log_events": (projection.get("content") or {}).get("log_events") or [],
                    "truncated": projection.get("truncated", False),
                }
                for projection in projections
            ],
        })
    active_ids = {str(item.get("evidence_id")) for item in evidence_items if item.get("evidence_id")}
    hypothesis_view = dict(investigation_state["hypothesis_graph"] or {})
    hypothesis_view["hypotheses"] = [
        {
            **node,
            "supporting_evidence_refs": [
                ref for ref in node.get("supporting_evidence_refs") or [] if str(ref) in active_ids
            ],
            "contradicting_evidence_refs": [
                ref for ref in node.get("contradicting_evidence_refs") or [] if str(ref) in active_ids
            ],
        }
        for node in hypothesis_view.get("hypotheses") or []
    ]
    causal_view = dict(investigation_state["causal_graph"] or {})
    for key in ("nodes", "edges"):
        causal_view[key] = [
            {
                **item,
                "supporting_evidence_refs": [
                    ref for ref in item.get("supporting_evidence_refs") or [] if str(ref) in active_ids
                ],
                "opposing_evidence_refs": [
                    ref for ref in item.get("opposing_evidence_refs") or [] if str(ref) in active_ids
                ],
            }
            for item in causal_view.get(key) or []
        ]
    conclusion_view = investigation_state["conclusion"]
    if isinstance(conclusion_view, dict):
        stale_claim = any(
            str(claim.get("evidence_id") or "") not in active_ids
            for claim in conclusion_view.get("claims") or []
            if claim.get("evidence_id")
        )
        if stale_claim:
            conclusion_view = {
                "conclusion_id": conclusion_view.get("conclusion_id"),
                "revision": conclusion_view.get("revision"),
                "state": "RECHECK_REQUIRED",
                "report_text": "结论引用的 Evidence 生命周期已变化，请重新读取 ACTIVE Evidence。",
            }
    propagation = {
        "invalidated_claims": list((conclusion_view or {}).get("invalidated_claims") or []) if isinstance(conclusion_view, dict) else [],
        "invalidated_hypotheses": [
            item.get("hypothesis_id") for item in hypothesis_view.get("hypotheses") or []
            if item.get("invalidated_evidence_refs") and item.get("hypothesis_id")
        ],
        "invalidated_causal_edges": [
            item.get("edge_id") for item in causal_view.get("edges") or []
            if str(item.get("dependency_status") or "ACTIVE").upper() != "ACTIVE"
        ],
        "remaining_active_support": dict((conclusion_view or {}).get("remaining_active_support") or {}) if isinstance(conclusion_view, dict) else {},
    }
    if intervention_id:
        repo.record_case_event(
            case_id, tenant_id, event_type="intervention_read_receipt",
            payload={
                "intervention_id": intervention_id,
                "tool_name": "get_case_snapshot",
                "review_revision": max((int(item.get("review_revision") or 0) for item in excluded_evidence), default=0),
                "runtime_generation": runtime_generation,
            }, actor_id="mini-drop-pi-runtime",
        )
    return APIResponse(data={
        "case_id": case_id,
        "goal": case.get("problem_description", "")[:500],
        "target_scope": case.get("target_scope") or {},
        "case_command_revision": case.get("case_command_revision") or 1,
        "control_revision": case.get("control_revision") or 1,
        "scope_revision": case.get("scope_revision") or 1,
        "plan_revision": plan.get("plan_revision") or 0,
        "plan": plan,
        "attachments": [
            {"type": item.get("resource_ref", {}).get("type"),
             "id": item.get("resource_ref", {}).get("id"),
             "status": item.get("status")}
            for item in attachments
        ],
        "evidence": evidence_items,
        "excluded_evidence": excluded_evidence,
        "evidence_watermark": projection_count,
        "hypothesis_graph": hypothesis_view,
        "evidence_gaps": investigation_state["evidence_gaps"],
        "causal_graph": causal_view,
        "conclusion": conclusion_view,
        "recommendations": investigation_state["recommendations"],
        "propagation": propagation,
        "query_operations": [
            item.get("operation_id")
            for item in QUERY_REGISTRY.list_operations()
        ],
        "budget": {"active_cases": agent_max_active_cases()},
    })


@router.post("/internal/agent/tools/list-case-evidence")
def internal_tool_list_case_evidence(payload: dict[str, Any], request: Request) -> APIResponse:
    """v6 read-only tool: canonical evidence inventory with projection hashes."""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    intervention_id = str(payload.get("intervention_id") or "").strip()
    runtime_generation = int(payload.get("runtime_generation") or 0)
    filters = payload.get("filters") or {}
    status = filters.get("status")
    if str(status or "").upper() in {"EXCLUDED", "SUPERSEDED", "INVALID"}:
        raise HTTPException(status_code=409, detail="EXCLUDED_EVIDENCE_AUDIT_ONLY")
    items = case_evidence_service.list_evidence(
        case_id, tenant_id, status=status, limit=int(payload.get("limit") or 200),
    )
    if not status:
        items = [
            item for item in items
            if str(item.get("lifecycle_status") or item.get("status") or "ACTIVE").upper()
            not in {"EXCLUDED", "SUPERSEDED", "INVALID"}
        ]
    projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(repo, "list_evidence_projections") else []
    by_evidence: dict[str, list[dict[str, Any]]] = {}
    for projection in projections:
        by_evidence.setdefault(projection.get("evidence_id"), []).append(projection)
    result = []
    for item in items:
        result.append({
            **item,
            "projections": [
                {"projection_id": p.get("projection_id"), "projection_kind": p.get("projection_kind"),
                 "projection_hash": p.get("projection_hash"), "truncated": p.get("truncated")}
                for p in by_evidence.get(item.get("evidence_id"), [])
            ],
        })
    if intervention_id:
        repo.record_case_event(
            case_id, tenant_id, event_type="intervention_read_receipt",
            payload={
                "intervention_id": intervention_id,
                "tool_name": "list_case_evidence",
                "review_revision": max((int(item.get("review_revision") or 0) for item in result), default=0),
                "runtime_generation": runtime_generation,
            }, actor_id="mini-drop-pi-runtime",
        )
    return APIResponse(data={
        "items": result,
        "total": len(result),
        "evidence_watermark": len(projections),
        "cursor": payload.get("cursor"),
    })


@router.post("/internal/agent/tools/list-operations")
def internal_tool_list_operations(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    items = repo.list_operation_specs(enabled_only=True) if hasattr(repo, "list_operation_specs") else []
    if not items:
        items = QUERY_REGISTRY.list_operations()
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/internal/agent/tools/collectors")
def internal_tool_list_collectors(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    if case_id and repo.get_incident_case(case_id, _request_tenant()) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    return APIResponse(data=collector_catalog_payload())


@router.post("/internal/agent/tools/collection-proposal")
def internal_tool_propose_collection(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "propose_collection")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    target_selector = payload.get("target_selector") or {}
    requested_agent_id = str(target_selector.get("agent_id") or "").strip()
    try:
        requested_pid = int(
            target_selector.get("target_pid") or target_selector.get("pid") or 0
        )
    except (TypeError, ValueError):
        requested_pid = 0
    scoped_process_targets = collection_supervisor.case_scoped_process_targets(case)
    scoped_agent_level_ids = collection_supervisor.case_scoped_agent_level_ids(case)
    requires_discovery_authority = bool(
        requested_agent_id
        and requested_pid > 0
        and (
            (
                bool(scoped_process_targets)
                and (requested_agent_id, requested_pid)
                not in scoped_process_targets
            )
            or (
                not scoped_process_targets
                and bool(scoped_agent_level_ids)
                and requested_agent_id not in scoped_agent_level_ids
            )
        )
    )
    input_evidence_refs = [
        str(item) for item in payload.get("input_evidence_refs") or [] if str(item)
    ]
    authorized_agent_ids: set[str] | None = None
    dispatch_context: dict[str, Any] | None = None
    try:
        runtime_policy = _runtime_policy_from_tool_payload(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    discovery_evidence_refs = [
        str(item)
        for item in (
            payload.get("discovery_evidence_refs") or input_evidence_refs
        )
        if str(item)
    ]
    has_discovery_authority_claim = bool(
        str(payload.get("discovery_run_id") or "").strip()
        or discovery_evidence_refs
    )
    should_validate_discovery_authority = bool(
        requires_discovery_authority
        and (
            runtime_policy.side_effect_policy != "PROPOSE_ONLY"
            or has_discovery_authority_claim
        )
    )
    if should_validate_discovery_authority:
        try:
            authority = collection_supervisor.authorize_discovered_target(
                case_id=case_id,
                tenant_id=tenant_id,
                target_selector=target_selector,
                discovery_run_id=str(payload.get("discovery_run_id") or ""),
                evidence_refs=discovery_evidence_refs,
                expected_control_revision=payload.get("expected_control_revision"),
                expected_scope_revision=payload.get("expected_scope_revision"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        authorized_agent_ids = set(
            authority.get("authorized_agent_ids") or set()
        )
        authority_refs = [str(item) for item in authority.get("evidence_refs") or []]
        input_evidence_refs = list(dict.fromkeys([
            *input_evidence_refs, *authority_refs,
        ]))
        dispatch_context = {
            "discovery_run_id": authority["discovery_run_id"],
            "discovery_authority_evidence_ref": authority_refs[0],
            "discovery_authority_evidence_refs": authority_refs,
            "discovery_followup_authority": True,
            "membership_snapshot_id": authority["membership_snapshot_id"],
            "expected_boot_id": authority["expected_boot_id"],
            "expected_process_start_time": authority[
                "expected_process_start_time"
            ],
            "expected_entity_id": authority["expected_entity_id"],
        }
    elif requires_discovery_authority:
        # PROPOSE_ONLY may persist a reviewable proposal without execution
        # authority.  This ephemeral allowance is not pinned; approval replays
        # with no grant and therefore fails closed unless the Case scope is
        # corrected before dispatch.
        authorized_agent_ids = {requested_agent_id}
    try:
        collector_spec = get_collector_spec(str(payload.get("collector_id") or ""))
        if (
            collector_spec is not None
            and collector_spec.risk_level in runtime_policy.require_approval_for
            and not runtime_policy.auto_approve
        ):
            raise HTTPException(status_code=403, detail="COLLECTOR_REQUIRES_APPROVAL")
        result = collection_supervisor.propose_and_dispatch(
            case_id=case_id, tenant_id=tenant_id,
            collector_id=str(payload.get("collector_id") or ""),
            target_selector=target_selector,
            parameters=payload.get("parameters") or {},
            information_goal=str(payload.get("information_goal") or ""),
            reason_summary=str(payload.get("reason_summary") or ""),
            time_window=payload.get("time_window") or {},
            input_evidence_refs=input_evidence_refs,
            runtime_generation=int(payload.get("runtime_generation") or 1),
            expected_control_revision=payload.get("expected_control_revision"),
            expected_scope_revision=payload.get("expected_scope_revision"),
            idempotency_key=str(payload.get("idempotency_key") or "") or None,
            allowed_risk_levels=runtime_policy.allowed_risk_levels,
            max_collection_requests=runtime_policy.max_collection_requests,
            max_collection_duration_sec=runtime_policy.max_collection_duration_sec,
            agent_run_id=str(payload.get("agent_run_id") or "") or None,
            cycle_id=str(payload.get("cycle_id") or "") or None,
            auto_dispatch=runtime_policy.side_effect_policy == "AUTO_READ_LOW",
            authorized_agent_ids=authorized_agent_ids,
            dispatch_context=dispatch_context,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    proposal = result.get("proposal") or {}
    if proposal.get("status") == "REJECTED":
        raise HTTPException(
            status_code=409,
            detail="COLLECTION_PROPOSAL_REJECTED:" + ",".join(
                (proposal.get("validation_result") or {}).get("errors") or []
            ),
        )
    task = result.get("task")
    collection_request = result.get("collection_request")
    trigger_turn_id = str(payload.get("trigger_turn_id") or "")
    if trigger_turn_id and (
        task is not None
        or str((collection_request or {}).get("status") or "") in {"ACCEPTED", "DISPATCHED", "RUNNING"}
    ):
        completed_turn = repo.complete_agent_runtime_turn(
            trigger_turn_id,
            tenant_id,
            detail="采集已调度，后续由 Evidence 唤醒继续调查",
        )
        if completed_turn is not None:
            repo.record_case_event(
                case_id,
                tenant_id,
                event_type="turn.completed",
                payload={
                    "turn_id": trigger_turn_id,
                    "outcome": "collection_scheduled",
                    "collection_request_id": (collection_request or {}).get("collection_request_id"),
                },
                actor_id="mini-drop-agent-runtime",
            )
    return APIResponse(data={
        "proposal": proposal,
        "collection_request": collection_request,
        "task": _task_view(task) if task is not None else None,
    })


@router.post("/internal/agent/tools/topology-discovery")
def internal_tool_discover_topology(payload: dict[str, Any], request: Request) -> APIResponse:
    """Start/advance bounded discovery through the native CollectionSupervisor."""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "discover_topology")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    try:
        runtime_policy = _runtime_policy_from_tool_payload(payload)
        collector_spec = get_collector_spec("network_discovery")
        if (
            runtime_policy.side_effect_policy == "AUTO_READ_LOW"
            and collector_spec is not None
            and collector_spec.risk_level in runtime_policy.require_approval_for
            and not runtime_policy.auto_approve
        ):
            raise HTTPException(status_code=403, detail="COLLECTOR_REQUIRES_APPROVAL")
        auto_dispatch = (
            runtime_policy.side_effect_policy == "AUTO_READ_LOW"
            and runtime_policy.execution_mode == "normal"
        )
        supplied_run_id = str(payload.get("run_id") or "")
        # Real providers sometimes invent a friendly first-call identifier
        # (for example ``topo_run_001``) even though run IDs are server-owned.
        # Treat only Mini-Drop's canonical discovery-* IDs as an advance. This
        # keeps the public advance API strict while making the bounded Agent
        # tool robust to a harmless first-call formatting mistake.
        ignored_unrecognized_run_id = bool(
            supplied_run_id and not supplied_run_id.startswith("discovery-")
        )
        run_id = "" if ignored_unrecognized_run_id else supplied_run_id
        if run_id:
            advance = AdvanceTopologyDiscoveryRequest.model_validate({
                "wait_timeout_sec": payload.get("wait_timeout_sec", 0),
            })
            result = advance_topology_discovery_run(
                case_id,
                tenant_id,
                "mini-drop-agent-runtime",
                run_id,
                wait_timeout_sec=advance.wait_timeout_sec,
                expected_control_revision=payload.get("expected_control_revision"),
                expected_scope_revision=payload.get("expected_scope_revision"),
                auto_dispatch=auto_dispatch,
                allowed_risk_levels=runtime_policy.allowed_risk_levels,
                max_collection_requests=runtime_policy.max_collection_requests,
                max_collection_duration_sec=runtime_policy.max_collection_duration_sec,
            )
        else:
            start_payload = {
                key: payload[key]
                for key in StartTopologyDiscoveryRequest.model_fields
                if key in payload
            }
            start_payload.setdefault("wait_timeout_sec", 0)
            if not start_payload.get("idempotency_key"):
                trigger_ref = str(
                    payload.get("trigger_turn_id")
                    or payload.get("cycle_id")
                    or payload.get("agent_run_id")
                    or ""
                )
                if trigger_ref:
                    start_payload["idempotency_key"] = f"agent-topology:{trigger_ref}"
            result = start_topology_discovery_run(
                case_id,
                tenant_id,
                "mini-drop-agent-runtime",
                StartTopologyDiscoveryRequest.model_validate(start_payload),
                auto_dispatch=auto_dispatch,
                runtime_generation=int(payload.get("runtime_generation") or 1),
                expected_control_revision=payload.get("expected_control_revision"),
                expected_scope_revision=payload.get("expected_scope_revision"),
                allowed_risk_levels=runtime_policy.allowed_risk_levels,
                max_collection_requests=runtime_policy.max_collection_requests,
                max_collection_duration_sec=runtime_policy.max_collection_duration_sec,
                agent_run_id=str(payload.get("agent_run_id") or "") or None,
                cycle_id=str(payload.get("cycle_id") or "") or None,
            )
            if ignored_unrecognized_run_id:
                result = {
                    **result,
                    "compatibility": {
                        "ignored_unrecognized_run_id": supplied_run_id,
                        "reason": "FIRST_CALL_RUN_ID_IS_SERVER_OWNED",
                    },
                }
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    trigger_turn_id = str(payload.get("trigger_turn_id") or "")
    if trigger_turn_id and str(result.get("status") or "").upper() == "COLLECTING":
        completed_turn = repo.complete_agent_runtime_turn(
            trigger_turn_id,
            tenant_id,
            detail="拓扑发现采集已调度，后续由 Evidence 唤醒继续调查",
        )
        if completed_turn is not None:
            repo.record_case_event(
                case_id,
                tenant_id,
                event_type="turn.completed",
                payload={
                    "turn_id": trigger_turn_id,
                    "outcome": "topology_discovery_collecting",
                    "discovery_run_id": result.get("run_id"),
                },
                actor_id="mini-drop-agent-runtime",
            )
    return APIResponse(data=result)


@router.post("/internal/agent/tools/collection-status")
def internal_tool_collection_status(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    persistence = getattr(collection_supervisor, "_repo")
    return APIResponse(data={
        "proposals": persistence.list_collection_proposals(case_id, tenant_id),
        "requests": persistence.list_collection_requests(case_id, tenant_id),
    })


@router.post("/internal/agent/tools/evidence-analysis")
def internal_tool_submit_evidence_analysis(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "submit_evidence_analysis")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    try:
        result = evidence_analysis_service.complete_run(
            analysis_run_id=str(payload.get("analysis_run_id") or ""),
            case_id=case_id, tenant_id=tenant_id, facts=payload.get("facts") or [],
            anomalies=payload.get("anomalies") or [],
            interpretations=payload.get("interpretations") or [],
            conflicts=payload.get("conflicts") or [], limitations=payload.get("limitations") or [],
            next_collection_proposals=payload.get("next_collection_proposals") or [],
            token_usage=payload.get("token_usage") or {}, latency_ms=payload.get("latency_ms"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=result)


@router.post("/internal/agent/tools/evidence-analyses")
def internal_tool_get_evidence_analyses(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    items = evidence_analysis_service.list_runs(
        case_id, tenant_id, evidence_id=str(payload.get("evidence_id") or "") or None,
    )
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/internal/agent/tools/get-evidence-projection")
def internal_tool_get_evidence_projection(payload: dict[str, Any], request: Request) -> APIResponse:
    """v6 read-only tool: bounded content expansion of EvidenceProjection."""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    evidence_ids = [str(item) for item in (payload.get("evidence_ids") or [])]
    kinds = [str(item) for item in (payload.get("projection_kinds") or [])]
    max_bytes = int(payload.get("max_bytes") or 131072)
    projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(repo, "list_evidence_projections") else []
    selected = []
    for projection in projections:
        evidence = repo.get_case_evidence(
            case_id, tenant_id, str(projection.get("evidence_id") or ""),
        )
        if evidence is None:
            continue
        evidence_status = str(evidence.get("status") or "ACTIVE")
        # Ordinary investigation reads cannot resurrect excluded Evidence.
        # Audit reads may inspect it, but the lifecycle is explicit in output.
        if evidence_status == "EXCLUDED" and not bool(payload.get("audit_read")):
            continue
        if evidence_ids and projection.get("evidence_id") not in evidence_ids:
            continue
        if kinds and projection.get("projection_kind") not in kinds:
            continue
        if int(projection.get("projected_bytes") or 0) > max_bytes:
            selected.append({
                "projection_id": projection.get("projection_id"),
                "truncated_after_max_bytes": True,
                "projected_bytes": projection.get("projected_bytes"),
            })
            continue
        selected.append({
            **projection,
            "evidence_status": evidence_status,
            "review_revision": len(repo.list_evidence_reviews(
                case_id, tenant_id,
                evidence_id=str(projection.get("evidence_id") or ""),
            )),
        })
    return APIResponse(data={
        "items": selected,
        "max_bytes": max_bytes,
        "evidence_watermark": len(projections),
    })


@router.post("/internal/agent/tools/compare-evidence")
def internal_tool_compare_evidence(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    evidence_ids = [str(item) for item in (payload.get("evidence_ids") or [])][:8]
    projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(repo, "list_evidence_projections") else []
    rows = []
    for evidence_id in evidence_ids:
        item = repo.get_case_evidence(case_id, tenant_id, evidence_id)
        if item is None:
            rows.append({"evidence_id": evidence_id, "error": "NOT_FOUND"})
            continue
        item_projections = [p for p in projections if p.get("evidence_id") == evidence_id]
        rows.append({
            "evidence_id": evidence_id,
            "artifact_type": item.get("artifact_type"),
            "target_ref": item.get("target_ref"),
            "time_window": item.get("time_window") or {},
            "quality": item.get("quality"),
            "signals": [
                {"projection_hash": p.get("projection_hash"), "signals": (p.get("content") or {}).get("signals") or {}}
                for p in item_projections
            ],
        })
    return APIResponse(data={"items": rows, "dimensions": payload.get("dimensions") or ["signals"]})


@router.post("/internal/agent/tools/search-knowledge")
def internal_tool_search_knowledge(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    query = str(payload.get("query") or "")
    limit = int(payload.get("limit") or 5)
    case_id = str(payload.get("case_id") or "") or None
    tenant_id = _request_tenant()
    if not case_id or repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    user_items = retrieve_user_knowledge(
        query, tenant_id=tenant_id, case_id=case_id, limit=limit,
    )
    memory_items = retrieve_case_memory(
        query, tenant_id=tenant_id, case_id=case_id, limit=min(limit, 3),
    )
    static_items = [{
        "knowledge_id": item["knowledge_id"],
        "document_id": item["knowledge_id"],
        "chunk_id": f"static:{item['knowledge_id']}",
        "chunk_index": 0,
        "title": item["title"],
        "content": item["document"][:2000],
        "start_offset": 0,
        "end_offset": min(len(item["document"]), 2000),
        "score": 1.0,
        "source": "system_knowledge",
        "scope": "SYSTEM",
        "caveat": "系统知识仅提供调查方法，不能替代当前运行时 Evidence。",
    } for item in retrieve_knowledge(query, [], limit=limit)]
    items = sorted(
        user_items + memory_items + static_items,
        key=lambda item: (-float(item.get("score") or 0), str(item.get("chunk_id") or "")),
    )[:max(1, min(limit, 20))]
    return APIResponse(data={
        "items": items,
        "query": query,
        "retrieval_mode": "chunked_on_demand",
        "knowledge_is_evidence": False,
    })


@router.post("/internal/agent/tools/get-causal-graph")
def internal_tool_get_causal_graph(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    graph = repo.get_causal_graph(case_id, tenant_id) if hasattr(repo, "get_causal_graph") else None
    return APIResponse(data={"graph": graph})


@router.post("/internal/agent/tools/get-dependency-graph")
def internal_tool_get_dependency_graph(payload: dict[str, Any], request: Request) -> APIResponse:
    """Read the deterministic dependency graph; it carries no causal authority."""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    return APIResponse(data=case_dependency_graph_snapshot(repo, case_id, tenant_id))


@router.post("/internal/agent/tools/get-evidence-gaps")
def internal_tool_get_evidence_gaps(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    items = repo.list_evidence_gaps(case_id, tenant_id) if hasattr(repo, "list_evidence_gaps") else []
    return APIResponse(data={"items": items})


def _investigation_tool_case(payload: dict[str, Any], tool_name: str) -> tuple[str, str, dict[str, Any]]:
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, tool_name)
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    return case_id, tenant_id, case


@router.post("/internal/agent/tools/hypotheses")
def internal_tool_propose_hypotheses(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id, tenant_id, _ = _investigation_tool_case(payload, "propose_hypothesis_revision")
    try:
        graph = investigation_state_service.propose_hypotheses(
            case_id, tenant_id,
            graph={"hypotheses": payload.get("hypotheses") or [], "edges": payload.get("edges") or []},
            actor_id="mini-drop-pi-runtime",
            expected_scope_revision=payload.get("expected_scope_revision"),
            expected_control_revision=payload.get("expected_control_revision"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={"graph": graph})


@router.post("/internal/agent/tools/evidence-gaps")
def internal_tool_record_evidence_gaps(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id, tenant_id, _ = _investigation_tool_case(payload, "record_evidence_gaps")
    try:
        items = investigation_state_service.record_gaps(
            case_id, tenant_id, gaps=list(payload.get("gaps") or []),
            actor_id="mini-drop-pi-runtime",
            expected_scope_revision=payload.get("expected_scope_revision"),
            expected_control_revision=payload.get("expected_control_revision"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/internal/agent/tools/evidence-dependency")
def internal_tool_propose_evidence_dependency(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id, tenant_id, case = _investigation_tool_case(payload, "propose_evidence_dependency")
    source_kind = str(payload.get("source_kind") or "").upper()
    target_kind = str(payload.get("target_kind") or "").upper()
    relation = str(payload.get("relation") or "").upper()
    source_id = str(payload.get("source_id") or "")
    target_id = str(payload.get("target_id") or "")
    if source_kind != "EVIDENCE" or target_kind not in {"HYPOTHESIS", "CLAIM", "CAUSAL_NODE", "CAUSAL_EDGE"}:
        raise HTTPException(status_code=400, detail="INVALID_DEPENDENCY_KINDS")
    if relation not in {"SUPPORTS", "CONTRADICTS"}:
        raise HTTPException(status_code=400, detail="INVALID_DEPENDENCY_RELATION")
    evidence = repo.get_case_evidence(case_id, tenant_id, source_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="EVIDENCE_NOT_FOUND")
    lifecycle = str(evidence.get("lifecycle_status") or evidence.get("status") or "ACTIVE").upper()
    if lifecycle in {"EXCLUDED", "SUPERSEDED", "INVALID"}:
        raise HTTPException(status_code=409, detail="EXCLUDED_EVIDENCE_CANNOT_BE_DEPENDENCY")
    if not target_id:
        raise HTTPException(status_code=400, detail="DEPENDENCY_TARGET_REQUIRED")
    result = repo.propose_evidence_dependency(
        case_id=case_id, tenant_id=tenant_id, source_kind=source_kind,
        source_id=source_id, target_kind=target_kind, target_id=target_id,
        relation=relation, support_weight=float(payload.get("support_weight", 1.0) or 0.0),
        reason=str(payload.get("reason") or ""),
    )
    repo.record_case_event(case_id, tenant_id, event_type="evidence_dependency_proposed", payload=result, actor_id="mini-drop-pi-runtime")
    return APIResponse(data={"dependency": result, "calculation_version": "evidence-weighted-v1"})


@router.post("/internal/agent/tools/causal-graph")
def internal_tool_propose_causal_graph(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id, tenant_id, _ = _investigation_tool_case(payload, "propose_causal_graph")
    try:
        graph = investigation_state_service.propose_causal_graph(
            case_id, tenant_id, nodes=list(payload.get("nodes") or []),
            edges=list(payload.get("edges") or []), actor_id="mini-drop-pi-runtime",
            expected_scope_revision=payload.get("expected_scope_revision"),
            expected_control_revision=payload.get("expected_control_revision"),
            expected_evidence_watermark=payload.get("expected_evidence_watermark"),
            investigation_run_id=payload.get("investigation_run_id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={"graph": graph})


@router.post("/internal/agent/tools/reusable-evidence")
def internal_tool_reusable_evidence(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    task_ids = evidence_attachment_service.active_task_ids(case_id, tenant_id)
    evidence_rows = case_evidence_service.list_evidence(case_id, tenant_id, status="ACTIVE")
    projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(repo, "list_evidence_projections") else []
    reusable_evidence = []
    for item in evidence_rows:
        item_projections = [p for p in projections if p.get("evidence_id") == item.get("evidence_id")]
        reusable_evidence.append({
            "evidence_id": item.get("evidence_id"),
            "artifact_type": item.get("artifact_type"),
            "target_ref": item.get("target_ref"),
            "time_window": item.get("time_window") or {},
            "quality": item.get("quality"),
            "status": item.get("status"),
            "projection_hashes": [p.get("projection_hash") for p in item_projections],
            "reuse_reason": "fingerprint-target-window-quality-check",
        })
    return APIResponse(data={
        "case_id": case_id,
        "reusable_task_ids": task_ids,
        "reusable_evidence": reusable_evidence,
        "note": "Reuse requires ACTIVE evidence matching target/window/quality; a DONE Task alone is not reuse.",
    })


@router.post("/internal/agent/tools/query")
def internal_tool_create_query(payload: dict[str, Any], request: Request) -> APIResponse:
    """G4/G6：Pi 通过受控 Query Gateway 请求注册操作；仍由原生 Task 执行。"""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "request_operation")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    operation_id = str(payload.get("operation") or "")
    operation_spec = QUERY_REGISTRY.get(operation_id)
    try:
        runtime_policy = _runtime_policy_from_tool_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="RUNTIME_POLICY_INVALID") from exc
    if operation_spec is None or not runtime_policy.allows_operation(
        operation_id, operation_spec.risk,
    ):
        raise HTTPException(status_code=403, detail="OPERATION_DISABLED_BY_RUNTIME_POLICY")
    normalized_risk = str(operation_spec.risk).upper()
    normalized_risk = normalized_risk.replace("READ_LOW", "R1")
    normalized_risk = normalized_risk.replace("READ_ELEVATED", "R2").replace("READ_HIGH", "R2")
    normalized_risk = normalized_risk.replace("MUTATE", "R3").replace("WRITE", "R3")
    if normalized_risk in runtime_policy.require_approval_for and not runtime_policy.auto_approve:
        raise HTTPException(status_code=403, detail="OPERATION_REQUIRES_APPROVAL")
    try:
        enforce_runtime_execution_policy(
            runtime_policy,
            action_id=operation_id,
            risk_level=operation_spec.risk,
        )
    except ActuationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        task, operation_id = _create_case_query_task(
            case,
            tenant_id,
            "mini-drop-pi-runtime",
            operation_id,
            payload.get("parameters") or {},
            idempotency_key=str(payload.get("idempotency_key") or "") or None,
        )
    except QueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return APIResponse(data={"task": _task_view(task), "operation": operation_id})


@router.post("/internal/agent/tools/plan")
def internal_tool_upsert_plan(payload: dict[str, Any], request: Request) -> APIResponse:
    """模型提出新 Plan Revision。必须携带当前 revision，否则 STALE_PLAN 拒绝。"""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "propose_plan_revision")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    principal_id = "mini-drop-pi-runtime"
    envelope_fields = {
        "case_id", "tool", "side_effect_policy", "runtime_policy", "runtime_options",
        "runtime_generation", "expected_control_revision",
    }
    plan_payload = {key: value for key, value in payload.items() if key not in envelope_fields}
    try:
        plan = investigation_plan_service.update_plan(
            case_id,
            tenant_id,
            PlanUpdateInput(**plan_payload),
            actor_id=principal_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=plan)


def _internal_tool_finish_impl(payload: dict[str, Any], request: Request) -> APIResponse:
    """结构化结论提交；必须引用真实存在的 Evidence ID，并落审计事件。"""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    summary = str(payload.get("summary") or "").strip()
    evidence_ids = [str(item) for item in (payload.get("evidence_ids") or [])]
    requested_state = str(payload.get("state") or "PARTIALLY_CONFIRMED").upper()
    requested_state = {"CONCLUDED": "CONFIRMED"}.get(requested_state, requested_state)
    if requested_state not in {
        "CONFIRMED", "PARTIALLY_CONFIRMED", "INSUFFICIENT_EVIDENCE",
    }:
        raise HTTPException(status_code=400, detail="INVALID_CONCLUSION_STATE")
    abstention_reason = str(payload.get("abstention_reason") or "").strip()
    if not evidence_ids and not (
        requested_state == "INSUFFICIENT_EVIDENCE" and abstention_reason
    ):
        raise HTTPException(status_code=400, detail="NO_EVIDENCE_REFS")
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "finish_investigation")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)
    intervention_audit = _intervention_audit_for_finish(case_id, tenant_id, payload)
    if (
        payload.get("runtime_generation") is not None
        and payload.get("intervention_id")
        and not intervention_audit.get("read_receipts_validated")
    ):
        raise HTTPException(status_code=409, detail="INTERVENTION_READ_RECEIPTS_REQUIRED")

    # Real Pi tool calls always carry trigger_turn_id. Resolve it before the
    # first durable write so a bad application boundary cannot half-finish.
    trigger_turn_id = str(payload.get("trigger_turn_id") or "")
    if trigger_turn_id and repo.get_agent_runtime_turn(trigger_turn_id, tenant_id) is None:
        trigger_turn_id = ""
    if not trigger_turn_id:
        turns = repo.list_agent_runtime_turns(case_id, tenant_id)
        active_turns = [
            item for item in reversed(turns)
            if item.get("status") in {"ACCEPTED", "ROUTED", "RUNNING"}
        ]
        trigger_turn_id = str(active_turns[0].get("turn_id") or "") if active_turns else ""

    # 校验 Evidence ID 必须来自当前 Case 已接受的 Attachment 或 Diagnosis Evidence。
    known_evidence_ids: set[str] = set()
    excluded_evidence_ids: set[str] = set()
    for attachment in evidence_attachment_service.list_attachments(case_id, tenant_id):
        if attachment.get("status") == "EXCLUDED_BY_USER":
            continue
        known_evidence_ids.update(attachment.get("evidence_ids") or [])
    if hasattr(repo, "list_case_evidence"):
        for item in repo.list_case_evidence(case_id, tenant_id, status=None):
            if str(item.get("lifecycle_status") or item.get("status") or "ACTIVE").upper() in {
                "EXCLUDED", "SUPERSEDED", "INVALID",
            } or str(item.get("status") or "").upper() == "EXCLUDED":
                evidence_id = str(item.get("evidence_id") or "")
                if evidence_id:
                    excluded_evidence_ids.add(evidence_id)
                continue
            known_evidence_ids.add(str(item.get("evidence_id") or ""))
        for item in repo.list_case_evidence(case_id, tenant_id, status="EXCLUDED"):
            evidence_id = str(item.get("evidence_id") or "")
            known_evidence_ids.discard(evidence_id)
            if evidence_id:
                excluded_evidence_ids.add(evidence_id)
    diagnosis_id = case.get("diagnosis_session_id")
    if diagnosis_id:
        diagnosis = diagnosis_orchestrator.store.get_detail(diagnosis_id) or {}
        for item in (diagnosis.get("evidence") or []):
            ev_id = str(item.get("evidence_id") or "")
            if ev_id:
                known_evidence_ids.add(ev_id)
    unknown = [ev_id for ev_id in evidence_ids if ev_id not in known_evidence_ids]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"INVALID_EVIDENCE_REFS:{','.join(unknown[:5])}",
        )
    evidence_rows = {
        str(item.get("evidence_id")): item
        for item in repo.list_case_evidence(case_id, tenant_id, status=None)
        if item.get("evidence_id")
    }
    if not summary:
        raise HTTPException(status_code=400, detail="SUMMARY_REQUIRED")
    structured_conclusion = {
        key: payload.get(key)
        for key in (
            "claims", "primary_root_causes", "contributing_factors", "amplifiers",
            "propagated_effects", "symptoms", "coincidental_anomalies", "ruled_out",
            "recommendations",
            "root_location", "mechanism", "confidence_reason", "evidence_gaps",
            "invalidated_claims", "remaining_active_support",
        )
    }
    root_location = payload.get("root_location") or {}
    mechanism = payload.get("mechanism") or {}
    confidence_reason = str(payload.get("confidence_reason") or "").strip()
    structured_mode = any(
        key in payload for key in (
            "root_location", "mechanism", "confidence_reason", "evidence_gaps",
            "invalidated_claims", "remaining_active_support",
        )
    )
    if structured_mode and requested_state != "INSUFFICIENT_EVIDENCE":
        if not isinstance(root_location, dict) or not str(root_location.get("type") or "").strip():
            raise HTTPException(status_code=400, detail="ROOT_LOCATION_REQUIRED")
        if not isinstance(mechanism, dict) or not str(mechanism.get("statement") or "").strip():
            raise HTTPException(status_code=400, detail="MECHANISM_REQUIRED")
        if not confidence_reason:
            raise HTTPException(status_code=400, detail="CONFIDENCE_REASON_REQUIRED")
    for ref in list(root_location.get("evidence_refs") or []) + list(mechanism.get("supporting_evidence") or []) + list(mechanism.get("contradicting_evidence") or []):
        if str(ref) not in known_evidence_ids:
            raise HTTPException(status_code=400, detail=f"INVALID_STRUCTURED_EVIDENCE_REF:{ref}")
    if any(str(ref) in excluded_evidence_ids for ref in list(root_location.get("evidence_refs") or []) + list(mechanism.get("supporting_evidence") or []) + list(mechanism.get("contradicting_evidence") or [])):
        raise HTTPException(status_code=400, detail="EXCLUDED_EVIDENCE_IN_STRUCTURED_CONCLUSION")
    leaked_excluded = sorted(
        set(_nested_string_values(structured_conclusion)) & excluded_evidence_ids,
    )
    leaked_excluded.extend(_excluded_projection_value_leaks(
        case_id,
        tenant_id,
        excluded_evidence_ids,
        {"summary": summary, **structured_conclusion},
    ))
    if leaked_excluded:
        # Do not echo the matched value in the rejection body: that would
        # reintroduce the excluded numeric/string content into the Agent turn.
        leak_ids = sorted({item.split(":", 1)[0] for item in leaked_excluded})
        raise HTTPException(
            status_code=400,
            detail=f"EXCLUDED_EVIDENCE_IN_CONCLUSION:{','.join(leak_ids[:6])}",
        )

    # Recovery-only continuation for a conclusion committed by the previous
    # non-atomic implementation. Only the missing Case/message/Turn projection
    # is published; the verified conclusion itself is immutable.
    resume_conclusion_id = str(payload.get("resume_conclusion_id") or "")
    if resume_conclusion_id:
        existing_conclusion = repo.get_conclusion(case_id, tenant_id, resume_conclusion_id)
        if existing_conclusion is None:
            raise HTTPException(status_code=404, detail="CONCLUSION_NOT_FOUND")
        if str(existing_conclusion.get("report_text") or "") != summary:
            raise HTTPException(status_code=409, detail="CONCLUSION_RESUME_MISMATCH")
        resumed_state = str(existing_conclusion.get("state") or "PARTIALLY_CONFIRMED")
        resumed_limitations = [str(item) for item in existing_conclusion.get("limitations") or []]
        visible_summary = f"{resumed_state}：{summary}"
        if resumed_limitations:
            visible_summary += "\n\n限制：" + "；".join(dict.fromkeys(resumed_limitations))
        message_id = f"amsg-{hashlib.sha256(f'{case_id}:{resume_conclusion_id}:verified'.encode()).hexdigest()[:24]}"
        finalized = repo.finalize_investigation_result(
            case_id=case_id,
            tenant_id=tenant_id,
            summary=summary,
            evidence_refs=evidence_ids,
            limitations=resumed_limitations,
            conclusion_state=resumed_state,
            conclusion_id=resume_conclusion_id,
            message_id=message_id,
            visible_content=visible_summary,
            trigger_turn_id=trigger_turn_id or None,
            intervention_audit=intervention_audit,
        )
        return APIResponse(data={
            "accepted": True,
            "case_id": case_id,
            "evidence_refs": evidence_ids,
            "verifier": "causal-report-verifier.v1",
            "state": resumed_state,
            "conclusion_id": resume_conclusion_id,
            "assistant_message_id": finalized["message"]["message_id"],
            "event_type": "agent_finish_investigation",
            "resumed": True,
            **intervention_audit,
        })

    # v6 finish: ClaimEvidenceBinding must reference the current projection hash
    # and, when field_path is supplied, the predicate is evaluated against the
    # persisted projection content.  Legacy evidence_ids are upgraded into
    # bindings; they can no longer finish with an ID-only placeholder verifier.
    projections = repo.list_evidence_projections(case_id, tenant_id) if hasattr(repo, "list_evidence_projections") else []
    current_watermark = len(projections)
    expected_watermark = payload.get("expected_evidence_watermark")
    if expected_watermark is not None and int(expected_watermark) < current_watermark:
        raise HTTPException(status_code=409, detail="EVIDENCE_WATERMARK_STALE")
    claims = list(payload.get("claims") or [])
    legacy_compat = False
    if not claims:
        claims = []
        for evidence_id in evidence_ids:
            evidence = repo.get_case_evidence(case_id, tenant_id, evidence_id)
            matching = [p for p in projections if p.get("evidence_id") == evidence_id]
            if evidence is None or not matching:
                # v6 requires canonical Evidence+Projection.  Keep a narrow
                # compatibility path for old attachment-only callers while the
                # verifier remains factual and never ID-only.
                attachment_known = any(
                    evidence_id in (item.get("evidence_ids") or [])
                    for item in evidence_attachment_service.list_attachments(case_id, tenant_id)
                    if item.get("status") not in {"EXCLUDED_BY_USER", "SUPERSEDED"}
                )
                if not attachment_known:
                    raise HTTPException(status_code=400, detail=f"PROJECTION_MISSING:{evidence_id}")
                legacy_compat = True
                continue
            claims.append({
                "claim_id": f"claim-{hashlib.sha256(evidence_id.encode()).hexdigest()[:16]}",
                "evidence_id": evidence_id,
                "projection_hash": matching[0].get("projection_hash"),
                "support_kind": "SUPPORTS",
                "event_window": evidence.get("time_window") or {},
            })
    # Normalize model-friendly claim shapes into the canonical verifier schema.
    # DeepSeek/Pi often emits `supporting_evidence`, `evidence_ids` or `evidence`
    # instead of the server's `evidence_id` field.  Normalizing here keeps the
    # verifier strict about projection binding while forgiving the transport shape.
    normalized_claims: list[dict[str, Any]] = []
    for claim in claims:
        raw_ids: list[str] = []
        direct = claim.get("evidence_id")
        if direct:
            raw_ids.append(str(direct))
        for key in ("evidence_ids", "supporting_evidence", "evidence"):
            value = claim.get(key)
            if isinstance(value, str) and str(value).strip():
                raw_ids.append(str(value).strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and str(item).strip():
                        raw_ids.append(str(item).strip())
                    elif isinstance(item, dict):
                        nested = item.get("evidence_id") or item.get("id")
                        if nested:
                            raw_ids.append(str(nested))
        seen: set[str] = set()
        raw_ids = [item for item in raw_ids if not (item in seen or seen.add(item))]
        if not raw_ids:
            normalized_claims.append(claim)
            continue
        for evidence_id in raw_ids:
            canonical = {
                key: value for key, value in claim.items()
                if key not in {"evidence_ids", "supporting_evidence", "evidence"}
            }
            canonical["evidence_id"] = evidence_id
            normalized_claims.append(canonical)
    claims = normalized_claims
    declared_evidence_ids = set(evidence_ids)
    claim_evidence_ids = {
        str(claim.get("evidence_id") or "")
        for claim in claims
        if claim.get("evidence_id")
    }
    undeclared_claim_ids = sorted(claim_evidence_ids - declared_evidence_ids)
    if undeclared_claim_ids:
        raise HTTPException(
            status_code=400,
            detail=f"CLAIM_EVIDENCE_NOT_DECLARED:{','.join(undeclared_claim_ids[:6])}",
        )

    claim_errors: list[str] = []
    for claim in claims:
        evidence = repo.get_case_evidence(case_id, tenant_id, str(claim.get("evidence_id") or ""))
        if evidence is None:
            claim_errors.append(f"INVALID_EVIDENCE:{claim.get('evidence_id')}")
            continue
        # If the model omits projection_hash but the Evidence has exactly one
        # canonical projection, fill it server-side.  This removes a common
        # first-attempt failure without weakening verification: ambiguous
        # multi-projection cases still require an explicit hash.
        if not claim.get("projection_hash"):
            candidate_projections = [
                item for item in projections
                if item.get("evidence_id") == claim.get("evidence_id")
            ]
            if len(candidate_projections) == 1:
                claim["projection_hash"] = candidate_projections[0].get("projection_hash")
            else:
                claim_errors.append(f"PROJECTION_HASH_REQUIRED:{claim.get('evidence_id')}:{len(candidate_projections)}")
                continue
        ok, code = verify_claim_binding(evidence, projections, claim)
        if not ok:
            claim_errors.append(f"{code}:{claim.get('evidence_id')}:{claim.get('field_path') or 'id'}")
    if claim_errors:
        raise HTTPException(status_code=400, detail="CLAIM_VERIFICATION_FAILED:" + ",".join(claim_errors[:6]))

    run_id = None
    runs = repo.list_investigation_runs(case_id, tenant_id) if hasattr(repo, "list_investigation_runs") else []
    if runs:
        run_id = runs[0].get("run_id")
    graph = repo.get_causal_graph(case_id, tenant_id) if hasattr(repo, "get_causal_graph") else None
    graph_edges = (graph or {}).get("edges") or []
    evidence_gaps = repo.list_evidence_gaps(case_id, tenant_id) if hasattr(repo, "list_evidence_gaps") else []
    open_blocker_gaps = [
        item for item in evidence_gaps
        if str(item.get("status") or "") in {"OPEN", "BLOCKING"}
        and (str(item.get("status") or "") == "BLOCKING" or item.get("blocked_claim"))
    ]
    unresolved_alternatives = investigation_state_service.unresolved_alternative_count(
        case_id, tenant_id,
    )
    unsupported_business_symptoms = _business_symptom_missing_direct_evidence(
        graph or {}, case_id, tenant_id,
    )
    limitations = list(payload.get("limitations") or [])
    if unsupported_business_symptoms:
        limitations.append(
            "业务症状尚无接口探测、请求日志或链路数据直接支持；当前 Evidence 只能确认基础设施侧事实。"
        )
    recommendations_payload = list(payload.get("recommendations") or [])[:20]
    known_evidence_for_recommendations = known_evidence_ids
    for recommendation in recommendations_payload:
        if not isinstance(recommendation, dict):
            raise HTTPException(status_code=400, detail="INVALID_RECOMMENDATION_FIELDS")
        action = str(recommendation.get("concrete_action") or recommendation.get("action") or "").strip()
        target = str(recommendation.get("target") or "").strip()
        cause_ref = str(recommendation.get("cause_or_edge_ref") or "").strip()
        if not action or not target or not cause_ref:
            raise HTTPException(status_code=400, detail="INVALID_RECOMMENDATION_FIELDS")
        refs = [str(item) for item in recommendation.get("evidence_refs") or []]
        unknown_recommendation_refs = [item for item in refs if item not in known_evidence_for_recommendations]
        if unknown_recommendation_refs:
            raise HTTPException(
                status_code=400,
                detail=f"INVALID_RECOMMENDATION_EVIDENCE:{','.join(unknown_recommendation_refs[:5])}",
            )
        try:
            confidence = float(recommendation.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="INVALID_RECOMMENDATION_CONFIDENCE") from exc
        if confidence < 0.0 or confidence > 1.0:
            raise HTTPException(status_code=400, detail="INVALID_RECOMMENDATION_CONFIDENCE")
        recommendation_trust = [
            str(evidence_rows.get(ref, {}).get("review_trust_state") or "UNREVIEWED").upper()
            for ref in refs
        ]
        if recommendation_trust and all(state == "LOW_TRUST" for state in recommendation_trust):
            confidence = min(confidence, 0.5)
        recommendation["concrete_action"] = action
        recommendation["target"] = target
        recommendation["cause_or_edge_ref"] = cause_ref
        recommendation["evidence_refs"] = refs
        recommendation["confidence"] = confidence
    verified_state = verify_primary_confirmation(
        graph or {},
        requested_state,
        blocker_gaps=len(open_blocker_gaps),
        required_edge_missing=sum(
            1 for edge in graph_edges
            if edge.get("verification_state") not in {"OBSERVED", "SUPPORTED"}
        ) + len(unsupported_business_symptoms),
        alternative_primary_not_distinguished=unresolved_alternatives > 1,
    )
    supporting_trust = [
        str(evidence_rows.get(str(claim.get("evidence_id")), {}).get("review_trust_state") or "UNREVIEWED").upper()
        for claim in claims
        if str(claim.get("support_kind") or "SUPPORTS").upper() == "SUPPORTS"
    ]
    if verified_state == "CONFIRMED" and supporting_trust and all(
        state == "LOW_TRUST" for state in supporting_trust
    ):
        # LOW_TRUST is visible and citable, but cannot be the sole basis for a
        # high-certainty terminal conclusion.
        verified_state = "PARTIALLY_CONFIRMED"
    if legacy_compat:
        verified_state = "PARTIALLY_CONFIRMED"
    signature_payload = {
        "case_id": case_id,
        "trigger_turn_id": trigger_turn_id,
        "state": verified_state,
        "summary": summary,
        "evidence_ids": evidence_ids,
        "claims": [
            {key: value for key, value in claim.items() if key != "claim_id"}
            for claim in claims
        ],
        "primary_root_causes": list(payload.get("primary_root_causes") or []),
        "contributing_factors": list(payload.get("contributing_factors") or []),
        "amplifiers": list(payload.get("amplifiers") or []),
        "propagated_effects": list(payload.get("propagated_effects") or []),
        "symptoms": list(payload.get("symptoms") or []),
        "coincidental_anomalies": list(payload.get("coincidental_anomalies") or []),
        "ruled_out": list(payload.get("ruled_out") or []),
        "limitations": limitations,
        "abstention_reason": abstention_reason,
        "recommendations": [
            {key: value for key, value in recommendation.items() if key != "recommendation_id"}
            for recommendation in recommendations_payload
        ],
        "root_location": root_location,
        "mechanism": mechanism,
        "confidence_reason": confidence_reason,
    }
    finish_key = hashlib.sha256(
        _json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    conclusion_id = f"concl_{finish_key[:16]}"
    for index, claim in enumerate(claims):
        claim["claim_id"] = "claim_" + hashlib.sha256(
            f"{conclusion_id}:{index}:{claim.get('evidence_id')}:{claim.get('field_path') or ''}".encode()
        ).hexdigest()[:16]
    recommendation_ids = [
        "rec_" + hashlib.sha256(
            f"{conclusion_id}:{index}:{recommendation.get('cause_or_edge_ref')}:{recommendation.get('target')}".encode()
        ).hexdigest()[:16]
        for index, recommendation in enumerate(recommendations_payload)
    ]
    conclusion = None
    if hasattr(repo, "submit_conclusion_revision"):
        conclusion = repo.submit_conclusion_revision(
            case_id=case_id,
            tenant_id=tenant_id,
            investigation_run_id=run_id or "",
            state=verified_state,
            causal_graph_revision_id=(graph or {}).get("graph_id"),
            claims=claims,
            root_location=root_location,
            mechanism=mechanism,
            confidence_reason=confidence_reason,
            primary_root_causes=list(payload.get("primary_root_causes") or []),
            contributing_factors=list(payload.get("contributing_factors") or []),
            amplifiers=list(payload.get("amplifiers") or []),
            propagated_effects=list(payload.get("propagated_effects") or []),
            symptoms=list(payload.get("symptoms") or []),
            coincidental_anomalies=list(payload.get("coincidental_anomalies") or []),
            ruled_out=list(payload.get("ruled_out") or []),
            evidence_gap_ids=[str(item.get("gap_id")) for item in open_blocker_gaps],
            recommendation_ids=recommendation_ids,
            recommendations=recommendations_payload,
            limitations=limitations,
            abstention_reason=abstention_reason or None,
            report_text=summary,
            verifier_version="causal-report-verifier.v1",
            conclusion_id=conclusion_id,
        )
    visible_summary = f"{verified_state}：{summary}"
    if limitations:
        visible_summary += "\n\n限制：" + "；".join(dict.fromkeys(limitations))
    conclusion_id = conclusion.get("conclusion_id") if conclusion else conclusion_id
    message_id = f"amsg-{hashlib.sha256(f'{case_id}:{conclusion_id or summary}:verified'.encode()).hexdigest()[:24]}"
    finalized = repo.finalize_investigation_result(
        case_id=case_id,
        tenant_id=tenant_id,
        summary=summary,
        evidence_refs=evidence_ids,
        limitations=limitations,
        conclusion_state=verified_state,
        conclusion_id=conclusion_id,
        message_id=message_id,
        visible_content=visible_summary,
        trigger_turn_id=trigger_turn_id or None,
        limitation_refs=unsupported_business_symptoms,
        actor_id="mini-drop-pi-runtime",
        intervention_audit=intervention_audit,
    )
    assistant_message = finalized["message"]
    return APIResponse(data={
        "accepted": True,
        "case_id": case_id,
        "evidence_refs": evidence_ids,
        "verifier": "causal-report-verifier.v1",
        "state": verified_state,
        "conclusion_id": conclusion_id,
        "assistant_message_id": assistant_message["message_id"],
        "event_type": "agent_finish_investigation",
        **intervention_audit,
    })


@router.post("/internal/agent/tools/finish")
def internal_tool_finish(payload: dict[str, Any], request: Request) -> APIResponse:
    """Route wrapper that records structured diagnostics for finish failures."""
    try:
        return _internal_tool_finish_impl(payload, request)
    except HTTPException as exc:
        if exc.status_code == 400:
            log_event(
                "error",
                "finish_investigation_rejected",
                detail=str(exc.detail)[:500],
                case_id=str(payload.get("case_id") or ""),
                evidence_ids=[str(item) for item in (payload.get("evidence_ids") or [])][:10],
                claims_keys=[
                    sorted(str(key) for key in (claim or {}).keys())
                    for claim in (payload.get("claims") or [])[:10]
                ],
                state=str(payload.get("state") or ""),
            )
        raise


@router.get("/internal/agent/tools/health")
def internal_tool_health(request: Request) -> APIResponse:
    _require_internal_token(request)
    return APIResponse(data={"ok": True, "service": "mini-drop-tool-gateway"})


@router.post("/internal/runtime/v1/cases/{case_id}/events")
def internal_runtime_events(
    case_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """Sidecar 回传归一化 Runtime 事件（assistant/tool/decision/final）。

    私有思维链不允许进入此接口；event_seq 在 generation 内唯一，
    idempotency_key 用于崩溃重放去重。
    """
    _require_internal_token(request)
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    generation = int(payload.get("runtime_generation") or 0)
    if generation <= 0:
        raise HTTPException(status_code=400, detail="INVALID_RUNTIME_GENERATION")
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    if binding is not None and generation != int(binding.get("runtime_generation") or 0):
        raise HTTPException(status_code=409, detail="GENERATION_FENCED")
    events = payload.get("events") or []
    if not isinstance(events, list) or not events:
        raise HTTPException(status_code=400, detail="NO_RUNTIME_EVENTS")
    stored: list[dict[str, Any]] = []
    max_seq = 0
    final_content: str | None = None
    final_turn_id: str | None = None
    final_cycle_id: str | None = None
    final_model_request_id: str | None = None
    for raw in events:
        if not isinstance(raw, dict):
            continue
        event_type = str(raw.get("event_type") or raw.get("type") or "")
        if event_type.startswith("thinking"):
            continue
        if not event_type:
            continue
        event_seq = int(raw.get("event_seq") or raw.get("seq") or 0)
        if event_seq <= 0:
            continue
        payload_json = raw.get("payload") or {}
        if payload_json.get("foreign_turn_event"):
            # Keep the late/foreign lifecycle record for audit, but never let
            # it publish an assistant message or complete another turn.
            payload_json = {**payload_json, "business_projection": "FENCED"}
        cycle_id = str(raw.get("cycle_id") or payload_json.get("cycle_id") or "")
        model_request_id = str(raw.get("model_request_id") or payload_json.get("model_request_id") or "")
        persisted_event = repo.record_agent_runtime_event(
            event_id=str(raw.get("event_id") or f"evt_{secrets.token_hex(16)}"),
            case_id=case_id,
            tenant_id=tenant_id,
            runtime_generation=generation,
            event_seq=event_seq,
            event_type=event_type,
            payload=payload_json,
            idempotency_key=str(raw.get("idempotency_key") or "")
                         or f"runtime-event:{case_id}:{generation}:{event_seq}:{event_type}",
            cycle_id=cycle_id or None,
            model_request_id=model_request_id or None,
            evaluation_run_id=payload_json.get("evaluation_run_id"),
        )
        stored.append(persisted_event)
        if persisted_event.get("duplicate"):
            continue
        model_attempt_payload = payload_json.get("model_attempt")
        if (
            isinstance(model_attempt_payload, dict)
            and hasattr(repo, "record_model_attempt")
        ):
            try:
                repo.record_model_attempt({
                    **model_attempt_payload,
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    "context_packet_id": (
                        model_attempt_payload.get("context_packet_id")
                        or payload_json.get("context_packet_id")
                    ),
                    "idempotency_key": str(raw.get("idempotency_key") or "")
                        or f"runtime-event:{case_id}:{generation}:{event_seq}:{event_type}",
                })
            except Exception as exc:
                # Audit persistence must never break event ingestion/replay.
                log_event(
                    "error",
                    "model_attempt_record_failed",
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                    case_id=case_id,
                )
        max_seq = max(max_seq, event_seq)
        if (
            event_type in {"turn_end", "assistant.completed", "final"}
            and not payload_json.get("foreign_turn_event")
        ):
            text = _runtime_visible_content(payload_json)
            if text:
                final_content = text
                final_turn_id = str(payload_json.get("trigger_turn_id") or "")
                final_cycle_id = cycle_id or str(payload_json.get("cycle_id") or "")
                final_model_request_id = model_request_id or str(payload_json.get("model_request_id") or "")
    if binding is not None and max_seq > int(binding.get("last_event_seq") or 0):
        repo.upsert_agent_runtime_binding(
            case_id,
            tenant_id,
            runtime_type=binding.get("runtime_type") or "pi",
            runtime_version=binding.get("runtime_version") or "pi-0.84.2",
            runtime_session_id=binding.get("runtime_session_id") or "",
            runtime_generation=int(binding.get("runtime_generation") or generation),
            status=binding.get("status") or "READY",
            last_event_seq=max_seq,
            last_context_snapshot_id=binding.get("last_context_snapshot_id"),
            lease_owner=binding.get("lease_owner"),
        )
    if final_content:
        if not final_turn_id:
            turns = repo.list_agent_runtime_turns(case_id, tenant_id)
            open_turns = [item for item in reversed(turns) if item.get("status") == "ROUTED"]
            final_turn_id = open_turns[0].get("turn_id") if open_turns else None
        evidence_refs = _visible_evidence_ids(final_content, case_id, tenant_id)
        turn_key = final_turn_id or "system"
        message_id = f"amsg-{hashlib.sha256(f'{case_id}:{turn_key}:{final_content}'.encode()).hexdigest()[:24]}"
        message = repo.add_assistant_message(
            case_id=case_id,
            tenant_id=tenant_id,
            content=final_content,
            trigger_turn_id=final_turn_id or None,
            origin_turn_id=final_turn_id or None,
            cycle_id=final_cycle_id or None,
            model_request_id=final_model_request_id or None,
            evidence_refs=evidence_refs,
            message_id=message_id,
        )
        repo.record_case_event(
            case_id,
            tenant_id,
            event_type="assistant.message",
            payload={
                "message_id": message["message_id"],
                "trigger_turn_id": final_turn_id,
                "content": final_content,
                "evidence_refs": evidence_refs,
            },
            actor_id="mini-drop-agent-runtime",
        )
        if final_turn_id:
            repo.record_case_event(
                case_id,
                tenant_id,
                event_type="turn.completed",
                payload={"turn_id": final_turn_id, "message_id": message["message_id"]},
                actor_id="mini-drop-agent-runtime",
            )
    return APIResponse(data={
        "accepted": len(stored),
        "last_event_seq": max_seq,
        "assistant_message_id": message_id if final_content else None,
    })


@router.get("/internal/runtime/v1/cases/{case_id}/events")
def internal_runtime_events_list(
    case_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 200,
) -> APIResponse:
    """供 Sidecar 或工作台按游标读取 Runtime 事件（不含私有思维链）。"""
    _require_internal_token(request)
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    items = repo.list_agent_runtime_events(
        case_id,
        tenant_id,
        after_seq=max(0, int(after_seq)),
        limit=min(max(int(limit), 1), 1000),
    )
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/api/v1/cases/{case_id}/agent/shadow-plan")
async def run_case_shadow_plan(case_id: str, request: Request) -> APIResponse:
    """E3 Shadow Plan：Pi 生成计划但不创建 Task，与确定性计划配对比较。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    graph = repo.get_case_hypothesis_graph(case_id, tenant_id) or {"hypotheses": [], "edges": []}
    from server.app.diagnosis.probe_registry import list_probes
    probe_candidates = [
        {"collector_id": probe.probe_id, "rationale": probe.purpose,
         "risk": "READ_LOW" if probe.risk_level in {"R0", "R1"} else "READ_ELEVATED",
         "priority": 60}
        for probe in list_probes()
        if probe.risk_level in {"R0", "R1"}
    ]
    deterministic = build_deterministic_plan(
        case,
        active_hypotheses=graph.get("hypotheses") or [],
        probe_candidates=probe_candidates,
    )
    shadow, shadow_status = await request_shadow_plan(
        case,
        sidecar_url=os.getenv("MINI_DROP_PI_RUNTIME_URL", "") or None,
        deterministic_plan=deterministic,
    )
    comparison = compare_plans(deterministic, shadow)
    comparison["shadow_status"] = shadow_status
    return APIResponse(data=comparison)



__all__ = [
    "QueryError",
    "_build_runtime_case_context",
    "_case_investigation_footprint",
    "_create_case_query_task",
    "router",
]
