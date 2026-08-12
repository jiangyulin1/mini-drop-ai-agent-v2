"""统一资源身份图与因果图测试。"""

from __future__ import annotations

from datetime import datetime, timezone

from server.app.diagnosis.causal_graph import build_causal_graph, contract_coverage
from server.app.diagnosis.resource_identity import (
    ResourceIdentity,
    ResourceIdentityGraph,
    build_identity_graph,
    merge_identity_graphs,
)


def _utcnow():
    return datetime.now(timezone.utc)


def test_build_identity_graph_from_scope():
    graph = build_identity_graph(
        service_id="checkoutservice",
        instances=[
            {"instance_id": "checkout-1", "service_id": "checkoutservice",
             "host_id": "worker2", "agent_id": "a2", "pid": 1600,
             "container_id": "c0d9"},
            {"instance_id": "payment-1", "service_id": "paymentservice",
             "host_id": "worker2", "agent_id": "a2", "pid": 1700},
        ],
        dependencies=[
            {"source_service": "checkoutservice", "target_service": "paymentservice",
             "relation": "CALLS"},
        ],
    )
    ids = {node.stable_id for node in graph.nodes()}
    assert {"checkoutservice", "checkout-1", "worker2", "c0d9", "paymentservice"}.issubset(ids)
    relations = {edge.relation for edge in graph.edges()}
    assert "calls" in relations
    assert "runs_on" in relations
    assert graph.downstream("checkoutservice") == ["paymentservice"]


def test_higher_priority_source_overrides():
    graph = ResourceIdentityGraph()
    graph.register(ResourceIdentity(
        stable_id="svc-a", node_type="service", source="model",
        discovered_at=_utcnow(), confidence="low",
    ))
    graph.register(ResourceIdentity(
        stable_id="svc-a", node_type="service", source="orchestrator",
        discovered_at=_utcnow(), confidence="high",
    ))
    assert graph.get("svc-a").source == "orchestrator"


def test_merge_identity_graphs_prefers_primary():
    a = ResourceIdentityGraph()
    a.register(ResourceIdentity(stable_id="x", node_type="service", source="orchestrator", discovered_at=_utcnow(), confidence="high"))
    b = ResourceIdentityGraph()
    b.register(ResourceIdentity(stable_id="x", node_type="service", source="model", discovered_at=_utcnow(), confidence="low"))
    b.register(ResourceIdentity(stable_id="y", node_type="host", source="trace", discovered_at=_utcnow(), confidence="medium"))
    merged = merge_identity_graphs(a, b)
    assert merged.get("x").source == "orchestrator"
    assert merged.get("y") is not None


def test_unknown_relation_rejected():
    graph = ResourceIdentityGraph()
    try:
        graph.add_edge("a", "b", "bogus_relation")
        assert False, "应拒绝未注册关系"
    except ValueError:
        pass


def test_contract_coverage_for_runtime_lock():
    coverage, missing = contract_coverage("runtime_lock_contention", {
        "runtime_type": "go",
        "blocked_thread_ratio_max": 0.96,
        "lock_waiter_count_max": 27,
    })
    assert coverage == 1.0
    assert missing == []


def test_contract_coverage_partial():
    coverage, missing = contract_coverage("downstream_dependency", {
        "connection_refused_count": 5,
    })
    assert 0 < coverage < 1.0
    assert "endpoint.reachable" in missing


def test_causal_graph_builds_propagation_edges():
    assessment = {
        "classification": "downstream_dependency",
        "root_entity": "paymentservice",
        "root_location": {"type": "downstream", "target_ref": "checkout-1"},
        "contributing_causes": [],
        "ruled_out": [{"hypothesis": "self_code_regression"}],
        "compared_targets": [{"service_id": "checkoutservice"}],
    }
    graph = build_causal_graph(assessment, {"connection_refused_count": 10})
    assert graph["primary_cause"]["entity"] == "paymentservice"
    assert graph["primary_cause"]["mechanism"] == "downstream_dependency"
    propagation = [e for e in graph["propagation_edges"] if e["relation"] == "propagates_to"]
    assert any(e["source"] == "paymentservice" and e["target"] == "checkoutservice" for e in propagation)
    ruled_out = [e for e in graph["ruled_out_causes"] if e["relation"] == "rules_out"]
    assert any(e["target"] == "self_code_regression" for e in ruled_out)
