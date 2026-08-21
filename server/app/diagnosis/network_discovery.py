"""Network discovery EvidenceProjection and case-level dependency graph views.

Raw connection events remain in the Artifact store.  This module creates a
bounded, deterministic graph projection for the Evidence chain and merges the
active projections of one Case without turning communication into causality.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from server.app.diagnosis.dependency_graph import (
    DependencyEdge,
    DependencyGraph,
    DependencyMetrics,
    DependencyNode,
    IdentityAssertion,
    ObservationWindow,
)
from server.app.diagnosis.discovery_frontier import (
    AgentNetworkInventory,
    build_dependency_graph,
    coverage_requires_abstention,
)


MAX_PROJECTION_BYTES = 512 * 1024
MAX_GRAPH_EDGES = 200
MAX_GRAPH_PROCESSES = 80


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def _projection_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(content)).hexdigest()


def _aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _normalized_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Lift the current Agent's nested host identity into the canonical shape."""
    normalized = dict(payload)
    agent = payload.get("agent")
    if isinstance(agent, Mapping):
        normalized.setdefault("agent_id", agent.get("agent_id"))
        normalized.setdefault("boot_id", agent.get("boot_id"))
        normalized.setdefault("host_id", agent.get("hostname"))
        addresses = list(normalized.get("host_addresses") or [])
        if agent.get("ip_addr"):
            addresses.append(str(agent["ip_addr"]))
        normalized["host_addresses"] = sorted(set(addresses))
    return normalized


def _membership_inventories(
    membership_snapshot: Mapping[str, Any] | None,
) -> list[AgentNetworkInventory]:
    if not membership_snapshot:
        return []
    captured_at = _aware_datetime(membership_snapshot.get("captured_at"))
    inventories: list[AgentNetworkInventory] = []
    for member in membership_snapshot.get("members") or []:
        if not isinstance(member, Mapping):
            continue
        agent_id = str(member.get("agent_id") or "").strip()
        ip_addr = str(member.get("ip_addr") or "").strip()
        if not agent_id:
            continue
        inventories.append(AgentNetworkInventory(
            agent_id=agent_id,
            boot_id=str(member.get("boot_id") or "membership-only"),
            observed_at=captured_at,
            host_id=str(member.get("hostname") or agent_id),
            host_addresses=[ip_addr] if ip_addr else [],
            online=bool(member.get("online", True)),
            coverage_status="unknown",
            coverage_reasons=["membership_only_no_observation"],
        ))
    return inventories


def _seed_ref(payload: Mapping[str, Any], inventory: AgentNetworkInventory) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    seed_pid = payload.get("seed_pid") or summary.get("seed_pid")
    try:
        seed_pid = int(seed_pid)
    except (TypeError, ValueError):
        seed_pid = 0
    for process in inventory.processes:
        if process.pid == seed_pid:
            return process.stable_ref()
    return f"agent:{inventory.agent_id}:pid:{seed_pid}" if seed_pid else f"agent:{inventory.agent_id}"


def _bounded_graph(graph: DependencyGraph) -> tuple[DependencyGraph, bool]:
    """Keep a valid graph if a future collector produces unusually rich nodes."""
    if len(_json_bytes(graph.canonical_dict())) <= MAX_PROJECTION_BYTES - 32 * 1024:
        return graph, False
    for edge_limit, assertion_limit in ((120, 300), (60, 120), (30, 60)):
        edges = graph.edges[:edge_limit]
        referenced = {
            entity
            for edge in edges
            for entity in (edge.source_entity, edge.target_entity)
        }
        nodes = [node for node in graph.nodes if node.entity_id in referenced]
        assertions = [
            item for item in graph.identity_assertions
            if item.subject in referenced or item.object in referenced
        ][:assertion_limit]
        candidate = DependencyGraph(
            nodes=nodes, edges=edges, identity_assertions=assertions,
        )
        if len(_json_bytes(candidate.canonical_dict())) <= MAX_PROJECTION_BYTES - 32 * 1024:
            return candidate, True
    empty = DependencyGraph(nodes=[], edges=[], identity_assertions=[])
    return empty, True


def build_network_discovery_projection(
    payload: Mapping[str, Any],
    *,
    source_bytes: int = 0,
    raw_locator: str | None = None,
    projection_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the dedicated, bounded DEPENDENCY_GRAPH projection envelope."""
    normalized = _normalized_payload(payload)
    inventory = AgentNetworkInventory.from_payload(normalized)
    context = dict(projection_context or {})
    membership = context.get("membership_snapshot")
    inventories = [inventory, *_membership_inventories(
        membership if isinstance(membership, Mapping) else None,
    )]
    graph = build_dependency_graph(
        inventories,
        max_edges=MAX_GRAPH_EDGES,
        max_processes=MAX_GRAPH_PROCESSES,
        evidence_ref=str(
            context.get("evidence_id") or context.get("evidence_ref") or ""
        ) or None,
    )
    graph, graph_truncated = _bounded_graph(graph)
    graph_payload = graph.canonical_dict()
    coverage = dict(payload.get("coverage") or {})
    external = sorted(
        node.entity_id for node in graph.nodes
        if node.entity_type == "external_unmanaged_endpoint"
    )
    unresolved = sorted(
        node.entity_id for node in graph.nodes
        if node.entity_type == "managed_host_endpoint"
    )
    virtual = sorted(
        node.entity_id for node in graph.nodes
        if node.entity_type == "virtual_endpoint"
    )
    coverage_status = str(coverage.get("status") or "unknown").lower()
    coverage_blocked = coverage_requires_abstention(
        coverage_status,
        reasons=coverage.get("reasons") or [],
        has_observations=bool(
            inventory.processes or inventory.listeners or inventory.connections
        ),
    )
    coverage.update({
        "dependency_node_count": len(graph.nodes),
        "dependency_edge_count": len(graph.edges),
        "managed_unresolved_count": len(unresolved),
        "external_unmanaged_count": len(external),
        "virtual_endpoint_count": len(virtual),
        "membership_snapshot_bound": bool(context.get("membership_snapshot_id")),
        "conclusion": (
            "dependency" if (
                graph.edges
                and not coverage_blocked
                and not external
                and not unresolved
                and not virtual
            )
            else "insufficient_coverage"
        ),
    })
    effective_quality = coverage_status
    if (
        coverage.get("conclusion") == "insufficient_coverage"
        and effective_quality == "complete"
    ):
        # The source collector may have completed its local scan while the
        # Case still lacks a resolvable peer/endpoint.  Do not expose that as
        # globally complete quality in the Agent-facing projection.
        effective_quality = "partial"
    limitations = [str(item) for item in payload.get("limitations") or [] if item]
    if not context.get("membership_snapshot_id"):
        limitations.append("membership_snapshot_missing_remote_agent_mapping_is_limited")
    limitations.append("dependency_edges_are_observations_not_causal_claims")
    limitations = list(dict.fromkeys(limitations))
    summary = (
        f"网络依赖发现：{len(graph.nodes)} 个节点、{len(graph.edges)} 条通信边；"
        f"{len(external)} 个未托管端点、{len(virtual)} 个虚拟端点"
    )
    content: dict[str, Any] = {
        "artifact_type": "network_discovery",
        "summary": summary,
        "graph": graph_payload,
        "graph_digest": graph.digest(),
        "graph_semantics": "dependency_only_not_causal",
        "source_evidence_id": str(
            context.get("evidence_id") or context.get("evidence_ref") or ""
        ) or None,
        "seed_ref": context.get("discovery_seed_ref") or _seed_ref(normalized, inventory),
        "discovery_run_id": context.get("discovery_run_id"),
        "membership_snapshot_id": context.get("membership_snapshot_id"),
        "scope_revision": context.get("scope_revision"),
        "coverage": coverage,
        "unresolved_endpoints": unresolved,
        "external_endpoints": external,
        "virtual_endpoints": virtual,
        "limitations": limitations,
        "signals": {},
        "top_items": [],
        "samples": [],
        "log_events": [],
        "errors": [],
        "window": {
            "start": inventory.observed_at.isoformat(),
            "end": inventory.observed_at.isoformat(),
            "source": "network_discovery_snapshot",
        },
        "target": context.get("target_ref") or _seed_ref(normalized, inventory),
        "quality": effective_quality or "unknown",
        "interpretation_hints": [{
            "kind": "derived",
            "text": summary,
            "source": "deterministic-network-discovery-parser",
        }],
        "raw_ref": {"locator": raw_locator, "artifact_type": "network_discovery"},
    }
    projected_bytes = len(_json_bytes(content))
    truncated = graph_truncated or projected_bytes > MAX_PROJECTION_BYTES
    return {
        "projection_kind": "DEPENDENCY_GRAPH",
        "content": content,
        "projection_hash": _projection_hash(content),
        "truncated": truncated,
        "source_bytes": int(source_bytes or projected_bytes),
        "projected_bytes": projected_bytes,
        "projection_schema": "dependency-graph-projection.v1",
        "projection_version": 1,
    }


def _merge_node(previous: DependencyNode, current: DependencyNode) -> DependencyNode:
    winner, other = (current, previous) if current.confidence > previous.confidence else (previous, current)
    payload = winner.model_dump(mode="json")
    payload["attributes"] = {**other.attributes, **winner.attributes}
    listening = sorted(set(
        list(previous.attributes.get("listening_endpoints") or [])
        + list(current.attributes.get("listening_endpoints") or [])
    ))
    if listening:
        payload["attributes"]["listening_endpoints"] = listening
    return DependencyNode.model_validate(payload)


def _merge_edge(previous: DependencyEdge, current: DependencyEdge) -> DependencyEdge:
    connect_latencies = [
        value for value in (
            previous.metrics.connect_p95_ms, current.metrics.connect_p95_ms,
        ) if value is not None
    ]
    rtt_latencies = [
        value for value in (
            previous.metrics.rtt_p95_ms, current.metrics.rtt_p95_ms,
        ) if value is not None
    ]
    metrics = DependencyMetrics(**{
        field: max(getattr(previous.metrics, field), getattr(current.metrics, field))
        for field in (
            "connections", "active_connections", "failures", "resets", "timeouts",
            "bytes_sent", "bytes_received", "retransmissions",
        )
    }, connect_p95_ms=max(connect_latencies) if connect_latencies else None,
        rtt_p95_ms=max(rtt_latencies) if rtt_latencies else None)
    return DependencyEdge.create(
        source_entity=previous.source_entity,
        target_entity=previous.target_entity,
        relation=previous.relation,
        protocol=previous.protocol,
        destination_port=previous.destination_port,
        source_endpoint=previous.source_endpoint or current.source_endpoint,
        target_endpoint=previous.target_endpoint or current.target_endpoint,
        window=ObservationWindow(
            start=min(previous.window.start, current.window.start),
            end=max(previous.window.end, current.window.end),
        ),
        metrics=metrics,
        identity_confidence=max(previous.identity_confidence, current.identity_confidence),
        direction_confidence=max(previous.direction_confidence, current.direction_confidence),
        observation_points=sorted(set(previous.observation_points + current.observation_points)),
        evidence_refs=sorted(set(previous.evidence_refs + current.evidence_refs)),
        event_refs=sorted(set(previous.event_refs + current.event_refs)),
    )


def _entity_resolution_rank(node: DependencyNode | None) -> int:
    if node is None:
        return 0
    return {
        "process": 5,
        "instance": 4,
        "service": 3,
        "virtual_endpoint": 2,
        "managed_host_endpoint": 1,
        "external_unmanaged_endpoint": 0,
        "ip_endpoint": 0,
    }.get(node.entity_type, 0)


def _transport_key(edge: DependencyEdge) -> tuple[str, int, str, str]:
    return (
        edge.protocol,
        edge.destination_port,
        edge.source_endpoint.canonical() if edge.source_endpoint else "",
        edge.target_endpoint.canonical() if edge.target_endpoint else "",
    )


def _unique_resolved_entity(
    nodes: Mapping[str, DependencyNode],
    edges: Iterable[DependencyEdge],
    *,
    side: str,
) -> str:
    ranked: dict[int, set[str]] = {}
    for edge in edges:
        entity_id = edge.source_entity if side == "source" else edge.target_entity
        rank = _entity_resolution_rank(nodes.get(entity_id))
        if rank <= 1:
            continue
        ranked.setdefault(rank, set()).add(entity_id)
    if not ranked:
        return ""
    best = ranked[max(ranked)]
    return next(iter(best)) if len(best) == 1 else ""


def _reconcile_edges(
    nodes: dict[str, DependencyNode],
    edges: Iterable[DependencyEdge],
    assertions: Iterable[IdentityAssertion],
) -> list[DependencyEdge]:
    endpoint_targets: dict[str, tuple[float, str]] = {}
    for assertion in assertions:
        if assertion.predicate not in {"listens_on", "maps_to"}:
            continue
        target = nodes.get(assertion.object)
        if target is None or target.entity_type not in {
            "process", "instance", "service", "managed_host_endpoint",
        }:
            continue
        previous = endpoint_targets.get(assertion.subject)
        if previous is None or assertion.confidence > previous[0]:
            endpoint_targets[assertion.subject] = (assertion.confidence, assertion.object)

    normalized: list[DependencyEdge] = []
    for edge in edges:
        source = edge.source_entity
        target = edge.target_entity
        source_node = nodes.get(source)
        target_node = nodes.get(target)
        source_subject = source_node.endpoint.entity_id() if source_node and source_node.endpoint else ""
        target_subject = target_node.endpoint.entity_id() if target_node and target_node.endpoint else ""
        source = endpoint_targets.get(source_subject, (0.0, source))[1]
        target = endpoint_targets.get(target_subject, (0.0, target))[1]
        if source == target or source not in nodes or target not in nodes:
            continue
        rebuilt = DependencyEdge.create(
            source_entity=source,
            target_entity=target,
            relation=edge.relation,
            protocol=edge.protocol,
            destination_port=edge.destination_port,
            window=edge.window,
            source_endpoint=edge.source_endpoint,
            target_endpoint=edge.target_endpoint,
            metrics=edge.metrics,
            identity_confidence=max(
                edge.identity_confidence,
                endpoint_targets.get(source_subject, (0.0, ""))[0],
                endpoint_targets.get(target_subject, (0.0, ""))[0],
            ),
            direction_confidence=edge.direction_confidence,
            observation_points=edge.observation_points,
            evidence_refs=edge.evidence_refs,
            event_refs=edge.event_refs,
        )
        normalized.append(rebuilt)

    # Client and server snapshots can arrive as separate Evidence objects.  A
    # server-only observation initially represents the caller as a host
    # endpoint, while another projection may know the exact client process.
    # Reconcile only an exact transport tuple and only when the better identity
    # is unique; conflicting process identities remain separate.
    by_transport: dict[tuple[str, int, str, str], list[DependencyEdge]] = {}
    for edge in normalized:
        by_transport.setdefault(_transport_key(edge), []).append(edge)

    merged: dict[tuple[str, str, str, str, int, str, str], DependencyEdge] = {}
    for transport_key, group in by_transport.items():
        resolved_source = _unique_resolved_entity(nodes, group, side="source")
        resolved_target = _unique_resolved_entity(nodes, group, side="target")
        for edge in group:
            source = resolved_source or edge.source_entity
            target = resolved_target or edge.target_entity
            if source == target or source not in nodes or target not in nodes:
                continue
            rebuilt = DependencyEdge.create(
                source_entity=source,
                target_entity=target,
                relation=edge.relation,
                protocol=edge.protocol,
                destination_port=edge.destination_port,
                window=edge.window,
                source_endpoint=edge.source_endpoint,
                target_endpoint=edge.target_endpoint,
                metrics=edge.metrics,
                identity_confidence=max(
                    edge.identity_confidence,
                    nodes[source].confidence,
                    nodes[target].confidence,
                ),
                direction_confidence=edge.direction_confidence,
                observation_points=edge.observation_points,
                evidence_refs=edge.evidence_refs,
                event_refs=edge.event_refs,
            )
            logical_key = (
                source,
                target,
                rebuilt.relation,
                rebuilt.protocol,
                rebuilt.destination_port,
                transport_key[2],
                transport_key[3],
            )
            previous = merged.get(logical_key)
            merged[logical_key] = _merge_edge(previous, rebuilt) if previous else rebuilt
    return list(merged.values())


def aggregate_dependency_graph(
    evidence_items: Iterable[Mapping[str, Any]],
    projections: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge active DEPENDENCY_GRAPH projections into one bounded Case view."""
    active = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_items
        if str(item.get("status") or "ACTIVE") != "EXCLUDED"
    }
    nodes: dict[str, DependencyNode] = {}
    edges: list[DependencyEdge] = []
    assertions: dict[str, IdentityAssertion] = {}
    evidence_refs: list[str] = []
    coverage_items: list[dict[str, Any]] = []
    limitations: list[str] = []
    discovery_runs: list[str] = []
    membership_snapshots: list[str] = []
    for projection in projections:
        evidence_id = str(projection.get("evidence_id") or "")
        projection_kind = str(projection.get("projection_kind") or "")
        if evidence_id not in active or projection_kind not in {
            "DEPENDENCY_GRAPH", "TOPOLOGY_GRAPH",
        }:
            continue
        content = projection.get("content") or {}
        graph_payload = content.get("graph") or {}
        if projection_kind == "TOPOLOGY_GRAPH":
            topology = content.get("topology") or {}
            graph_payload = {
                "schema_version": "dependency-graph.v1",
                "nodes": topology.get("nodes") or [],
                "edges": topology.get("edges") or [],
                "identity_assertions": topology.get("identity_assertions") or [],
            }
        try:
            graph = DependencyGraph.model_validate(graph_payload)
        except (TypeError, ValueError):
            continue
        evidence_refs.append(evidence_id)
        for node in graph.nodes:
            nodes[node.entity_id] = _merge_node(nodes[node.entity_id], node) if node.entity_id in nodes else node
        # Every edge in a Case view must cite a live canonical Evidence row.
        # Older projections may contain collector event IDs (or no ref at all),
        # so bind the current projection's Evidence ID and discard refs that do
        # not belong to the active Case.  Event lineage remains in
        # ``event_refs`` and is never promoted to ``evidence_refs``.
        for edge in graph.edges:
            canonical_refs = {
                str(ref) for ref in edge.evidence_refs
                if str(ref) in active
            }
            if evidence_id:
                canonical_refs.add(evidence_id)
            edges.append(edge.model_copy(update={
                "evidence_refs": sorted(canonical_refs),
                "event_refs": sorted(set(str(ref) for ref in edge.event_refs if ref)),
            }))
        for assertion in graph.identity_assertions:
            assertions[assertion.assertion_id] = assertion
        topology = content.get("topology") if isinstance(content.get("topology"), Mapping) else {}
        coverage = content.get("coverage") or topology.get("coverage")
        if isinstance(coverage, Mapping):
            coverage_items.append(dict(coverage))
        limitations.extend(
            str(item) for item in (
                content.get("limitations") or topology.get("limitations") or []
            ) if item
        )
        discovery_run_id = content.get("discovery_run_id") or topology.get("discovery_run_id")
        if discovery_run_id:
            discovery_runs.append(str(discovery_run_id))
        if content.get("membership_snapshot_id"):
            membership_snapshots.append(str(content["membership_snapshot_id"]))

    reconciled_edges = _reconcile_edges(nodes, edges, assertions.values())
    referenced = {
        entity
        for edge in reconciled_edges
        for entity in (edge.source_entity, edge.target_entity)
    }
    retained_nodes = [node for node in nodes.values() if node.entity_id in referenced]
    retained_assertions = [
        item for item in assertions.values()
        if item.subject in referenced or item.object in referenced
        or item.object in nodes
    ]
    graph = DependencyGraph(
        nodes=retained_nodes,
        edges=reconciled_edges,
        identity_assertions=retained_assertions,
    )
    graph, truncated = _bounded_graph(graph)
    external_count = sum(
        1 for node in graph.nodes if node.entity_type == "external_unmanaged_endpoint"
    )
    virtual_count = sum(1 for node in graph.nodes if node.entity_type == "virtual_endpoint")
    def _coverage_item_is_insufficient(item: Mapping[str, Any]) -> bool:
        status = str(item.get("status") or "").strip().lower()
        conclusion = str(item.get("conclusion") or "").strip().lower()
        if not status:
            # A bare affirmative conclusion from an older projection does not
            # establish that the underlying snapshot was complete.
            return True
        if status in {"partial", "unknown", "insufficient"}:
            return True
        if conclusion and conclusion != "dependency":
            return True
        # These counters describe a graph that contains an endpoint we cannot
        # safely expand.  Keep the edge visible, but do not report complete
        # coverage to the Agent.
        for key in (
            "managed_unresolved_count", "external_unmanaged_count",
            "virtual_endpoint_count", "unresolved_endpoint_count",
        ):
            try:
                if int(item.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                # A malformed counter is itself not evidence of complete
                # coverage; the explicit status/conclusion above remains the
                # authoritative fail-closed signal.
                continue
        return False

    # A graph without a coverage block is an unknown snapshot, not proof of a
    # complete dependency.  Keep the edges for inspection but require an
    # explicit affirmative coverage state before returning ``dependency``.
    insufficient = not graph.edges or not coverage_items or any(
        _coverage_item_is_insufficient(item) for item in coverage_items
    )
    return {
        "schema_version": "case-dependency-graph.v1",
        "graph": graph.canonical_dict(),
        "graph_digest": graph.digest(),
        "graph_semantics": "dependency_only_not_causal",
        "evidence_refs": sorted(set(evidence_refs)),
        "discovery_run_ids": sorted(set(discovery_runs)),
        "membership_snapshot_ids": sorted(set(membership_snapshots)),
        "coverage": {
            "projection_count": len(set(evidence_refs)),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "external_unmanaged_count": external_count,
            "virtual_endpoint_count": virtual_count,
            "unresolved_endpoint_count": sum(
                1 for node in graph.nodes
                if node.entity_type == "managed_host_endpoint"
            ),
            "conclusion": "insufficient_coverage" if insufficient else "dependency",
            "items": coverage_items,
        },
        "limitations": sorted(set(limitations + [
            "dependency_edges_are_observations_not_causal_claims",
        ])),
        "truncated": truncated,
    }


def case_dependency_graph_snapshot(
    repository: Any,
    case_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    evidence = repository.list_case_evidence(case_id, tenant_id)
    projections = repository.list_evidence_projections(case_id, tenant_id)
    return aggregate_dependency_graph(evidence, projections)
