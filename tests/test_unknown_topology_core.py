"""未知拓扑 snapshot → identity/dependency graph → bounded frontier 回放。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.app.diagnosis.dependency_graph import (
    DependencyEdge,
    DependencyGraph,
    DependencyNode,
    DiscoveryEvent,
    NetworkEndpoint,
    ObservationWindow,
    ProcessIncarnation,
    SocketObservation,
)
from server.app.diagnosis.discovery_frontier import (
    AgentNetworkInventory,
    DiscoveryFrontierEngine,
    EndpointResolver,
    FrontierBudget,
    build_dependency_graph,
    build_discovery_snapshot_graph,
)


NOW = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)


def _payload(
    agent_id: str,
    address: str,
    boot_id: str,
    *,
    processes: list[dict] | None = None,
    listeners: list[dict] | None = None,
    connections: list[dict] | None = None,
    nested_agent_only: bool = False,
    coverage: dict | None = None,
) -> dict:
    payload = {
        "schema_version": "network_discovery.v1",
        "event_schema_version": "network-discovery-event.v1",
        "observed_at": NOW.isoformat(),
        "agent": {
            "agent_id": agent_id,
            "hostname": f"host-{agent_id}",
            "ip_addr": address,
            "boot_id": boot_id,
        },
        "processes": processes or [],
        "listeners": listeners or [],
        "connections": connections or [],
        "events": [],
        "coverage": coverage or {"status": "complete", "reasons": []},
        "limitations": ["tcp_communication_does_not_prove_root_cause"],
    }
    if not nested_agent_only:
        payload.update({
            "agent_id": agent_id,
            "boot_id": boot_id,
            "hostname": f"host-{agent_id}",
            "host_addresses": [address],
        })
    return payload


def _process(identity: str, pid: int, start: int, comm: str) -> dict:
    return {
        "process_identity": identity,
        "pid": pid,
        "start_time_ticks": start,
        "comm": comm,
        "netns": 4026532000 + pid,
    }


def _two_agent_payloads(*, include_inbound: bool = False) -> list[dict]:
    client_connection = {
        "event_id": "ev-client",
        "observed_at": NOW.isoformat(),
        "process_identity": "proc-client",
        "local": "10.0.0.1:41000",
        "remote": "10.0.0.2:8080",
        "protocol": "tcp",
        "state": "ESTABLISHED",
        "direction": "outbound",
        "direction_confidence": 0.7,
    }
    server_connections = []
    if include_inbound:
        server_connections.append({
            "event_id": "ev-server",
            "observed_at": NOW.isoformat(),
            "process_identity": "proc-server",
            "local": "10.0.0.2:8080",
            "remote": "10.0.0.1:41000",
            "protocol": "tcp",
            "state": "ESTABLISHED",
            "direction": "inbound",
            "direction_confidence": 0.9,
        })
    return [
        _payload(
            "agent-a", "10.0.0.1", "boot-a",
            processes=[_process("proc-client", 101, 1001, "frontend")],
            connections=[client_connection],
            nested_agent_only=True,
        ),
        _payload(
            "agent-b", "10.0.0.2", "boot-b",
            processes=[_process("proc-server", 202, 2002, "checkout")],
            listeners=[{
                "event_id": "ev-listen",
                "observed_at": NOW.isoformat(),
                "process_identity": "proc-server",
                "local": "0.0.0.0:8080",
                "local_port": 8080,
                "protocol": "tcp",
                "state": "LISTEN",
            }],
            connections=server_connections,
        ),
    ]


def test_contract_normalizes_ipv6_and_preserves_lsof_source():
    endpoint = NetworkEndpoint.parse("tcp://[2001:db8::1]:443")
    assert endpoint.canonical() == "tcp://[2001:db8::1]:443"
    process = ProcessIncarnation(
        agent_id="agent-a", boot_id="boot-a", pid=1, process_start_time=10,
    )
    event = DiscoveryEvent(
        event_id="event-a", agent_id="agent-a", boot_id="boot-a",
        observed_at=NOW, event_type="tcp_snapshot", process=process,
        socket=SocketObservation(
            local=NetworkEndpoint.parse("10.0.0.1:40000"),
            remote=endpoint,
        ),
        source="lsof",
    )
    assert event.source == "lsof"


def test_actual_collector_nested_agent_and_process_identity_are_normalized():
    inventory = AgentNetworkInventory.from_payload(_two_agent_payloads()[0])
    assert inventory.agent_id == "agent-a"
    assert inventory.boot_id == "boot-a"
    assert inventory.host_addresses == ["10.0.0.1"]
    assert inventory.connections[0].process is not None
    assert inventory.connections[0].process.stable_ref() == "process:agent-a:boot-a:101:1001"
    assert inventory.connections[0].observation_point == "client"
    assert inventory.connections[0].result == "success"


def test_snapshot_build_resolves_registered_wildcard_listener_and_adds_assertions():
    graph = build_dependency_graph(_two_agent_payloads())
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.source_entity == "process:agent-a:boot-a:101:1001"
    assert edge.target_entity == "process:agent-b:boot-b:202:2002"
    assert edge.source_endpoint == NetworkEndpoint.parse("10.0.0.1:41000")
    assert edge.target_endpoint == NetworkEndpoint.parse("10.0.0.2:8080")
    assert edge.relation == "calls"
    target = graph.node_map()[edge.target_entity]
    assert "tcp://10.0.0.2:8080" in target.attributes["listening_endpoints"]
    assert any(
        assertion.subject == "ip_endpoint:tcp://10.0.0.2:8080"
        and assertion.object == edge.target_entity
        and assertion.predicate == "listens_on"
        for assertion in graph.identity_assertions
    )


def test_cross_agent_late_listener_snapshot_resolves_earlier_seed_connection():
    seed = _payload(
        "agent-a", "10.0.0.1", "boot-a",
        processes=[_process("proc-a", 101, 1001, "client")],
        connections=[{
            "event_id": "ev-early",
            "observed_at": "2026-08-21T01:00:00Z",
            "process_identity": "proc-a",
            "local": "10.0.0.1:41000",
            "remote": "10.0.0.2:8080",
            "state": "ESTABLISHED",
            "direction": "outbound",
        }],
    )
    remote = _payload(
        "agent-b", "10.0.0.2", "boot-b",
        processes=[_process("proc-b", 202, 2002, "server")],
        listeners=[{
            "process_identity": "proc-b",
            "observed_at": "2026-08-21T01:00:10Z",
            "local": "0.0.0.0:8080",
            "protocol": "tcp",
            "state": "LISTEN",
        }],
    )
    graph = build_dependency_graph([seed, remote])
    assert graph.edges[0].target_entity == "process:agent-b:boot-b:202:2002"


def test_wildcard_listener_does_not_capture_unrelated_public_endpoint():
    payloads = _two_agent_payloads()
    payloads[0]["connections"][0]["remote"] = "8.8.8.8:8080"
    graph = build_dependency_graph(payloads)
    target = graph.node_map()[graph.edges[0].target_entity]
    assert target.entity_type == "external_unmanaged_endpoint"
    assert target.entity_id == "external_unmanaged_endpoint:tcp://8.8.8.8:8080"


def test_inbound_only_edge_points_from_remote_caller_to_local_server():
    server = _payload(
        "agent-b", "10.0.0.2", "boot-b",
        processes=[_process("proc-server", 202, 2002, "checkout")],
        listeners=[{
            "process_identity": "proc-server", "local": "0.0.0.0:8080",
            "protocol": "tcp", "state": "LISTEN",
        }],
        connections=[{
            "event_id": "ev-inbound", "process_identity": "proc-server",
            "local": "10.0.0.2:8080", "remote": "10.0.0.1:41000",
            "state": "ESTABLISHED", "direction": "inbound",
            "direction_confidence": 0.9,
        }],
    )
    caller_inventory = _payload("agent-a", "10.0.0.1", "boot-a")
    graph = build_dependency_graph([caller_inventory, server])
    edge = graph.edges[0]
    assert edge.source_entity.startswith("agent:agent-a:endpoint:tcp://10.0.0.1:41000")
    assert edge.target_entity == "process:agent-b:boot-b:202:2002"
    assert edge.destination_port == 8080
    assert edge.source_endpoint == NetworkEndpoint.parse("10.0.0.1:41000")
    assert edge.target_endpoint == NetworkEndpoint.parse("10.0.0.2:8080")


def test_dual_client_server_observation_is_one_directed_edge_not_double_counted():
    graph = build_dependency_graph(_two_agent_payloads(include_inbound=True))
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.source_entity == "process:agent-a:boot-a:101:1001"
    assert edge.target_entity == "process:agent-b:boot-b:202:2002"
    assert edge.metrics.connections == 1
    assert edge.observation_points == ["client", "server"]


def test_later_listener_snapshot_keeps_earlier_connection_resolved_to_process():
    first = _payload(
        "agent-local", "127.0.0.1", "boot-local",
        processes=[
            _process("proc-client", 101, 1001, "client"),
            _process("proc-server", 202, 2002, "server"),
        ],
        listeners=[{
            "process_identity": "proc-server",
            "observed_at": NOW.isoformat(),
            "local": "127.0.0.1:19090",
            "protocol": "tcp",
            "state": "LISTEN",
        }],
        connections=[{
            "event_id": "ev-local-client",
            "observed_at": NOW.isoformat(),
            "process_identity": "proc-client",
            "local": "127.0.0.1:41000",
            "remote": "127.0.0.1:19090",
            "state": "ESTABLISHED",
            "direction": "outbound",
        }],
    )
    later = _payload(
        "agent-local", "127.0.0.1", "boot-local",
        processes=[_process("proc-server", 202, 2002, "server")],
        listeners=[{
            "process_identity": "proc-server",
            "observed_at": "2026-08-21T01:00:10Z",
            "local": "127.0.0.1:19090",
            "protocol": "tcp",
            "state": "LISTEN",
        }],
    )
    later["observed_at"] = "2026-08-21T01:00:10Z"

    graph = build_dependency_graph([first, later])

    assert len(graph.edges) == 1
    assert graph.edges[0].source_entity == "process:agent-local:boot-local:101:1001"
    assert graph.edges[0].target_entity == "process:agent-local:boot-local:202:2002"
    assert not any(node.entity_type == "managed_host_endpoint" for node in graph.nodes)


def test_listener_snapshot_from_remote_agent_may_arrive_after_connection():
    """Cross-agent fanout is serial: the listener often is observed later."""
    payloads = _two_agent_payloads()
    payloads[1]["observed_at"] = "2026-08-21T01:00:10Z"
    payloads[1]["listeners"][0]["observed_at"] = "2026-08-21T01:00:10Z"

    graph = build_dependency_graph(payloads)

    assert len(graph.edges) == 1
    assert graph.edges[0].target_entity == "process:agent-b:boot-b:202:2002"
    assert not any(
        node.entity_type == "managed_host_endpoint" for node in graph.nodes
    )


def test_missing_process_start_time_is_partial_and_never_falls_back_to_one():
    payload = _payload(
        "agent-a", "10.0.0.1", "boot-a",
        processes=[{"pid": 101, "comm": "checkout"}],
        connections=[{
            "event_id": "ev-no-start",
            "pid": 101,
            "local": "10.0.0.1:41000",
            "remote": "10.0.0.2:8080",
            "state": "ESTABLISHED",
            "direction": "outbound",
        }],
    )

    inventory = AgentNetworkInventory.from_payload(payload)
    assert inventory.processes == []
    assert inventory.coverage_status == "partial"
    assert "process_start_time_missing_or_invalid" in inventory.coverage_reasons

    graph = build_dependency_graph([payload])
    assert len(graph.edges) == 1
    assert not any(
        node.entity_id.endswith(":101:1") for node in graph.nodes
    )
    assert graph.edges[0].source_entity.startswith(
        "managed_host_endpoint:agent-a:"
    )


def test_event_and_case_evidence_refs_use_separate_namespaces():
    payloads = _two_agent_payloads()
    payloads[0]["connections"][0]["evidence_refs"] = ["ev-source"]

    graph = build_dependency_graph(payloads, evidence_ref="ev-artifact")
    edge = graph.edges[0]

    assert edge.event_refs == ["ev-client"]
    assert edge.evidence_refs == ["ev-artifact", "ev-source"]
    assert "ev-client" not in edge.evidence_refs


def test_virtual_endpoint_is_not_falsely_resolved_to_one_backend():
    payloads = _two_agent_payloads()
    graph = build_dependency_graph(payloads, virtual_endpoints=[{
        "endpoint": "10.0.0.2:8080",
        "service_id": "checkout-vip",
        "backends": [
            "process:agent-b:boot-b:202:2002",
            "process:agent-c:boot-c:303:3003",
        ],
    }])
    target = graph.node_map()[graph.edges[0].target_entity]
    assert target.entity_type == "virtual_endpoint"
    assert len(target.attributes["backend_candidates"]) == 2


def _frontier_graph() -> DependencyGraph:
    nodes = [
        DependencyNode(entity_id="seed", entity_type="process", agent_id="agent-a"),
        DependencyNode(entity_id="peer-b", entity_type="process", agent_id="agent-b"),
        DependencyNode(entity_id="peer-c", entity_type="process", agent_id="agent-c"),
    ]
    edges = [
        DependencyEdge.create(
            source_entity="seed", target_entity="peer-b", relation="calls",
            protocol="tcp", destination_port=80,
            window=ObservationWindow(start=NOW, end=NOW),
        ),
        DependencyEdge.create(
            source_entity="peer-b", target_entity="peer-c", relation="calls",
            protocol="tcp", destination_port=81,
            window=ObservationWindow(start=NOW, end=NOW),
        ),
    ]
    return DependencyGraph(nodes=nodes, edges=edges)


@pytest.mark.parametrize(
    ("budget", "expected_reason"),
    [
        (FrontierBudget(max_processes=1, max_hosts=10), "MAX_PROCESSES"),
        (FrontierBudget(max_processes=10, max_hosts=1), "MAX_HOSTS"),
    ],
)
def test_frontier_enforces_process_and_host_budgets(budget, expected_reason):
    run = DiscoveryFrontierEngine(EndpointResolver([])).run(
        run_id="run-budget", seed_entity="seed", graph=_frontier_graph(), budget=budget,
    )
    assert expected_reason in run.stopped_reasons
    assert run.coverage["conclusion"] == "insufficient_coverage"


def test_frontier_hop_and_edge_budgets_are_deterministic():
    hop_run = DiscoveryFrontierEngine(EndpointResolver([])).run(
        run_id="run-hop", seed_entity="seed", graph=_frontier_graph(),
        budget=FrontierBudget(max_hops=1),
    )
    assert hop_run.visited_entities == ["peer-b", "seed"]
    assert hop_run.targets[0].entity_id == "peer-b"
    edge_run = DiscoveryFrontierEngine(EndpointResolver([])).run(
        run_id="run-edge", seed_entity="seed", graph=_frontier_graph(),
        budget=FrontierBudget(max_edges=1),
    )
    assert edge_run.stopped_reasons == ["MAX_EDGES"]


def test_snapshot_build_result_exposes_lineage_digest_coverage_and_targets():
    result = build_discovery_snapshot_graph(
        _two_agent_payloads(),
        seed_ref={"agent_id": "agent-a", "pid": 101},
        membership_snapshot_id="membership-1",
        discovery_run_id="discovery-1",
    )
    assert result.membership_snapshot_id == "membership-1"
    assert result.discovery_run_id == "discovery-1"
    assert result.seed_ref == "process:agent-a:boot-a:101:1001"
    assert result.graph_digest == result.graph.digest()
    assert result.coverage["conclusion"] == "dependency"
    assert [target.agent_id for target in result.managed_frontier_targets] == ["agent-b"]
    assert result.managed_frontier_targets[0].endpoint == NetworkEndpoint.parse("10.0.0.2:8080")
    # Intrinsic L4 limitation remains visible but does not turn a complete,
    # resolved dependency snapshot into a fake causal conclusion.
    assert "tcp_communication_does_not_prove_root_cause" in result.limitations


def test_unknown_snapshot_coverage_forces_insufficient_conclusion():
    payloads = _two_agent_payloads()
    payloads[0]["coverage"] = {"status": "unknown", "reasons": []}

    result = build_discovery_snapshot_graph(
        payloads,
        seed_ref={"agent_id": "agent-a", "pid": 101},
        discovery_run_id="unknown-coverage",
    )

    assert result.coverage["conclusion"] == "insufficient_coverage"
    assert result.coverage["unknown_agent_snapshots"] == 1
    assert "agent_snapshot_coverage_unknown" in result.limitations


def test_graph_digest_is_identical_when_snapshot_order_changes():
    payloads = _two_agent_payloads(include_inbound=True)
    first = build_dependency_graph(payloads)
    second = build_dependency_graph(list(reversed(payloads)))
    assert first.digest() == second.digest()
