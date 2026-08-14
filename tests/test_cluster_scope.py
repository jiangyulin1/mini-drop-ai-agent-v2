"""E3.5: 集群范围与采集扇出 —— 确定性 Planner 对 3+ 节点环境的扇出闭环。

覆盖退出条件：逻辑 Step 扇出、部分失败、取消、恢复（幂等）、覆盖率判定与
coverage-aware Evidence 聚合；离线成员进入覆盖率分母与排除原因，迟到结果隔离。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis.cluster_scope import (
    CapacityBudget,
    EnvironmentProfile,
    MemberEntry,
    MembershipSnapshot,
    TargetResolver,
    classify_coverage,
)
from server.app.main import app, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _profile(cluster: str = "prod-a", max_instances: int = 128) -> EnvironmentProfile:
    return EnvironmentProfile(
        environment_id="env-prod",
        environment_type="production",
        platform="swarm",
        region="cn-north",
        cluster=cluster,
        allowed_data_sources=["sys_metrics", "log_scan"],
        default_risk_policy="READ_LOW",
        clock_quality="good",
        capacity=CapacityBudget(
            max_instances=max_instances,
            max_fault_domains=8,
            max_parallel_tasks=16,
            per_fault_domain_parallelism=4,
        ),
    )


def _member(
    agent_id: str, *, online: bool = True, fault_domain: str = "zone-a",
    version: str = "0.3.0", reason: str = "",
) -> MemberEntry:
    return MemberEntry(
        agent_id=agent_id,
        hostname=agent_id,
        ip_addr=f"192.168.10.{agent_id[-1]}",
        instance_id=f"inst-{agent_id}",
        service_id="checkout",
        fault_domain=fault_domain,
        version=version,
        capability_version="1",
        online=online,
        exclusion_reason=reason,
        agent_version=version,
        pid=42,
        process_start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _three_node_snapshot() -> MembershipSnapshot:
    return MembershipSnapshot(
        snapshot_id="snap-test",
        environment_id="env-prod",
        cluster_id="prod-a",
        topology_version="topo-v1",
        scope_revision=1,
        members=[
            _member("agent-a", fault_domain="zone-a"),
            _member("agent-b", fault_domain="zone-b"),
            _member("agent-c", fault_domain="zone-a", version="0.2.0"),
        ],
    )


# ── 纯单元：选择策略与覆盖率判定 ───────────────────────────────────────


def test_representative_strategy_stratifies_by_fault_domain():
    snapshot = _three_node_snapshot()
    resolution = TargetResolver().resolve_collection_targets(
        snapshot, "REPRESENTATIVE", profile=_profile(),
    )
    agents = {t.member.agent_id for t in resolution.targets}
    assert "agent-a" in agents and "agent-b" in agents
    # zone-a 有 agent-a 与 agent-c：REPRESENTATIVE 每域至少取一个代表
    assert len(resolution.targets) == 3
    assert resolution.excluded == []


def test_outliers_strategy_ranks_by_metric():
    snapshot = _three_node_snapshot()
    resolution = TargetResolver().resolve_collection_targets(
        snapshot, "OUTLIERS", profile=_profile(), max_targets=2,
        metric_scores={"agent-a": 1.0, "agent-b": 90.0, "agent-c": 5.0},
    )
    agents = [t.member.agent_id for t in resolution.targets]
    assert agents == ["agent-b", "agent-c"]


def test_all_in_scope_respects_budget():
    snapshot = _three_node_snapshot()
    profile = _profile(max_instances=2)
    resolution = TargetResolver().resolve_collection_targets(
        snapshot, "ALL_IN_SCOPE", profile=profile,
    )
    assert len(resolution.targets) == 2


def test_explicit_target_ref_outside_snapshot_is_rejected():
    snapshot = _three_node_snapshot()
    resolution = TargetResolver().resolve_collection_targets(
        snapshot, "REPRESENTATIVE", profile=_profile(),
        target_refs=["agent-a", "not-in-snapshot"],
    )
    assert [t.member.agent_id for t in resolution.targets] == ["agent-a"]
    assert resolution.rejected and resolution.rejected[0]["ref"] == "not-in-snapshot"


def test_canary_and_control_strategy_pairs_groups():
    snapshot = _three_node_snapshot()
    resolution = TargetResolver().resolve_collection_targets(
        snapshot, "CANARY_AND_CONTROL", profile=_profile(),
        canary_labels={"inst-agent-a"}, control_labels={"inst-agent-b"},
    )
    agents = {t.member.agent_id for t in resolution.targets}
    assert agents == {"agent-a", "agent-b"}


def test_offline_member_enters_coverage_denominator():
    snapshot = MembershipSnapshot(
        snapshot_id="snap-offline",
        environment_id="env-prod",
        cluster_id="prod-a",
        members=[
            _member("agent-a", fault_domain="zone-a"),
            _member("agent-b", fault_domain="zone-b", online=False, reason="OFFLINE"),
        ],
    )
    resolution = TargetResolver().resolve_collection_targets(
        snapshot, "ALL_IN_SCOPE", profile=_profile(),
    )
    assert [t.member.agent_id for t in resolution.targets] == ["agent-a"]
    assert resolution.excluded and resolution.excluded[0]["agent_id"] == "agent-b"
    assert resolution.excluded[0]["reason"] == "OFFLINE"
    # 离线成员仍是覆盖率分母：2 个成员只有 1 个成功 → 50% 覆盖率
    report = classify_coverage(
        snapshot=snapshot,
        succeeded_members=["agent-a"],
        failed_members=[],
        time_aligned=True,
    )
    assert report.coverage == pytest.approx(0.5)
    assert report.conclusion == "insufficient_coverage"


def test_coverage_classification_distinguishes_levels():
    snapshot = _three_node_snapshot()
    full = classify_coverage(
        snapshot=snapshot, succeeded_members=["agent-a", "agent-b", "agent-c"],
        failed_members=[], time_aligned=True,
    )
    assert full.conclusion == "cluster-wide"
    partial = classify_coverage(
        snapshot=snapshot, succeeded_members=["agent-a", "agent-b"],
        failed_members=["agent-c"], time_aligned=True,
    )
    assert partial.coverage == pytest.approx(2 / 3)
    assert partial.conclusion == "fault-domain"
    misaligned = classify_coverage(
        snapshot=snapshot, succeeded_members=["agent-a", "agent-b", "agent-c"],
        failed_members=[], time_aligned=False,
    )
    assert misaligned.conclusion == "insufficient_coverage"


# ── API 集成：三节点扇出闭环 ───────────────────────────────────────────


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "cluster-fanout-case",
        "problem_description": "检查 prod-a/orders 是否整个集群 CPU 问题",
        "recovery_goal": "判定集群范围还是局部问题",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"cluster_id": "prod-a", "service_id": "checkout"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _register_agents() -> None:
    repo.register_agent(
        "agent-a", "node-a", "192.168.10.11", version="0.3.0",
        capabilities=["sys_metrics", "service:checkout", "fault_domain:zone-a"],
    )
    repo.register_agent(
        "agent-b", "node-b", "192.168.10.12", version="0.3.0",
        capabilities=["sys_metrics", "service:checkout", "fault_domain:zone-b"],
    )
    repo.register_agent(
        "agent-c", "node-c", "192.168.10.13", version="0.2.0",
        capabilities=["sys_metrics", "service:checkout", "fault_domain:zone-a"],
    )


def _cluster_plan(case: dict) -> dict:
    return {
        "goal": "验证 prod-a 集群 CPU 是否局部异常",
        "expected_case_row_version": case["row_version"],
        "expected_scope_revision": case["scope_revision"],
        "expected_plan_revision": 0,
        "steps": [{
            "kind": "COLLECTION",
            "collector_id": "sys_metrics",
            "target_refs": ["cluster:prod-a"],
            "purpose": "按故障域分层采样验证集群 CPU",
            "hypothesis_refs": ["hyp_cluster_cpu"],
            "priority": 80,
            "risk": "READ_LOW",
            "status": "QUEUED",
        }],
    }


def test_fanout_three_node_dispatch(client: TestClient):
    _register_agents()
    case = _create_case(client)
    plan = client.put(f"/api/v1/cases/{case['case_id']}/plans", json=_cluster_plan(case))
    assert plan.status_code == 200, plan.text
    step_id = plan.json()["data"]["steps"][0]["step_id"]

    resp = client.post(f"/api/v1/cases/{case['case_id']}/fanout", json={
        "step_id": step_id,
        "strategy": "REPRESENTATIVE",
        "environment_id": "env-prod",
        "cluster_id": "prod-a",
        "profile": _profile().model_dump(mode="json"),
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    run = data["run"]
    assert len(run["task_ids"]) == 3
    assert run["status"] == "RUNNING"
    # 三个成员各有 Task，且 agent 在快照内
    assert set(run["member_task_map"].keys()) == {"agent-a", "agent-b", "agent-c"}
    # 快照固化：可读取
    snapshot = client.get(f"/api/v1/cases/{case['case_id']}/fanout").json()["data"]["items"]
    assert snapshot and snapshot[0]["snapshot_id"] == run["snapshot_id"]


def test_fanout_partial_failure_aggregate(client: TestClient):
    _register_agents()
    case = _create_case(client)
    plan = client.put(f"/api/v1/cases/{case['case_id']}/plans", json=_cluster_plan(case)).json()["data"]
    step_id = plan["steps"][0]["step_id"]
    run = client.post(f"/api/v1/cases/{case['case_id']}/fanout", json={
        "step_id": step_id, "strategy": "ALL_IN_SCOPE",
        "environment_id": "env-prod", "cluster_id": "prod-a",
        "profile": _profile().model_dump(mode="json"),
    }).json()["data"]["run"]

    task_ids = run["task_ids"]
    # 两个成功，一个失败 → 覆盖率 2/3，结论 fault-domain
    for task_id in task_ids[:2]:
        outcome = client.post(
            f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/task-outcome",
            json={"task_id": task_id, "status": "DONE", "scope_revision": run["scope_revision"]},
        )
        assert outcome.status_code == 200, outcome.text
    client.post(
        f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/task-outcome",
        json={"task_id": task_ids[2], "status": "FAILED", "scope_revision": run["scope_revision"]},
    )
    agg = client.post(
        f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/aggregate",
        json={"time_aligned": True},
    )
    assert agg.status_code == 200, agg.text
    data = agg.json()["data"]
    assert data["coverage"]["coverage"] == pytest.approx(2 / 3)
    assert data["coverage"]["conclusion"] == "fault-domain"
    assert data["run"]["quorum_met"] is True
    assert data["run"]["status"] == "COMPLETED"
    assert data["run"]["failed_count"] == 1


def test_fanout_cancel_propagation_and_resume(client: TestClient):
    _register_agents()
    case = _create_case(client)
    plan = client.put(f"/api/v1/cases/{case['case_id']}/plans", json=_cluster_plan(case)).json()["data"]
    step_id = plan["steps"][0]["step_id"]
    run = client.post(f"/api/v1/cases/{case['case_id']}/fanout", json={
        "step_id": step_id, "strategy": "ALL_IN_SCOPE",
        "environment_id": "env-prod", "cluster_id": "prod-a",
        "profile": _profile().model_dump(mode="json"),
    }).json()["data"]["run"]
    task_ids = run["task_ids"]
    # 一个先 DONE，其余仍 PENDING
    client.post(
        f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/task-outcome",
        json={"task_id": task_ids[0], "status": "DONE", "scope_revision": run["scope_revision"]},
    )
    cancelled = client.post(
        f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/cancel", json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    statuses = cancelled.json()["data"]["task_statuses"]
    assert statuses[task_ids[0]] == "DONE"
    # 取消传播：其余 PENDING Task → CANCELLED
    assert all(statuses[t] == "CANCELLED" for t in task_ids[1:])

    resumed = client.post(
        f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/resume", json={},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["data"]["status"] == "RUNNING"
    assert all(
        resumed.json()["data"]["task_statuses"][t] in {"PENDING", "DONE"}
        for t in task_ids
    )


def test_fanout_late_result_is_isolated(client: TestClient):
    _register_agents()
    case = _create_case(client)
    plan = client.put(f"/api/v1/cases/{case['case_id']}/plans", json=_cluster_plan(case)).json()["data"]
    step_id = plan["steps"][0]["step_id"]
    run = client.post(f"/api/v1/cases/{case['case_id']}/fanout", json={
        "step_id": step_id, "strategy": "ALL_IN_SCOPE",
        "environment_id": "env-prod", "cluster_id": "prod-a",
        "profile": _profile().model_dump(mode="json"),
    }).json()["data"]["run"]
    task_id = run["task_ids"][0]
    # 迟到结果：scope_revision 不匹配 → 隔离，不进入 task_statuses
    outcome = client.post(
        f"/api/v1/cases/{case['case_id']}/fanout/{run['run_id']}/task-outcome",
        json={"task_id": task_id, "status": "DONE", "scope_revision": run["scope_revision"] + 1},
    )
    assert outcome.status_code == 200, outcome.text
    data = outcome.json()["data"]
    assert task_id in data["late_result_isolated"]
    assert data["task_statuses"].get(task_id) != "DONE"


def test_fanout_recovery_is_idempotent(client: TestClient):
    _register_agents()
    case = _create_case(client)
    plan = client.put(f"/api/v1/cases/{case['case_id']}/plans", json=_cluster_plan(case)).json()["data"]
    step_id = plan["steps"][0]["step_id"]
    payload = {
        "step_id": step_id, "strategy": "ALL_IN_SCOPE",
        "environment_id": "env-prod", "cluster_id": "prod-a",
        "profile": _profile().model_dump(mode="json"),
    }
    run1 = client.post(f"/api/v1/cases/{case['case_id']}/fanout", json=payload).json()["data"]["run"]
    run2 = client.post(f"/api/v1/cases/{case['case_id']}/fanout", json=payload).json()["data"]["run"]
    # 幂等：同一逻辑 Step 重放复用既有 Task，不重复下发
    assert set(run2["task_ids"]) == set(run1["task_ids"])
    tasks = client.get("/api/tasks").json()["data"]["items"]
    fanout_tasks = [
        t for t in tasks
        if t.get("request_params", {}).get("options", {}).get("source") == "fanout_collection_run"
    ]
    assert len(fanout_tasks) == 3
