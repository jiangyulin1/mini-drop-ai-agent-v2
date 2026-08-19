"""v6 Agent HTTP surface exposed through an explicit router."""

from __future__ import annotations

import hashlib
import json as _json
import os
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.app.agent_runtime.config import agent_max_active_cases
from server.app.agent_runtime.catalog import get_tool_spec, tool_catalog_payload
from server.app.agent_runtime.options import RuntimeOptions, resolve_runtime_options
from server.app.agent_runtime.policy import (
    RuntimePolicy,
    constrain_side_effect_policy,
    resolve_runtime_policy,
)
from server.app.agent_runtime.port import CaseContextSnapshot
from server.app.application.task_views import task_view as _task_view
from server.app.diagnosis.actuation import ActuationError, enforce_runtime_execution_policy
from server.app.diagnosis.investigation_plan import PlanUpdateInput
from server.app.diagnosis.knowledge import retrieve_knowledge
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
from server.app.runtime_services import (
    case_evidence_service,
    diagnosis_orchestrator,
    evidence_attachment_service,
    investigation_plan_service,
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
) -> CaseContextSnapshot:
    """Build the L0/L1 Case projection handed to an AgentRuntimePort before a Turn."""
    case_id = str(case.get("case_id") or "")
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {}
    graph = repo.get_case_hypothesis_graph(case_id, tenant_id) or {"hypotheses": []}
    hypotheses = graph.get("hypotheses") or []
    evidence_summary: list[dict[str, Any]] = []
    canonical_evidence = case_evidence_service.list_evidence(case_id, tenant_id, status=None)
    for item in canonical_evidence:
        if item.get("status") == "EXCLUDED":
            continue
        projections = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=item.get("evidence_id"),
        ) if hasattr(repo, "list_evidence_projections") else []
        for projection in projections[:2]:
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
    missing_facts: list[str] = []
    for hypothesis in hypotheses:
        for fact in (hypothesis.get("missing_evidence") or []):
            if str(fact) not in missing_facts:
                missing_facts.append(str(fact))
    binding = repo.get_agent_runtime_binding(case_id, tenant_id)
    selected_skills = SKILL_REGISTRY.select_skills(
        goal=str(case.get("problem_description") or ""),
        target_scope=case.get("target_scope") or {},
        evidence_summary=evidence_summary,
        missing_facts=missing_facts,
        limit=3,
    )
    knowledge_context = retrieve_knowledge(
        str(case.get("problem_description") or ""),
        [{"finding_type": str(item.get("finding_type") or item.get("status") or "")}
         for item in hypotheses],
        limit=3,
    )
    resolved_policy = constrain_side_effect_policy(
        resolve_runtime_policy(runtime_policy), side_effect_policy,
    )
    experimental = resolved_policy.execution_mode in {"dry_run", "sandbox"}
    resolved_options = resolve_runtime_options(runtime_options, experiment_mode=experimental)
    strategy = get_strategy(strategy_id or resolved_options.strategy_id)
    directive = strategy.build_directive(
        goal=str(case.get("problem_description") or ""),
        target_scope=case.get("target_scope") or {},
        evidence_summary=evidence_summary,
        skill_context=selected_skills,
        missing_facts=missing_facts,
        strategy_params=resolved_options.strategy_params,
    )
    tool_catalog = sorted(resolved_policy.effective_tools())
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
        evidence_watermark=len(evidence_summary),
        investigation_run_id=investigation_run_id,
        turn_id=turn_id,
        disposition=disposition,
        side_effect_policy=resolved_policy.side_effect_policy,
        diagnostic_strategy_id=strategy.strategy_id,
        strategy_params=resolved_options.strategy_params,
        strategy_guidance=strategy.render_prompt_guidance(),
        runtime_policy=resolved_policy.audit_summary(),
        runtime_options=resolved_options.audit_summary(),
        context_snapshot_id=None,
        runtime_generation=int(binding.get("runtime_generation") or 1) if binding else 1,
        runtime_session_id=str(binding.get("runtime_session_id") or "") if binding else "",
        hypotheses=[
            {
                "hypothesis_id": item.get("hypothesis_id"),
                "statement": item.get("statement"),
                "status": item.get("status"),
            }
            for item in hypotheses[:20]
        ],
        evidence_summary=evidence_summary[:20],
        missing_facts=missing_facts[:20],
        running_task_ids=[],
        budget={"max_active_cases": agent_max_active_cases()},
        recent_user_commands=[],
        tool_catalog_summary=tool_catalog,
        knowledge_context=knowledge_context,
        skill_context=selected_skills,
        investigation_directive=directive.model_dump(mode="json"),
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


def _runtime_policy_from_tool_payload(payload: dict[str, Any]) -> RuntimePolicy:
    return resolve_runtime_policy(
        payload.get("runtime_policy") or {
            "side_effect_policy": str(payload.get("side_effect_policy") or "AUTO_READ_LOW"),
        },
    )


def _runtime_visible_content(payload: dict[str, Any]) -> str:
    """Extract only user-visible assistant text from a Sidecar event."""
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


def _visible_evidence_ids(content: str, case_id: str, tenant_id: str) -> list[str]:
    """Return canonical evidence IDs that are both persisted and visibly cited."""
    known = [
        str(item.get("evidence_id") or "")
        for item in repo.list_case_evidence(case_id, tenant_id, status="ACTIVE")
    ]
    return [item for item in known if item and item in content]


@router.post("/internal/agent/tools/case-snapshot")
def internal_tool_case_snapshot(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    plan = investigation_plan_service.read_plan(case_id, tenant_id) or {"plan_id": None, "steps": []}
    attachments = evidence_attachment_service.list_attachments(case_id, tenant_id)
    evidence_items: list[dict[str, Any]] = []
    for item in case_evidence_service.list_evidence(case_id, tenant_id):
        projections = repo.list_evidence_projections(
            case_id, tenant_id, evidence_id=item.get("evidence_id"),
        ) if hasattr(repo, "list_evidence_projections") else []
        evidence_items.append({
            "evidence_id": item.get("evidence_id"),
            "artifact_type": item.get("artifact_type"),
            "status": item.get("status"),
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
        "evidence_watermark": len(evidence_items),
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
    filters = payload.get("filters") or {}
    status = filters.get("status")
    items = case_evidence_service.list_evidence(
        case_id, tenant_id, status=status, limit=int(payload.get("limit") or 200),
    )
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
        selected.append(projection)
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
    items = retrieve_knowledge(query, [], limit=int(payload.get("limit") or 5))
    return APIResponse(data={"items": items})


@router.post("/internal/agent/tools/get-causal-graph")
def internal_tool_get_causal_graph(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    graph = repo.get_causal_graph(case_id, tenant_id) if hasattr(repo, "get_causal_graph") else None
    return APIResponse(data={"graph": graph})


@router.post("/internal/agent/tools/get-evidence-gaps")
def internal_tool_get_evidence_gaps(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    items = repo.list_evidence_gaps(case_id, tenant_id) if hasattr(repo, "list_evidence_gaps") else []
    return APIResponse(data={"items": items})


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


@router.post("/internal/agent/tools/evaluate-hypotheses")
def internal_tool_evaluate_hypotheses(payload: dict[str, Any], request: Request) -> APIResponse:
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    graph = repo.get_case_hypothesis_graph(case_id, tenant_id) or {"hypotheses": [], "edges": []}
    return APIResponse(data={
        "hypotheses": [
            {"hypothesis_id": item.get("hypothesis_id"), "status": item.get("status")}
            for item in (graph.get("hypotheses") or [])
        ],
        "note": "确定性假设评估结果；模型不得凭直觉覆盖。",
    })


@router.post("/internal/agent/tools/rca-analysis")
def internal_tool_rca_analysis(payload: dict[str, Any], request: Request) -> APIResponse:
    """E9：把 rca 候选归因分析器作为只读 Tool 暴露，模型不能越过它自创原因。

    规则引擎（candidates）是确定性白名单匹配；返回候选时同时给出缺失证据，
    绝不凭空捏造证据引用。本工具不创建任何 Task。
    """
    _require_internal_token(request)
    from server.app.rca.candidates import generate_candidates
    from server.app.rca.models import EvidenceInput
    list_fields = (
        "task_metadata", "top_functions", "tool_results", "suggestions", "failure_events",
    )
    dict_fields = ("ebpf_metrics", "sys_metrics", "baseline_diff", "agent_stats")
    evidence = EvidenceInput(**{
        **{key: payload.get(key) or [] for key in list_fields},
        **{key: payload.get(key) for key in dict_fields},
    })
    candidates = generate_candidates(evidence)
    return APIResponse(data={
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "description": item.description,
                "evidence_refs": item.evidence_refs,
                "rule_score": item.rule_score,
                "missing_evidence": item.missing_evidence,
            }
            for item in candidates
        ],
        "note": "确定性规则候选；模型可在其内排序/挑选，但不得新增候选或证据引用。",
    })


def _internal_tool_finish_impl(payload: dict[str, Any], request: Request) -> APIResponse:
    """结构化结论提交；必须引用真实存在的 Evidence ID，并落审计事件。"""
    _require_internal_token(request)
    case_id = str(payload.get("case_id") or "")
    tenant_id = _request_tenant()
    summary = str(payload.get("summary") or "").strip()
    evidence_ids = [str(item) for item in (payload.get("evidence_ids") or [])]
    if not evidence_ids:
        raise HTTPException(status_code=400, detail="NO_EVIDENCE_REFS")
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    fence_error = _tool_fence(case, tenant_id, payload, "finish_investigation")
    if fence_error:
        raise HTTPException(status_code=409, detail=fence_error)

    # 校验 Evidence ID 必须来自当前 Case 已接受的 Attachment 或 Diagnosis Evidence。
    known_evidence_ids: set[str] = set()
    for attachment in evidence_attachment_service.list_attachments(case_id, tenant_id):
        if attachment.get("status") == "EXCLUDED_BY_USER":
            continue
        known_evidence_ids.update(attachment.get("evidence_ids") or [])
    if hasattr(repo, "list_case_evidence"):
        for item in repo.list_case_evidence(case_id, tenant_id, status="ACTIVE"):
            known_evidence_ids.add(str(item.get("evidence_id") or ""))
        for item in repo.list_case_evidence(case_id, tenant_id, status="EXCLUDED"):
            known_evidence_ids.discard(str(item.get("evidence_id") or ""))
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
    requested_state = str(payload.get("state") or "PARTIALLY_CONFIRMED").upper()
    graph_edges = (graph or {}).get("edges") or []
    verified_state = verify_primary_confirmation(
        graph or {},
        requested_state,
        blocker_gaps=0,
        required_edge_missing=sum(
            1 for edge in graph_edges
            if edge.get("verification_state") not in {"OBSERVED", "SUPPORTED"}
        ),
    )
    if legacy_compat:
        verified_state = "PARTIALLY_CONFIRMED"
    conclusion = None
    if hasattr(repo, "submit_conclusion_revision"):
        conclusion = repo.submit_conclusion_revision(
            case_id=case_id,
            tenant_id=tenant_id,
            investigation_run_id=run_id or "",
            state=verified_state,
            causal_graph_revision_id=(graph or {}).get("graph_id"),
            claims=claims,
            limitations=list(payload.get("limitations") or []),
            abstention_reason=payload.get("abstention_reason"),
            report_text=summary,
            verifier_version="causal-report-verifier.v1",
        )
    repo.record_case_event(
        case_id,
        tenant_id,
        event_type="agent_finish_investigation",
        payload={
            "summary": summary,
            "evidence_refs": evidence_ids,
            "verifier": "causal-report-verifier.v1",
            "state": verified_state,
            "conclusion_id": conclusion.get("conclusion_id") if conclusion else None,
        },
        actor_id="mini-drop-pi-runtime",
    )
    if hasattr(repo, "persist_case_conclusion"):
        repo.persist_case_conclusion(
            case_id,
            tenant_id,
            summary=summary,
            evidence_refs=evidence_ids,
            limitations=list(payload.get("limitations") or []),
            actor_id="mini-drop-pi-runtime",
        )
    return APIResponse(data={
        "accepted": True,
        "case_id": case_id,
        "evidence_refs": evidence_ids,
        "verifier": "causal-report-verifier.v1",
        "state": verified_state,
        "conclusion_id": conclusion.get("conclusion_id") if conclusion else None,
        "event_type": "agent_finish_investigation",
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
        if event_type in {"turn_end", "assistant.completed", "final"}:
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
) -> APIResponse:
    """供 Sidecar 或工作台读取已持久化的 Runtime 事件（不含私有思维链）。"""
    _require_internal_token(request)
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    items = repo.list_agent_runtime_events(case_id, tenant_id)
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
