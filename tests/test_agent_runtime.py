"""Conversation Agent runtime: intent, MCP-safe tools and capacity prediction."""

from server.app.diagnosis.agent_runtime import (
    AgentTurnIntent,
    DeploymentRequirements,
    assess_deployment_capacity,
    build_case_evidence_chain,
    build_observability_tool_plan,
    classify_turn,
    parse_deployment_requirements,
)
from server.app.diagnosis.authorization import OperationClass, SourceDefinition


def test_turn_intent_is_deterministic_and_explicit_intent_wins():
    assert classify_turn("为什么判断是数据库？") == AgentTurnIntent.EXPLAIN
    assert classify_turn("评估这个服务现在能否部署") == AgentTurnIntent.DEPLOYMENT_ASSESSMENT
    assert classify_turn("其实不是 worker-1", AgentTurnIntent.STATUS) == AgentTurnIntent.STATUS


def test_parse_chinese_deployment_requirements():
    result = parse_deployment_requirements("部署 3 个副本，每个 CPU 2 核、内存 4GB、磁盘 20GB")
    assert result is not None
    assert result.replicas == 3
    assert result.cpu_cores_per_replica == 2
    assert result.memory_mb_per_replica == 4096
    assert result.disk_mb_per_replica == 20 * 1024


def test_capacity_assessment_requires_allocatable_inventory_not_utilization():
    requirements = DeploymentRequirements(
        replicas=2,
        cpu_cores_per_replica=2,
        memory_mb_per_replica=4096,
    )
    result = assess_deployment_capacity(
        requirements,
        target_scope={},
        tool_evidence=[{
            "content_projection": {"nodes": [{"node_id": "n1", "cpu_percent": 2}]},
        }],
    )
    assert result.verdict == "insufficient_data"
    assert "不能用瞬时利用率替代容量" in result.summary


def test_capacity_assessment_applies_margin_and_replica_count():
    requirements = DeploymentRequirements(
        replicas=2,
        cpu_cores_per_replica=2,
        memory_mb_per_replica=4096,
        disk_mb_per_replica=10_000,
        safety_margin_ratio=0.25,
    )
    result = assess_deployment_capacity(requirements, target_scope={
        "deployment_inventory": [
            {"node_id": "n1", "allocatable_cpu_cores": 3, "allocatable_memory_mb": 6000, "allocatable_disk_mb": 20_000},
            {"node_id": "n2", "allocatable_cpu_cores": 3, "allocatable_memory_mb": 6000, "allocatable_disk_mb": 20_000},
            {"node_id": "n3", "allocatable_cpu_cores": 1, "allocatable_memory_mb": 6000, "allocatable_disk_mb": 20_000},
        ],
    })
    assert result.verdict == "ready"
    assert result.eligible_nodes == ["n1", "n2"]
    assert result.rejected_nodes == [{"node_id": "n3", "reasons": ["cpu_insufficient"]}]


def test_deployment_plan_prefers_registered_mcp_capacity_source():
    source = SourceDefinition(
        source_id="cluster-capacity-mcp",
        name="Capacity MCP",
        source_type="mcp",
        operation_class=OperationClass.READ,
        operations=["capacity.read"],
        resource_dimensions=["cluster_id", "environment"],
        data_classes=["capacity"],
    )
    plan = build_observability_tool_plan(
        {
            "environment": "production",
            "target_scope": {
                "cluster_id": "prod-a",
                "service_id": "checkout",
                "instances": [{"agent_id": "agent-1"}],
            },
        },
        intent=AgentTurnIntent.DEPLOYMENT_ASSESSMENT,
        max_tool_calls=2,
        source_definitions=[source],
    )
    assert plan[0].source_id == "cluster-capacity-mcp"
    assert plan[0].operation == "capacity.read"
    assert plan[0].resource == {"cluster_id": "prod-a", "environment": "production"}
    assert len(plan) == 1


def test_evidence_chain_contains_only_cited_evidence_with_roles():
    chain = build_case_evidence_chain(
        {"hypotheses": [{
            "hypothesis_id": "h1",
            "supporting_evidence_refs": ["ev-1"],
            "contradicting_evidence_refs": ["ev-2"],
        }]},
        [
            {"evidence_id": "ev-1", "source_type": "metric", "integrity_hash": "a"},
            {"evidence_id": "ev-2", "source_type": "trace", "integrity_hash": "b"},
            {"evidence_id": "ev-unused", "source_type": "log"},
        ],
    )
    assert [item["evidence_id"] for item in chain] == ["ev-1", "ev-2"]
    assert chain[0]["roles"] == ["support:h1"]
    assert chain[1]["roles"] == ["contradiction:h1"]
