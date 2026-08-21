"""Case-scoped unknown-topology discovery orchestration.

The HTTP layer only coordinates catalog-backed ``network_discovery`` Tasks.
It never logs into an unmanaged host and never treats a TCP dependency as a
causal conclusion.  Durable Case events plus Task options make a run resumable
without introducing a second workflow database.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from mini_drop_contracts import get_collector_spec
from server.app.artifact_service import extract_artifact_json
from server.app.common_utils import status_value
from server.app.diagnosis.discovery_frontier import (
    FrontierBudget,
    build_discovery_snapshot_graph,
)
from server.app.diagnosis.network_discovery import case_dependency_graph_snapshot
from server.app.diagnosis.schemas import StrictModel
from server.app.http.auth import (
    request_principal as _request_principal,
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.runtime_services import (
    case_evidence_service,
    collection_supervisor,
    fanout_service,
    repo,
)
from server.app.schemas import APIResponse


router = APIRouter()

_ACTIVE_TASK_STATES = {"PENDING", "RUNNING", "UPLOADING", "ANALYZING"}
_TERMINAL_FAILURE_STATES = {"FAILED", "CANCELLED"}


class StartTopologyDiscoveryRequest(StrictModel):
    seed_agent_id: str = Field(default="", max_length=128)
    seed_pid: Optional[int] = Field(default=None, ge=1, le=4194304)
    max_hops: int = Field(default=2, ge=0, le=4)
    max_hosts: int = Field(default=12, ge=1, le=32)
    max_processes: int = Field(default=40, ge=1, le=200)
    max_edges: int = Field(default=200, ge=1, le=1000)
    max_parallel_tasks: int = Field(default=8, ge=1, le=8)
    include_loopback: bool = False
    collect_registered_peers: bool = True
    wait_timeout_sec: int = Field(default=20, ge=0, le=45)
    idempotency_key: str = Field(default="", max_length=256)


class AdvanceTopologyDiscoveryRequest(StrictModel):
    wait_timeout_sec: int = Field(default=20, ge=0, le=45)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _task_options(task: Any) -> dict[str, Any]:
    params = _value(task, "request_params", {}) or {}
    return dict(params.get("options") or {}) if isinstance(params, dict) else {}


def _task_id(task: Any) -> str:
    return str(_value(task, "id", "") or "")


def _task_status(task: Any) -> str:
    return status_value(_value(task, "status", ""))


def _agent_id(agent: Any) -> str:
    return str(_value(agent, "id", _value(agent, "agent_id", "")) or "")


def _run_tasks(case_id: str, run_id: str) -> list[Any]:
    tasks = []
    for task in getattr(repo, "tasks", {}).values():
        options = _task_options(task)
        if (
            str(options.get("case_id") or "") == case_id
            and str(options.get("discovery_run_id") or "") == run_id
            and str(_value(task, "collector_type", "")) == "network_discovery"
        ):
            tasks.append(task)
    return sorted(tasks, key=lambda item: (_value(item, "created_at", _utcnow()), _task_id(item)))


def _case_run_event(case_id: str, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    events = repo.list_case_events(case_id, tenant_id, limit=1000) or []
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("event_type") == "topology_discovery_started"
            and str(payload.get("run_id") or "") == run_id
        ):
            return event
    return None


def _completed_event(case_id: str, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    events = repo.list_case_events(case_id, tenant_id, limit=1000) or []
    for event in reversed(events):
        payload = event.get("payload") or {}
        if (
            event.get("event_type") == "topology_discovery_completed"
            and str(payload.get("run_id") or "") == run_id
        ):
            return event
    return None


def _seed_from_case(case: dict[str, Any], payload: StartTopologyDiscoveryRequest) -> tuple[str, int]:
    scope = case.get("target_scope") or {}
    candidates = [item for item in scope.get("instances") or [] if isinstance(item, dict)]
    if not candidates and scope.get("agent_id"):
        candidates = [{"agent_id": scope.get("agent_id"), "pid": scope.get("pid")}]
    requested_agent = payload.seed_agent_id
    requested_pid = payload.seed_pid
    if requested_agent or requested_pid:
        matches = [
            item for item in candidates
            if (not requested_agent or str(item.get("agent_id") or "") == requested_agent)
            and (requested_pid is None or int(item.get("pid") or 0) == requested_pid)
        ]
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="DISCOVERY_SEED_OUT_OF_CASE_SCOPE")
        return str(matches[0].get("agent_id") or ""), int(matches[0].get("pid") or 0)
    for item in candidates:
        agent_id = str(item.get("agent_id") or "")
        pid = int(item.get("pid") or 0)
        if agent_id and pid > 0:
            return agent_id, pid
    raise HTTPException(status_code=409, detail="DISCOVERY_SEED_REQUIRED")


def _collector_parameters(
    *, scope: str, include_loopback: bool, max_processes: int,
    listener_ports: list[int] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scope": scope,
        "include_loopback": include_loopback,
        "include_listeners": True,
        "include_connections": scope == "target",
        "max_processes": min(max(1, max_processes), 500),
        "max_sockets": 500,
        "max_events": 500,
    }
    if listener_ports:
        values["listener_ports"] = sorted({int(port) for port in listener_ports if 0 < int(port) <= 65535})[:3]
    return values


def _dispatch(
    *, case: dict[str, Any], tenant_id: str, run_id: str,
    agent_id: str, pid: int, hop: int, phase: str,
    parameters: dict[str, Any], authorized: bool,
    membership_snapshot_id: str,
    parent_task_id: str = "", authority_ref: str = "",
    runtime_generation: int = 1,
    expected_control_revision: int | None = None,
    expected_scope_revision: int | None = None,
    allowed_risk_levels: set[str] | frozenset[str] | None = None,
    max_collection_requests: int = 8,
    max_collection_duration_sec: int = 240,
) -> Any:
    result = _propose_or_dispatch(
        case=case,
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=agent_id,
        pid=pid,
        hop=hop,
        phase=phase,
        parameters=parameters,
        authorized=authorized,
        membership_snapshot_id=membership_snapshot_id,
        parent_task_id=parent_task_id,
        authority_ref=authority_ref,
        runtime_generation=runtime_generation,
        expected_control_revision=expected_control_revision,
        expected_scope_revision=expected_scope_revision,
        allowed_risk_levels=allowed_risk_levels,
        max_collection_requests=max_collection_requests,
        max_collection_duration_sec=max_collection_duration_sec,
        auto_dispatch=True,
    )
    task = result.get("task")
    if task is None:
        raise HTTPException(status_code=409, detail="TOPOLOGY_DISCOVERY_TASK_NOT_DISPATCHED")
    return task


def _propose_or_dispatch(
    *, case: dict[str, Any], tenant_id: str, run_id: str,
    agent_id: str, pid: int, hop: int, phase: str,
    parameters: dict[str, Any], authorized: bool,
    membership_snapshot_id: str,
    parent_task_id: str = "", authority_ref: str = "",
    runtime_generation: int = 1,
    expected_control_revision: int | None = None,
    expected_scope_revision: int | None = None,
    allowed_risk_levels: set[str] | frozenset[str] | None = None,
    max_collection_requests: int = 8,
    max_collection_duration_sec: int = 240,
    auto_dispatch: bool = True,
    agent_run_id: str | None = None,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    spec = get_collector_spec("network_discovery")
    if spec is None or not spec.enabled:
        raise HTTPException(status_code=409, detail="NETWORK_DISCOVERY_COLLECTOR_UNAVAILABLE")
    signature = hashlib.sha256(json.dumps(
        {"agent_id": agent_id, "pid": pid, "hop": hop, "phase": phase, "parameters": parameters},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]
    result = collection_supervisor.propose_and_dispatch(
        case_id=str(case.get("case_id") or case.get("id") or ""),
        tenant_id=tenant_id,
        collector_id="network_discovery",
        target_selector={"agent_id": agent_id, "target_pid": pid},
        parameters=parameters,
        information_goal=spec.information_goals[0],
        reason_summary="从已授权种子沿已注册 Agent 的 TCP 依赖边执行有界拓扑发现",
        idempotency_key=f"topology-discovery:{run_id}:{signature}",
        runtime_generation=max(1, int(runtime_generation or 1)),
        expected_control_revision=(
            int(expected_control_revision)
            if expected_control_revision is not None
            else int(case.get("control_revision") or 1)
        ),
        expected_scope_revision=(
            int(expected_scope_revision)
            if expected_scope_revision is not None
            else int(case.get("scope_revision") or 1)
        ),
        allowed_risk_levels=(
            allowed_risk_levels
            if allowed_risk_levels is not None
            else {"R0", "R1"}
        ),
        max_collection_requests=max_collection_requests,
        max_collection_duration_sec=max_collection_duration_sec,
        agent_run_id=agent_run_id,
        cycle_id=cycle_id,
        auto_dispatch=auto_dispatch,
        authorized_agent_ids={agent_id} if authorized else None,
        dispatch_context={
            "discovery_run_id": run_id,
            "discovery_hop": hop,
            "discovery_phase": phase,
            "discovery_parent_task_id": parent_task_id,
            "discovery_seed_ref": str((case.get("target_scope") or {}).get("service_id") or ""),
            "discovery_authority_evidence_ref": authority_ref,
            "membership_snapshot_id": membership_snapshot_id,
        },
    )
    proposal = result.get("proposal") or {}
    if proposal.get("status") == "REJECTED":
        errors = (proposal.get("validation_result") or {}).get("errors") or []
        raise HTTPException(
            status_code=409,
            detail="TOPOLOGY_DISCOVERY_COLLECTION_REJECTED:" + ",".join(str(item) for item in errors),
        )
    return result


def _artifact_payload(task: Any) -> dict[str, Any] | None:
    task_id = _task_id(task)
    artifacts = getattr(repo, "artifacts", {}).get(task_id, [])
    value = extract_artifact_json(artifacts, "network_discovery")
    return value if isinstance(value, dict) else None


def _member_snapshot_payload(
    member: dict[str, Any], *, captured_at: Any,
) -> dict[str, Any]:
    agent_id = str(member.get("agent_id") or "")
    return {
        "schema_version": "network_discovery.v1",
        "agent_id": agent_id,
        "boot_id": str(member.get("boot_id") or f"unknown-{agent_id}"),
        "observed_at": (
            captured_at.isoformat() if isinstance(captured_at, datetime)
            else str(captured_at or _utcnow().isoformat())
        ),
        "host_id": str(member.get("hostname") or agent_id),
        "host_addresses": [str(member.get("ip_addr") or "")],
        "online": bool(member.get("online", True)),
        "clock_quality": "unknown",
        "coverage": {
            "status": "unknown",
            "reasons": ["membership_only_no_observation"],
        },
        "processes": [],
        "listeners": [],
        "connections": [],
    }


def _enrich_payload(payload: dict[str, Any], task: Any, agent: Any) -> dict[str, Any]:
    result = dict(payload)
    nested_agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
    agent_id = str(
        result.get("agent_id") or nested_agent.get("agent_id") or _task_options(task).get("agent_id")
        or _value(task, "agent_id", "")
    )
    ip_addr = str(nested_agent.get("ip_addr") or _value(agent, "ip_addr", "") or "")
    result["agent_id"] = agent_id
    result["boot_id"] = str(result.get("boot_id") or nested_agent.get("boot_id") or f"unknown-{agent_id}")
    result["host_id"] = str(result.get("host_id") or nested_agent.get("hostname") or _value(agent, "hostname", agent_id) or agent_id)
    addresses = list(result.get("host_addresses") or [])
    if ip_addr and ip_addr not in addresses:
        addresses.append(ip_addr)
    result["host_addresses"] = addresses
    result["online"] = str(_value(agent, "status", "ONLINE") or "ONLINE") == "ONLINE"
    result.setdefault("clock_quality", "unknown")
    return result


def _inventory_payloads(
    tasks: list[Any], membership_snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    payloads: list[dict[str, Any]] = []
    ready_agents: set[str] = set()
    unavailable: list[str] = []
    agents = getattr(repo, "agents", {})
    for task in tasks:
        payload = _artifact_payload(task)
        agent_id = str(_value(task, "agent_id", "") or "")
        if payload is None:
            if _task_status(task) in _TERMINAL_FAILURE_STATES:
                unavailable.append(agent_id or _task_id(task))
            continue
        agent = agents.get(agent_id)
        payloads.append(_enrich_payload(payload, task, agent))
        ready_agents.add(agent_id)
    for member in membership_snapshot.get("members") or []:
        if not isinstance(member, dict):
            continue
        agent_id = str(member.get("agent_id") or "")
        if agent_id and agent_id not in ready_agents:
            payloads.append(_member_snapshot_payload(
                member, captured_at=membership_snapshot.get("captured_at"),
            ))
    return payloads, sorted(set(unavailable))


def _has_target_task(tasks: list[Any], agent_id: str, pid: int) -> bool:
    return any(
        str(_value(task, "agent_id", "") or "") == agent_id
        and int(_value(task, "target_pid", 0) or 0) == pid
        and str(_task_options(task).get("scope") or "target") == "target"
        for task in tasks
    )


def _has_listener_task(tasks: list[Any], agent_id: str, port: int) -> bool:
    for task in tasks:
        options = _task_options(task)
        if str(_value(task, "agent_id", "") or "") != agent_id:
            continue
        if str(options.get("scope") or "") != "host":
            continue
        if port in {int(item) for item in options.get("listener_ports") or []}:
            return True
    return False


def _target_is_in_case_scope(
    case: dict[str, Any], agent_id: str, pid: int,
) -> bool:
    """Return whether this exact process is part of the original Case scope.

    A topology expansion may discover another PID on the same Agent.  Agent ID
    equality alone must not make that process an original seed target; the
    frontier's run-scoped authority is required for the expansion task.
    """
    scoped_targets = collection_supervisor.case_scoped_process_targets(case)
    if scoped_targets:
        return (str(agent_id), int(pid)) in scoped_targets
    scoped_agents = collection_supervisor.case_scoped_agent_level_ids(case)
    if scoped_agents:
        return str(agent_id) in scoped_agents
    # Preserve the legacy unscoped/service-only Case behavior.  The
    # CollectionSupervisor applies its own deterministic target checks.
    return True


def _write_graph_artifact(
    *, seed_task: Any, run_id: str, build: Any, unavailable_agents: list[str],
) -> dict[str, Any]:
    graph = build.graph.model_dump(mode="json")
    payload = {
        "schema_version": "dependency-graph.v1",
        "discovery_run_id": run_id,
        "membership_snapshot_id": build.membership_snapshot_id,
        "seed_ref": build.seed_ref,
        "graph_digest": build.graph_digest,
        "snapshot_graph_digest": build.graph_digest,
        "graph_digest_kind": "snapshot",
        "coverage": {
            **build.coverage,
            "unavailable_agents": unavailable_agents,
        },
        "limitations": sorted(set([
            *build.limitations,
            *(["some_discovery_tasks_failed"] if unavailable_agents else []),
        ])),
        "frontier": build.frontier.model_dump(mode="json"),
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "identity_assertions": graph["identity_assertions"],
        "summary": {
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "managed_target_count": len(build.managed_frontier_targets),
            "external_endpoint_count": len(build.frontier.external_endpoints),
            "virtual_endpoint_count": len(build.frontier.virtual_endpoints),
            "coverage_ratio": float(build.coverage.get("managed_fraction") or 0.0),
        },
    }
    root = Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()
    output_dir = root / _task_id(seed_task)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"dependency_graph_{run_id}.json"
    raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    output_path.write_bytes(raw)
    artifact = {
        "artifact_type": "dependency_graph",
        "filename": output_path.name,
        "local_path": str(output_path),
        "content_type": "application/json",
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "metadata": {
            "schema_version": "dependency-graph.v1",
            "discovery_run_id": run_id,
            "membership_snapshot_id": build.membership_snapshot_id,
            "graph_digest": build.graph_digest,
            "snapshot_graph_digest": build.graph_digest,
            "graph_digest_kind": "snapshot",
            **payload["summary"],
            "coverage": payload["coverage"],
            "limitations": payload["limitations"],
        },
    }
    try:
        repo.add_artifacts(
            _task_id(seed_task), [artifact],
            attempt_id=str(_value(seed_task, "current_attempt_id", "") or "") or None,
        )
    except TypeError:
        repo.add_artifacts(_task_id(seed_task), [artifact])
    return payload


def _run_config(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    allowed_risks = payload.get("allowed_risk_levels")
    if allowed_risks is None:
        allowed_risks = ["R0", "R1"]
    return {
        "run_id": str(payload.get("run_id") or ""),
        "seed": dict(payload.get("seed") or {}),
        "budget": dict(payload.get("budget") or {}),
        "include_loopback": bool(payload.get("include_loopback")),
        "collect_registered_peers": bool(payload.get("collect_registered_peers", True)),
        "membership_snapshot_id": str(payload.get("membership_snapshot_id") or ""),
        "control_revision": int(payload.get("control_revision") or 1),
        "scope_revision": int(payload.get("scope_revision") or 1),
        "runtime_generation": int(payload.get("runtime_generation") or 1),
        "allowed_risk_levels": [
            str(item) for item in allowed_risks
        ],
        "max_collection_requests": int(payload.get("max_collection_requests") or 8),
        "max_collection_duration_sec": int(
            payload.get("max_collection_duration_sec") or 240
        ),
        "agent_run_id": str(payload.get("agent_run_id") or ""),
        "cycle_id": str(payload.get("cycle_id") or ""),
    }


def _stable_run_id(case_id: str, tenant_id: str, idempotency_key: str) -> str:
    if not idempotency_key:
        return f"discovery-{uuid4().hex[:20]}"
    digest = hashlib.sha256(
        f"{tenant_id}:{case_id}:{idempotency_key}".encode("utf-8")
    ).hexdigest()[:20]
    return f"discovery-{digest}"


def _assert_expected_revisions(
    case: dict[str, Any],
    *,
    expected_control_revision: int | None,
    expected_scope_revision: int | None,
) -> tuple[int, int]:
    control_revision = int(case.get("control_revision") or 1)
    scope_revision = int(case.get("scope_revision") or 1)
    if (
        expected_control_revision is not None
        and int(expected_control_revision) != control_revision
    ):
        raise HTTPException(status_code=409, detail="STALE_CONTROL_REVISION")
    if (
        expected_scope_revision is not None
        and int(expected_scope_revision) != scope_revision
    ):
        raise HTTPException(status_code=409, detail="STALE_SCOPE_REVISION")
    return control_revision, scope_revision


def _pending_run_result(
    *, case_id: str, tenant_id: str, run_id: str, started: dict[str, Any],
) -> dict[str, Any] | None:
    tasks = _run_tasks(case_id, run_id)
    if tasks:
        return None
    started_payload = started.get("payload") or {}
    proposal_id = str(started_payload.get("seed_proposal_id") or "")
    proposal = (
        repo.get_collection_proposal(proposal_id, case_id, tenant_id)
        if proposal_id and hasattr(repo, "get_collection_proposal")
        else None
    )
    if proposal is None:
        return None
    status = str(proposal.get("status") or "PROPOSED").upper()
    if status == "PROPOSED":
        return {
            "run_id": run_id,
            "status": "PROPOSED",
            "proposal": proposal,
            "task_ids": [],
            "membership_snapshot_id": started_payload.get("membership_snapshot_id"),
            "message": "拓扑发现已形成受控采集提案，等待执行授权",
        }
    if status == "REJECTED":
        return {
            "run_id": run_id,
            "status": "REJECTED",
            "proposal": proposal,
            "task_ids": [],
            "membership_snapshot_id": started_payload.get("membership_snapshot_id"),
        }
    return None


def _pending_expansion_proposals(
    case_id: str, tenant_id: str, run_id: str,
) -> list[dict[str, Any]]:
    if not hasattr(repo, "list_collection_proposals"):
        return []
    pending: list[dict[str, Any]] = []
    for proposal in repo.list_collection_proposals(case_id, tenant_id):
        if str(proposal.get("status") or "").upper() != "PROPOSED":
            continue
        validation = proposal.get("validation_result") or {}
        context = (validation.get("approval_context") or {}).get("dispatch_context") or {}
        if str(context.get("discovery_run_id") or "") == run_id:
            pending.append(proposal)
    return pending


def _advance(
    *, case: dict[str, Any], tenant_id: str, actor_id: str,
    config: dict[str, Any], wait_timeout_sec: int, auto_dispatch: bool = True,
) -> dict[str, Any]:
    run_id = config["run_id"]
    if int(config.get("scope_revision") or 1) != int(case.get("scope_revision") or 1):
        raise HTTPException(status_code=409, detail="DISCOVERY_SCOPE_REVISION_CHANGED")
    if int(config.get("control_revision") or 1) != int(case.get("control_revision") or 1):
        raise HTTPException(status_code=409, detail="DISCOVERY_CONTROL_REVISION_CHANGED")
    membership_snapshot_id = str(config.get("membership_snapshot_id") or "")
    membership_snapshot = repo.get_membership_snapshot(
        str(case.get("case_id") or case.get("id") or ""),
        tenant_id,
        membership_snapshot_id,
    ) if membership_snapshot_id else None
    if membership_snapshot is None:
        raise HTTPException(status_code=409, detail="DISCOVERY_MEMBERSHIP_SNAPSHOT_MISSING")
    if int(membership_snapshot.get("scope_revision") or 1) != int(
        case.get("scope_revision") or 1,
    ):
        raise HTTPException(status_code=409, detail="DISCOVERY_SCOPE_REVISION_CHANGED")
    pending_proposals = _pending_expansion_proposals(
        str(case.get("case_id") or case.get("id") or ""), tenant_id, run_id,
    )
    if pending_proposals:
        return {
            "run_id": run_id,
            "status": "PROPOSED",
            "proposals": pending_proposals,
            "task_ids": [
                _task_id(task)
                for task in _run_tasks(
                    str(case.get("case_id") or case.get("id") or ""), run_id,
                )
            ],
            "membership_snapshot_id": membership_snapshot_id,
            "message": "拓扑扩展已形成受控采集提案，等待执行授权",
        }
    deadline = time.monotonic() + max(0, wait_timeout_sec)
    while True:
        tasks = _run_tasks(str(case.get("case_id") or case.get("id") or ""), run_id)
        payloads, unavailable = _inventory_payloads(tasks, membership_snapshot)
        real_payloads = [item for item in payloads if item.get("processes") or item.get("listeners") or item.get("connections")]
        if not real_payloads:
            active = any(_task_status(task) in _ACTIVE_TASK_STATES for task in tasks)
            if time.monotonic() < deadline and active:
                time.sleep(0.25)
                continue
            result = {
                "run_id": run_id,
                "status": "COLLECTING" if active else "PARTIAL",
                "task_ids": [_task_id(task) for task in tasks],
                "tasks": [{"task_id": _task_id(task), "status": _task_status(task)} for task in tasks],
                "message": "等待 Agent 上传 network_discovery 快照",
                "unavailable_agents": unavailable,
            }
            if not active and _completed_event(
                str(case.get("case_id") or case.get("id") or ""), tenant_id, run_id,
            ) is None:
                repo.record_case_event(
                    str(case.get("case_id") or case.get("id") or ""), tenant_id,
                    event_type="topology_discovery_completed",
                    payload={
                        "run_id": run_id,
                        "graph_digest": "",
                        "evidence_ids": [],
                        "coverage": {
                            "conclusion": "insufficient_coverage",
                            "registered_agent_snapshots": len(membership_snapshot.get("members") or []),
                            "agent_artifact_count": 0,
                        },
                        "limitations": ["no_network_discovery_artifact_available"],
                        "node_count": 0,
                        "edge_count": 0,
                        "task_ids": result["task_ids"],
                    },
                    actor_id=actor_id,
                )
            return result

        budget = FrontierBudget(**config["budget"])
        build = build_discovery_snapshot_graph(
            payloads,
            seed_ref=config["seed"],
            membership_snapshot_id=membership_snapshot_id,
            discovery_run_id=run_id,
            budget=budget,
        )
        graph_nodes = build.graph.node_map()
        scheduled: list[Any] = []
        proposed: list[dict[str, Any]] = []
        if config["collect_registered_peers"]:
            distinct_agents = {
                str(_value(task, "agent_id", "") or "") for task in tasks
                if str(_value(task, "agent_id", "") or "")
            }
            target_task_count = sum(
                1 for task in tasks if str(_task_options(task).get("scope") or "target") == "target"
            )
            for target in sorted(
                build.managed_frontier_targets,
                key=lambda item: (item.hop, item.agent_id, item.entity_id),
            ):
                if len(scheduled) >= budget.max_parallel_tasks:
                    break
                if target.hop > budget.max_hops or not target.agent_id:
                    continue
                node = graph_nodes.get(target.entity_id)
                if node is None:
                    continue
                if target.agent_id not in distinct_agents and len(distinct_agents) >= budget.max_hosts:
                    continue
                if node.entity_type == "managed_host_endpoint" and node.endpoint is not None:
                    port = node.endpoint.port
                    if _has_listener_task(tasks, target.agent_id, port):
                        continue
                    dispatch_result = _propose_or_dispatch(
                        case=case, tenant_id=tenant_id, run_id=run_id,
                        agent_id=target.agent_id, pid=1, hop=target.hop,
                        phase="resolve_listener",
                        parameters=_collector_parameters(
                            scope="host", include_loopback=config["include_loopback"],
                            max_processes=min(500, budget.max_processes * 10),
                            listener_ports=[port],
                        ),
                        authorized=True,
                        membership_snapshot_id=membership_snapshot_id,
                        parent_task_id=_task_id(tasks[0]),
                        authority_ref=f"dependency-graph:{build.graph_digest}",
                        runtime_generation=int(config.get("runtime_generation") or 1),
                        expected_control_revision=int(config.get("control_revision") or 1),
                        expected_scope_revision=int(config.get("scope_revision") or 1),
                        allowed_risk_levels=set(
                            config.get("allowed_risk_levels", ["R0", "R1"])
                        ),
                        max_collection_requests=int(config.get("max_collection_requests") or 8),
                        max_collection_duration_sec=int(
                            config.get("max_collection_duration_sec") or 240
                        ),
                        auto_dispatch=auto_dispatch,
                        agent_run_id=str(config.get("agent_run_id") or "") or None,
                        cycle_id=str(config.get("cycle_id") or "") or None,
                    )
                    if dispatch_result.get("task") is not None:
                        scheduled.append(dispatch_result["task"])
                    else:
                        proposed.append(dispatch_result.get("proposal") or {})
                        break
                    distinct_agents.add(target.agent_id)
                elif node.entity_type == "process" and node.process is not None:
                    pid = node.process.pid
                    if _has_target_task(tasks, target.agent_id, pid):
                        continue
                    if target_task_count + len(scheduled) >= budget.max_processes:
                        continue
                    dispatch_result = _propose_or_dispatch(
                        case=case, tenant_id=tenant_id, run_id=run_id,
                        agent_id=target.agent_id, pid=pid, hop=target.hop,
                        phase="expand_process",
                        parameters=_collector_parameters(
                            scope="target", include_loopback=config["include_loopback"],
                            max_processes=min(500, budget.max_processes * 10),
                        ),
                        authorized=not _target_is_in_case_scope(
                            case, target.agent_id, pid,
                        ),
                        membership_snapshot_id=membership_snapshot_id,
                        parent_task_id=_task_id(tasks[0]),
                        authority_ref=f"dependency-graph:{build.graph_digest}",
                        runtime_generation=int(config.get("runtime_generation") or 1),
                        expected_control_revision=int(config.get("control_revision") or 1),
                        expected_scope_revision=int(config.get("scope_revision") or 1),
                        allowed_risk_levels=set(
                            config.get("allowed_risk_levels", ["R0", "R1"])
                        ),
                        max_collection_requests=int(config.get("max_collection_requests") or 8),
                        max_collection_duration_sec=int(
                            config.get("max_collection_duration_sec") or 240
                        ),
                        auto_dispatch=auto_dispatch,
                        agent_run_id=str(config.get("agent_run_id") or "") or None,
                        cycle_id=str(config.get("cycle_id") or "") or None,
                    )
                    if dispatch_result.get("task") is not None:
                        scheduled.append(dispatch_result["task"])
                    else:
                        proposed.append(dispatch_result.get("proposal") or {})
                        break
                    distinct_agents.add(target.agent_id)

        if proposed:
            repo.record_case_event(
                str(case.get("case_id") or case.get("id") or ""), tenant_id,
                event_type="topology_discovery_expansion_proposed",
                payload={
                    "run_id": run_id,
                    "graph_digest": build.graph_digest,
                    "proposal_ids": [
                        str(item.get("proposal_id") or "") for item in proposed
                    ],
                },
                actor_id=actor_id,
            )
            return {
                "run_id": run_id,
                "status": "PROPOSED",
                "proposals": proposed,
                "task_ids": [_task_id(task) for task in tasks],
                "graph_digest": build.graph_digest,
                "coverage": build.coverage,
                "limitations": build.limitations,
                "message": "拓扑扩展已形成受控采集提案，等待执行授权",
            }

        if scheduled:
            repo.record_case_event(
                str(case.get("case_id") or case.get("id") or ""), tenant_id,
                event_type="topology_discovery_expanded",
                payload={
                    "run_id": run_id,
                    "graph_digest": build.graph_digest,
                    "scheduled_task_ids": [_task_id(task) for task in scheduled],
                    "hop_targets": [
                        {"agent_id": _value(task, "agent_id", ""), "pid": _value(task, "target_pid", 0)}
                        for task in scheduled
                    ],
                },
                actor_id=actor_id,
            )
            if time.monotonic() < deadline:
                time.sleep(0.25)
                continue
            tasks = _run_tasks(str(case.get("case_id") or case.get("id") or ""), run_id)
            return {
                "run_id": run_id,
                "status": "COLLECTING",
                "task_ids": [_task_id(task) for task in tasks],
                "graph_digest": build.graph_digest,
                "coverage": build.coverage,
                "limitations": build.limitations,
            }

        # An Agent can upload the snapshot before the native Task reaches DONE.
        # Do not finalize the discovery in that interval: Case Evidence is
        # materialized at the Task terminal transition, and completing early
        # would persist a stale aggregate digest/evidence list.
        active_tasks = [
            task for task in tasks if _task_status(task) in _ACTIVE_TASK_STATES
        ]
        if active_tasks and time.monotonic() < deadline:
            time.sleep(0.25)
            continue
        if active_tasks:
            return {
                "run_id": run_id,
                "status": "COLLECTING",
                "task_ids": [_task_id(task) for task in tasks],
                "graph_digest": build.graph_digest,
                "coverage": build.coverage,
                "limitations": build.limitations,
                "message": "Agent 已上传或正在生成快照，等待原生 Task 进入终态后物化 Evidence",
            }

        seed_task = next(
            (task for task in tasks if str(_task_options(task).get("discovery_phase") or "") == "seed"),
            tasks[0],
        )
        graph_payload = _write_graph_artifact(
            seed_task=seed_task, run_id=run_id, build=build, unavailable_agents=unavailable,
        )
        evidence_ids: list[str] = []
        for task in tasks:
            if _task_status(task) != "DONE" or _artifact_payload(task) is None:
                continue
            evidence_ids.extend(case_evidence_service.materialize_task_artifacts(
                str(case.get("case_id") or case.get("id") or ""), tenant_id,
                task_id=_task_id(task), actor_id=actor_id,
            ))
        evidence_ids = sorted(set(evidence_ids))
        # The coordinator's snapshot graph is intentionally distinct from the
        # Case aggregate graph.  Materializing Evidence can reconcile client /
        # server observations and bind canonical Evidence IDs, so its digest
        # may legitimately change.  Persist both values for an auditable
        # hand-off instead of making downstream readers compare unlike forms.
        aggregate_graph = case_dependency_graph_snapshot(repo, str(case.get("case_id") or case.get("id") or ""), tenant_id)
        snapshot_graph_digest = str(build.graph_digest)
        case_aggregate_graph_digest = str(aggregate_graph.get("graph_digest") or "")
        graph_payload["snapshot_graph_digest"] = snapshot_graph_digest
        graph_payload["case_aggregate_graph_digest"] = case_aggregate_graph_digest or None
        graph_payload["graph_digest_kind"] = "snapshot"
        existing = _completed_event(
            str(case.get("case_id") or case.get("id") or ""), tenant_id, run_id,
        )
        if existing is None or (existing.get("payload") or {}).get("graph_digest") != build.graph_digest:
            repo.record_case_event(
                str(case.get("case_id") or case.get("id") or ""), tenant_id,
                event_type="topology_discovery_completed",
                payload={
                    "run_id": run_id,
                    "graph_digest": build.graph_digest,
                    "snapshot_graph_digest": snapshot_graph_digest,
                    "case_aggregate_graph_digest": case_aggregate_graph_digest or None,
                    "graph_digest_kind": "snapshot",
                    "evidence_ids": evidence_ids,
                    "coverage": graph_payload["coverage"],
                    "limitations": graph_payload["limitations"],
                    "node_count": graph_payload["summary"]["node_count"],
                    "edge_count": graph_payload["summary"]["edge_count"],
                    "task_ids": [_task_id(task) for task in tasks],
                },
                actor_id=actor_id,
            )
        status = "COMPLETED" if graph_payload["coverage"].get("conclusion") != "insufficient_coverage" else "PARTIAL"
        return {
            "run_id": run_id,
            "status": status,
            "task_ids": [_task_id(task) for task in tasks],
            "evidence_ids": evidence_ids,
            "graph": graph_payload,
            "snapshot_graph_digest": snapshot_graph_digest,
            "case_aggregate_graph_digest": case_aggregate_graph_digest or None,
        }


def start_topology_discovery_run(
    case_id: str,
    tenant_id: str,
    actor_id: str,
    payload: StartTopologyDiscoveryRequest,
    *,
    auto_dispatch: bool = True,
    runtime_generation: int = 1,
    expected_control_revision: int | None = None,
    expected_scope_revision: int | None = None,
    allowed_risk_levels: set[str] | frozenset[str] | None = None,
    max_collection_requests: int = 8,
    max_collection_duration_sec: int = 240,
    agent_run_id: str | None = None,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    """Start one bounded Case discovery or persist only its seed proposal."""
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    if case.get("state") in {"STOPPED", "RESOLVED"}:
        raise HTTPException(status_code=409, detail="CASE_TERMINAL")
    control_revision, scope_revision = _assert_expected_revisions(
        case,
        expected_control_revision=expected_control_revision,
        expected_scope_revision=expected_scope_revision,
    )
    seed_agent_id, seed_pid = _seed_from_case(case, payload)
    run_id = _stable_run_id(case_id, tenant_id, payload.idempotency_key)
    existing = _case_run_event(case_id, tenant_id, run_id)
    if existing is not None:
        pending = _pending_run_result(
            case_id=case_id, tenant_id=tenant_id, run_id=run_id, started=existing,
        )
        if pending is not None:
            return pending
        return _advance(
            case=case,
            tenant_id=tenant_id,
            actor_id=actor_id,
            config=_run_config(existing),
            wait_timeout_sec=payload.wait_timeout_sec,
        )
    budget = FrontierBudget(
        max_hops=payload.max_hops,
        max_hosts=payload.max_hosts,
        max_processes=payload.max_processes,
        max_edges=payload.max_edges,
        max_parallel_tasks=payload.max_parallel_tasks,
    )
    target_scope = case.get("target_scope") or {}
    membership = fanout_service.build_membership_snapshot(
        environment_id=str(target_scope.get("environment_id") or case.get("environment") or "unknown"),
        cluster_id=str(target_scope.get("cluster_id") or ""),
        scope_revision=int(case.get("scope_revision") or 1),
        topology_version="network-discovery.v1",
    )
    repo.create_membership_snapshot(
        case_id, tenant_id, membership.model_dump(mode="json"),
    )
    seed_dispatch = _propose_or_dispatch(
        case=case, tenant_id=tenant_id, run_id=run_id,
        agent_id=seed_agent_id, pid=seed_pid, hop=0, phase="seed",
        parameters=_collector_parameters(
            scope="target", include_loopback=payload.include_loopback,
            max_processes=min(500, payload.max_processes * 10),
        ),
        authorized=False,
        membership_snapshot_id=membership.snapshot_id,
        runtime_generation=runtime_generation,
        expected_control_revision=control_revision,
        expected_scope_revision=scope_revision,
        allowed_risk_levels=(
            allowed_risk_levels
            if allowed_risk_levels is not None
            else {"R0", "R1"}
        ),
        max_collection_requests=max_collection_requests,
        max_collection_duration_sec=max_collection_duration_sec,
        auto_dispatch=auto_dispatch,
        agent_run_id=agent_run_id,
        cycle_id=cycle_id,
    )
    seed_task = seed_dispatch.get("task")
    seed_proposal = seed_dispatch.get("proposal") or {}
    config = {
        "run_id": run_id,
        "seed": {"agent_id": seed_agent_id, "pid": seed_pid},
        "budget": budget.model_dump(mode="json"),
        "include_loopback": payload.include_loopback,
        "collect_registered_peers": payload.collect_registered_peers,
        "membership_snapshot_id": membership.snapshot_id,
        "control_revision": control_revision,
        "scope_revision": scope_revision,
        "runtime_generation": max(1, int(runtime_generation or 1)),
        "allowed_risk_levels": sorted(
            allowed_risk_levels
            if allowed_risk_levels is not None
            else {"R0", "R1"}
        ),
        "max_collection_requests": max_collection_requests,
        "max_collection_duration_sec": max_collection_duration_sec,
        "agent_run_id": agent_run_id or "",
        "cycle_id": cycle_id or "",
    }
    repo.record_case_event(
        case_id, tenant_id,
        event_type="topology_discovery_started",
        payload={
            **config,
            "seed_task_id": _task_id(seed_task) if seed_task is not None else "",
            "seed_proposal_id": str(seed_proposal.get("proposal_id") or ""),
            "execution_authority": "AUTO_READ_LOW" if auto_dispatch else "PROPOSE_ONLY",
        },
        actor_id=actor_id,
    )
    if seed_task is None:
        return {
            "run_id": run_id,
            "status": "PROPOSED",
            "proposal": seed_proposal,
            "task_ids": [],
            "membership_snapshot_id": membership.snapshot_id,
            "message": "拓扑发现已形成受控采集提案，等待执行授权",
        }
    return _advance(
        case=case, tenant_id=tenant_id, actor_id=actor_id,
        config=config, wait_timeout_sec=payload.wait_timeout_sec,
    )


def advance_topology_discovery_run(
    case_id: str,
    tenant_id: str,
    actor_id: str,
    run_id: str,
    *,
    wait_timeout_sec: int = 20,
    expected_control_revision: int | None = None,
    expected_scope_revision: int | None = None,
    auto_dispatch: bool = True,
    allowed_risk_levels: set[str] | frozenset[str] | None = None,
    max_collection_requests: int | None = None,
    max_collection_duration_sec: int | None = None,
) -> dict[str, Any]:
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    _assert_expected_revisions(
        case,
        expected_control_revision=expected_control_revision,
        expected_scope_revision=expected_scope_revision,
    )
    event = _case_run_event(case_id, tenant_id, run_id)
    if event is None:
        raise HTTPException(status_code=404, detail="TOPOLOGY_DISCOVERY_RUN_NOT_FOUND")
    pending = _pending_run_result(
        case_id=case_id, tenant_id=tenant_id, run_id=run_id, started=event,
    )
    if pending is not None:
        return pending
    config = _run_config(event)
    if allowed_risk_levels is not None:
        config["allowed_risk_levels"] = sorted(
            set(config.get("allowed_risk_levels") or []) & set(allowed_risk_levels)
        )
    if max_collection_requests is not None:
        config["max_collection_requests"] = min(
            int(config.get("max_collection_requests") or 8),
            int(max_collection_requests),
        )
    if max_collection_duration_sec is not None:
        config["max_collection_duration_sec"] = min(
            int(config.get("max_collection_duration_sec") or 240),
            int(max_collection_duration_sec),
        )
    return _advance(
        case=case,
        tenant_id=tenant_id,
        actor_id=actor_id,
        config=config,
        wait_timeout_sec=wait_timeout_sec,
        auto_dispatch=auto_dispatch,
    )


def get_topology_discovery_run(
    case_id: str, tenant_id: str, run_id: str,
) -> dict[str, Any]:
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="CASE_NOT_FOUND")
    started = _case_run_event(case_id, tenant_id, run_id)
    if started is None:
        raise HTTPException(status_code=404, detail="TOPOLOGY_DISCOVERY_RUN_NOT_FOUND")
    pending = _pending_run_result(
        case_id=case_id, tenant_id=tenant_id, run_id=run_id, started=started,
    )
    if pending is not None:
        return {
            **pending,
            "started": started.get("payload") or {},
            "completed": None,
            "tasks": [],
        }
    completed = _completed_event(case_id, tenant_id, run_id)
    tasks = _run_tasks(case_id, run_id)
    completed_payload = (completed or {}).get("payload") or None
    completed_status = "COLLECTING"
    if completed_payload:
        completed_status = (
            "PARTIAL"
            if (completed_payload.get("coverage") or {}).get("conclusion") == "insufficient_coverage"
            else "COMPLETED"
        )
    return {
        "run_id": run_id,
        "status": completed_status,
        "started": started.get("payload") or {},
        "completed": completed_payload,
        "tasks": [
            {
                "task_id": _task_id(task),
                "agent_id": _value(task, "agent_id", ""),
                "target_pid": _value(task, "target_pid", 0),
                "status": _task_status(task),
                "phase": _task_options(task).get("discovery_phase"),
                "hop": _task_options(task).get("discovery_hop"),
            }
            for task in tasks
        ],
    }


@router.post("/api/v1/cases/{case_id}/topology/discovery-runs")
def start_topology_discovery(
    case_id: str,
    payload: StartTopologyDiscoveryRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    return APIResponse(data=start_topology_discovery_run(
        case_id,
        _request_tenant(),
        _request_principal(request),
        payload,
    ))


@router.post("/api/v1/cases/{case_id}/topology/discovery-runs/{run_id}/advance")
def advance_topology_discovery(
    case_id: str,
    run_id: str,
    payload: AdvanceTopologyDiscoveryRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    return APIResponse(data=advance_topology_discovery_run(
        case_id,
        _request_tenant(),
        _request_principal(request),
        run_id,
        wait_timeout_sec=payload.wait_timeout_sec,
    ))


@router.get("/api/v1/cases/{case_id}/topology/discovery-runs/{run_id}")
def get_topology_discovery(case_id: str, run_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    return APIResponse(data=get_topology_discovery_run(
        case_id, _request_tenant(), run_id,
    ))


__all__ = [
    "AdvanceTopologyDiscoveryRequest",
    "StartTopologyDiscoveryRequest",
    "advance_topology_discovery_run",
    "get_topology_discovery_run",
    "router",
    "start_topology_discovery_run",
]
