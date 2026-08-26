"""E3: internal Tool Gateway — token gate, read-only projections, STALE_PLAN."""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base

TOKEN = "test-internal-token"


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _headers() -> dict:
    return {"X-Internal-Token": TOKEN}


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "tool-gateway-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _create_topology_case(client: TestClient) -> dict:
    repo.register_agent(
        "agent-topology", "topology-host", "10.30.0.10",
        capabilities=["network_discovery", "sys_metrics"],
    )
    created = client.post("/api/v1/cases", json={
        "title": "topology-tool-case",
        "problem_description": "只知道种子 PID，需要发现跨主机依赖",
        "recovery_goal": "形成有覆盖边界的依赖图",
        "run_mode": "COLLABORATE",
        "environment": "test",
        "target_scope": {
            "service_id": "seed-service",
            "instances": [{
                "service_id": "seed-service",
                "instance_id": "seed-1",
                "agent_id": "agent-topology",
                "host_id": "topology-host",
                "pid": 4321,
            }],
        },
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _create_top_level_target_case(
    client: TestClient, *, agent_id: str, pid: int | None,
) -> dict:
    target_scope = {"agent_id": agent_id}
    if pid is not None:
        target_scope["pid"] = pid
    created = client.post("/api/v1/cases", json={
        "title": "top-level-target-case",
        "problem_description": "验证顶层 Agent 目标范围",
        "recovery_goal": "只采集明确授权的目标",
        "run_mode": "COLLABORATE",
        "environment": "test",
        "target_scope": target_scope,
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _persist_discovered_remote_authority(
    case_id: str,
    *,
    run_id: str = "discovery-0123456789abcdefabcd",
    evidence_id: str = "ev-discovered-remote",
    remote_agent_id: str = "agent-discovered-remote",
    remote_pid: int = 9876,
    membership_snapshot_id: str = "snap-discovery-authority",
    projection_membership_snapshot_id: str | None = None,
    create_membership_snapshot: bool = True,
    membership_scope_revision_delta: int = 0,
    include_remote_member: bool = True,
    remote_entity_type: str = "process",
) -> tuple[str, str]:
    case = repo.get_incident_case(case_id, "tenant-a")
    assert case is not None
    if create_membership_snapshot and repo.get_membership_snapshot(
        case_id, "tenant-a", membership_snapshot_id,
    ) is None:
        members = [{
            "agent_id": "agent-topology",
            "hostname": "topology-host",
            "ip_addr": "10.30.0.10",
            "online": True,
            "pid": 4321,
        }]
        if include_remote_member:
            members.append({
                "agent_id": remote_agent_id,
                "hostname": "remote-host",
                "ip_addr": "10.30.0.20",
                "online": True,
                "pid": remote_pid,
            })
        repo.create_membership_snapshot(case_id, "tenant-a", {
            "snapshot_id": membership_snapshot_id,
            "environment_id": str(case.get("environment") or "test"),
            "cluster_id": "",
            "topology_version": "network-discovery.v1",
            "scope_revision": (
                int(case.get("scope_revision") or 1)
                + membership_scope_revision_delta
            ),
            "members": members,
        })
    seed_entity = "process:agent-topology:boot-seed:4321:100"
    remote_entity = (
        f"process:{remote_agent_id}:boot-remote:{remote_pid}:200"
        if remote_entity_type == "process"
        else f"{remote_entity_type}:tcp://10.30.0.20:8080"
    )
    remote_node = {
        "entity_id": remote_entity,
        "entity_type": remote_entity_type,
        "display_name": "remote-payment",
        "agent_id": remote_agent_id,
        "confidence": 0.95,
        "attributes": {},
    }
    if remote_entity_type == "process":
        remote_node["process"] = {
            "agent_id": remote_agent_id,
            "boot_id": "boot-remote",
            "pid": remote_pid,
            "process_start_time": 200,
        }
    content = {
        "artifact_type": "dependency_graph",
        "discovery_run_id": run_id,
        "membership_snapshot_id": (
            membership_snapshot_id
            if projection_membership_snapshot_id is None
            else projection_membership_snapshot_id
        ),
        "graph": {
            "schema_version": "dependency-graph.v1",
            "nodes": [
                {
                    "entity_id": seed_entity,
                    "entity_type": "process",
                    "display_name": "seed",
                    "agent_id": "agent-topology",
                    "confidence": 0.95,
                    "attributes": {},
                    "process": {
                        "agent_id": "agent-topology",
                        "boot_id": "boot-seed",
                        "pid": 4321,
                        "process_start_time": 100,
                    },
                },
                remote_node,
            ],
            "edges": [{
                "schema_version": "dependency-edge.v1",
                "edge_id": "dep-discovered-remote",
                "source_entity": seed_entity,
                "target_entity": remote_entity,
                "relation": "calls",
                "protocol": "tcp",
                "destination_port": 8080,
                "window": {
                    "start": "2026-08-21T01:00:00Z",
                    "end": "2026-08-21T01:00:01Z",
                },
                "metrics": {"connections": 1, "active_connections": 1},
                "identity_confidence": 0.95,
                "direction_confidence": 0.9,
                "observation_points": ["client", "server"],
                "evidence_refs": [evidence_id],
                "event_refs": ["event-client", "event-server"],
            }],
            "identity_assertions": [],
        },
        "coverage": {
            "status": "complete",
            "conclusion": "dependency",
            "managed_unresolved_count": 0,
            "external_unmanaged_count": 0,
            "virtual_endpoint_count": 0,
        },
        "limitations": ["dependency_edges_are_observations_not_causal_claims"],
    }
    projection_hash = hashlib.sha256(json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    repo.upsert_case_evidence(
        case_id=case_id,
        tenant_id="tenant-a",
        evidence_id=evidence_id,
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="dependency_graph",
        collector_id="network_discovery",
        source_type="task_artifact",
        target_ref=seed_entity,
        content_hash=projection_hash,
        projection_hash=projection_hash,
        membership_snapshot_id=membership_snapshot_id,
        time_window={
            "start": "2026-08-21T01:00:00Z",
            "end": "2026-08-21T01:00:01Z",
        },
    )
    repo.upsert_evidence_projection(
        evidence_id=evidence_id,
        case_id=case_id,
        tenant_id="tenant-a",
        projection_kind="DEPENDENCY_GRAPH",
        content=content,
        projection_schema="dependency-graph-projection.v1",
        projection_version=1,
        truncated=False,
        source_bytes=len(json.dumps(content)),
        parser_version="test-discovery-authority.v1",
    )
    return run_id, evidence_id


def test_internal_tool_requires_token(client: TestClient):
    resp = client.post("/internal/agent/tools/case-snapshot", json={"case_id": "x"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "INTERNAL_TOKEN_REQUIRED"


def test_internal_catalog_is_authenticated_and_canonical(client: TestClient):
    denied = client.get("/internal/agent/tools/catalog")
    assert denied.status_code == 401
    response = client.get("/internal/agent/tools/catalog", headers=_headers())
    assert response.status_code == 200
    catalog = response.json()["data"]
    assert catalog["schema_version"] == "tool-catalog.v1"
    names = {item["name"] for item in catalog["tools"]}
    assert len(names) == 25
    assert {
        "get_case_snapshot", "propose_collection", "propose_plan_revision",
        "get_dependency_graph", "discover_topology",
        "propose_hypothesis_revision", "record_evidence_gaps", "propose_causal_graph",
        "submit_evidence_analysis", "finish_investigation", "propose_evidence_dependency",
    } <= names
    assert {"evaluate_hypotheses", "rca_candidate_analysis", "request_operation"}.isdisjoint(names)
    by_name = {item["name"]: item for item in catalog["tools"]}
    recommendation = by_name["finish_investigation"]["parameters"]["properties"]["recommendations"]["items"]
    assert set(recommendation["required"]) == {"cause_or_edge_ref", "target", "concrete_action"}
    graph = by_name["propose_causal_graph"]["parameters"]["properties"]
    assert "node_id" in graph["nodes"]["items"]["properties"]
    assert "source_node_id" in graph["edges"]["items"]["properties"]
    hypotheses = by_name["propose_hypothesis_revision"]["parameters"]["properties"]["hypotheses"]["items"]
    assert set(hypotheses["required"]) == {"hypothesis_id", "statement", "status"}
    gaps = by_name["record_evidence_gaps"]["parameters"]["properties"]["gaps"]["items"]
    assert set(gaps["required"]) == {"required_fact", "status"}
    collection = by_name["propose_collection"]["parameters"]["properties"]
    assert collection["discovery_run_id"]["pattern"] == r"^discovery-[0-9a-f]{20}$"
    assert collection["discovery_evidence_refs"]["maxItems"] == 32


def test_public_runtime_config_exposes_safe_strategy_and_schema_summaries(client: TestClient):
    response = client.get("/api/v1/agent-runtime/config")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "deterministic"
    assert data["ready"] is True
    assert data["ai_ready"] is False
    assert data["ai_status"] == "NOT_CONFIGURED"
    assert {item["strategy_id"] for item in data["available_strategies"]} == {"hybrid"}
    assert len(data["tool_catalog"]["tools"]) == 25
    assert all("internal_path" not in item for item in data["tool_catalog"]["tools"])
    assert data["runtime_policy_schema"]["title"] == "RuntimePolicy"
    assert data["runtime_options_schema"]["title"] == "RuntimeOptions"


def test_runtime_policy_can_remove_proposal_tools_at_gateway(client: TestClient):
    case = _create_case(client)
    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-a", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "runtime_policy": {"side_effect_policy": "READ_ONLY"},
        },
        headers=_headers(),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "TURN_READ_ONLY"


def test_topology_tool_propose_only_persists_bounded_seed_without_task(client: TestClient):
    case = _create_topology_case(client)
    response = client.post(
        "/internal/agent/tools/topology-discovery",
        json={
            "case_id": case["case_id"],
            "seed_agent_id": "agent-topology",
            "seed_pid": 4321,
            "wait_timeout_sec": 0,
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "idempotency_key": "agent-proposed-topology",
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "PROPOSED"
    assert data["proposal"]["status"] == "PROPOSED"
    assert data["task_ids"] == []
    assert not [task for task in repo.tasks.values() if task.collector_type == "network_discovery"]
    started = next(
        event for event in repo.list_case_events(case["case_id"], "tenant-a", limit=100)
        if event["event_type"] == "topology_discovery_started"
    )
    assert started["payload"]["membership_snapshot_id"]
    assert started["payload"]["execution_authority"] == "PROPOSE_ONLY"


def test_topology_tool_auto_read_low_dispatches_only_case_seed(client: TestClient):
    case = _create_topology_case(client)
    response = client.post(
        "/internal/agent/tools/topology-discovery",
        json={
            "case_id": case["case_id"],
            "seed_agent_id": "agent-topology",
            "seed_pid": 4321,
            "wait_timeout_sec": 0,
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {
                "side_effect_policy": "AUTO_READ_LOW",
                "allowed_risk_levels": ["R1"],
                "max_collection_requests": 2,
                "max_collection_duration_sec": 20,
            },
        },
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "COLLECTING"
    task = repo.tasks[data["task_ids"][0]]
    assert task.agent_id == "agent-topology"
    assert task.target_pid == 4321
    assert task.collector_type == "network_discovery"
    options = task.request_params["options"]
    assert options["membership_snapshot_id"]
    assert options["discovery_phase"] == "seed"


def test_topology_tool_ignores_provider_invented_first_call_run_id(client: TestClient):
    case = _create_topology_case(client)
    response = client.post(
        "/internal/agent/tools/topology-discovery",
        json={
            "case_id": case["case_id"],
            "run_id": "topo_run_001",
            "seed_agent_id": "agent-topology",
            "seed_pid": 4321,
            "wait_timeout_sec": 0,
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {
                "side_effect_policy": "AUTO_READ_LOW",
                "allowed_risk_levels": ["R1"],
                "max_collection_requests": 2,
                "max_collection_duration_sec": 20,
            },
        },
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "COLLECTING"
    assert data["run_id"].startswith("discovery-")
    assert data["run_id"] != "topo_run_001"
    assert data["compatibility"] == {
        "ignored_unrecognized_run_id": "topo_run_001",
        "reason": "FIRST_CALL_RUN_ID_IS_SERVER_OWNED",
    }
    task = repo.tasks[data["task_ids"][0]]
    assert task.collector_type == "network_discovery"


def test_topology_tool_rejects_read_only_and_stale_scope_before_snapshot(client: TestClient):
    case = _create_topology_case(client)
    read_only = client.post(
        "/internal/agent/tools/topology-discovery",
        json={
            "case_id": case["case_id"],
            "runtime_policy": {"side_effect_policy": "READ_ONLY"},
        },
        headers=_headers(),
    )
    assert read_only.status_code == 409
    assert read_only.json()["detail"] == "TURN_READ_ONLY"

    stale = client.post(
        "/internal/agent/tools/topology-discovery",
        json={
            "case_id": case["case_id"],
            "expected_scope_revision": case["scope_revision"] + 1,
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "STALE_SCOPE_REVISION"
    assert not [
        event for event in repo.list_case_events(case["case_id"], "tenant-a", limit=100)
        if event["event_type"] == "topology_discovery_started"
    ]


def test_internal_case_snapshot_returns_projection(client: TestClient):
    case = _create_case(client)
    resp = client.post(
        "/internal/agent/tools/case-snapshot",
        json={"case_id": case["case_id"]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["case_id"] == case["case_id"]
    assert "plan" in data and "attachments" in data
    assert isinstance(data.get("evidence"), list)
    assert "process.list" in data.get("query_operations", [])


def test_case_branch_workspace_can_be_created_and_selected(client: TestClient):
    case = _create_case(client)
    created = client.post(
        f"/api/v1/cases/{case['case_id']}/branches",
        json={"label": "第一条假设", "reason": "independent_probe"},
    )
    assert created.status_code == 200, created.text
    branch_id = created.json()["data"]["branch_id"]
    listed = client.get(f"/api/v1/cases/{case['case_id']}/branches")
    assert listed.status_code == 200, listed.text
    assert any(item["branch_id"] == branch_id for item in listed.json()["data"]["items"])
    workspace = client.get(
        f"/api/v1/cases/{case['case_id']}/workspace",
        params={"branch_id": branch_id},
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["data"]["branch_id"] == branch_id
    assert workspace.json()["data"]["hypothesis_graph"]["hypotheses"] == []


def test_case_branch_keeps_user_message_in_branch_event_stream(client: TestClient):
    case = _create_case(client)
    created = client.post(
        f"/api/v1/cases/{case['case_id']}/branches",
        json={"label": "分支消息", "reason": "branch_message_scope"},
    )
    assert created.status_code == 200, created.text
    branch_id = created.json()["data"]["branch_id"]

    message = client.post(
        f"/api/v1/cases/{case['case_id']}/messages",
        json={"branch_id": branch_id, "content": "只在这个分支讨论", "kind": "message"},
    )
    assert message.status_code == 200, message.text
    payload = message.json()["data"]["payload"]
    assert payload["branch_id"] == branch_id

    branch_events = client.get(
        f"/api/v1/cases/{case['case_id']}/events",
        params={"branch_id": branch_id},
    )
    assert branch_events.status_code == 200, branch_events.text
    assert any(
        item["event_type"] == "user_message"
        and item["payload"].get("branch_id") == branch_id
        for item in branch_events.json()["data"]["items"]
    )


def test_branch_evidence_visibility_is_isolated(client: TestClient):
    case = _create_case(client)
    common = {
        "case_id": case["case_id"], "tenant_id": "tenant-a",
        "attachment_id": None, "task_id": None, "artifact_id": None,
        "artifact_type": "metric", "collector_id": "test",
        "source_type": "test", "target_ref": "service-a",
        "content_hash": "h", "projection_hash": "p",
    }
    repo.upsert_case_evidence(
        evidence_id="ev-branch-a", lineage={"branch_id": "branch-a", "visibility_scope": "BRANCH_LOCAL"}, **common,
    )
    repo.upsert_case_evidence(
        evidence_id="ev-branch-b", lineage={"branch_id": "branch-b", "visibility_scope": "BRANCH_LOCAL"}, **common,
    )
    repo.upsert_case_evidence(
        evidence_id="ev-seed", lineage={}, **common,
    )
    for evidence_id in ("ev-branch-a", "ev-branch-b", "ev-seed"):
        repo.upsert_evidence_projection(
            evidence_id=evidence_id, case_id=case["case_id"], tenant_id="tenant-a",
            projection_kind="metric", content={"summary": evidence_id},
            projection_schema="test.v1", projection_version=1, truncated=False,
            source_bytes=1, parser_version="test",
        )
    response = client.post(
        "/internal/agent/tools/list-case-evidence",
        json={"case_id": case["case_id"], "branch_id": "branch-a"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    ids = {item["evidence_id"] for item in response.json()["data"]["items"]}
    assert ids == {"ev-branch-a", "ev-seed"}

    promoted = repo.promote_case_evidence(
        case["case_id"], "tenant-a", "ev-branch-a", target_branch_id="branch-b",
        actor_id="operator",
    )
    assert promoted["lineage"]["visibility_scope"] == "PROMOTED"
    assert {
        item["evidence_id"] for item in client.post(
            "/internal/agent/tools/list-case-evidence",
            json={"case_id": case["case_id"], "branch_id": "branch-a"},
            headers=_headers(),
        ).json()["data"]["items"]
    } == {"ev-branch-a", "ev-seed"}
    assert {
        item["evidence_id"] for item in client.post(
            "/internal/agent/tools/list-case-evidence",
            json={"case_id": case["case_id"], "branch_id": "branch-b"},
            headers=_headers(),
        ).json()["data"]["items"]
    } == {"ev-branch-a", "ev-branch-b", "ev-seed"}
    assert {
        item["evidence_id"] for item in client.post(
            "/internal/agent/tools/list-case-evidence",
            json={"case_id": case["case_id"], "branch_id": "branch-c"},
            headers=_headers(),
        ).json()["data"]["items"]
    } == {"ev-seed"}


def test_collection_proposal_requires_scope_fence(client: TestClient):
    case = _create_case(client)
    turn_id = "turn-collection-scheduled"
    repo.record_agent_runtime_turn(
        turn_id=turn_id,
        case_id=case["case_id"],
        tenant_id="tenant-a",
        runtime_session_id=case["case_id"],
        runtime_generation=1,
        user_message="collect CPU evidence",
        requested_mode=None,
        status="ACCEPTED",
        accepted_mode="pi",
    )
    repo.register_agent(
        "agent-collector", "node-a", "192.168.9.10", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    ok = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-collector", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "trigger_turn_id": turn_id,
        },
        headers=_headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["proposal"]["status"] == "ACCEPTED"
    assert ok.json()["data"]["collection_request"]["status"] == "DISPATCHED"
    assert ok.json()["data"]["task"]["collector_type"] == "sys_metrics"
    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    goal = workspace.json()["data"]["information_goals"][0]
    assert goal["title"] == "主机和目标进程资源饱和度"
    assert goal["status"] == "COLLECTING"
    assert goal["collection_request_id"] == ok.json()["data"]["collection_request"]["collection_request_id"]
    assert goal["task_id"] == ok.json()["data"]["task"]["id"]
    assert repo.get_agent_runtime_turn(turn_id, "tenant-a")["status"] == "COMPLETED"

    stale = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-collector", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"] + 1,
        },
        headers=_headers(),
    )
    assert stale.status_code == 409
    assert "STALE_SCOPE_REVISION" in stale.json()["detail"]


def test_collection_proposal_authorizes_one_discovered_remote_target(client: TestClient):
    case = _create_topology_case(client)
    remote_agent_id = "agent-discovered-remote"
    remote_pid = 9876
    repo.register_agent(
        remote_agent_id, "remote-host", "10.30.0.20", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"], remote_agent_id=remote_agent_id, remote_pid=remote_pid,
    )

    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": remote_agent_id, "target_pid": remote_pid,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "input_evidence_refs": [evidence_id],
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["proposal"]["validation_result"]["discovery_scope_expansion"] is True
    assert data["proposal"]["input_evidence_refs"] == [evidence_id]
    assert data["task"]["agent_id"] == remote_agent_id
    assert data["task"]["target_pid"] == remote_pid
    options = repo.tasks[data["task"]["id"]].request_params["options"]
    assert options["agent_id"] == remote_agent_id
    assert options["discovery_run_id"] == run_id
    assert options["discovery_authority_evidence_ref"] == evidence_id
    assert options["discovery_authority_evidence_refs"] == [evidence_id]
    assert options["discovery_followup_authority"] is True
    assert options["membership_snapshot_id"] == "snap-discovery-authority"
    assert options["expected_boot_id"] == "boot-remote"
    assert options["expected_process_start_time"] == 200
    assert options["expected_entity_id"] == (
        f"process:{remote_agent_id}:boot-remote:{remote_pid}:200"
    )
    # The authorization is proposal-scoped; the persisted Case scope remains
    # exactly the original seed Agent and is never globally widened.
    current = repo.get_incident_case(case["case_id"], "tenant-a")
    assert {
        item["agent_id"] for item in current["target_scope"]["instances"]
    } == {"agent-topology"}


def test_same_agent_new_pid_requires_and_accepts_exact_discovery_authority(
    client: TestClient,
):
    case = _create_topology_case(client)
    remote_pid = 9876
    payload = {
        "case_id": case["case_id"],
        "collector_id": "sys_metrics",
        "target_selector": {
            "agent_id": "agent-topology", "target_pid": remote_pid,
        },
        "parameters": {"duration_sec": 15},
        "information_goal": "主机和目标进程资源饱和度",
        "expected_control_revision": case["control_revision"],
        "expected_scope_revision": case["scope_revision"],
        "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
    }

    unproven = client.post(
        "/internal/agent/tools/collection-proposal",
        json=payload,
        headers=_headers(),
    )
    assert unproven.status_code == 409
    assert unproven.json()["detail"] == "DISCOVERY_AUTHORITY_RUN_REQUIRED"
    assert not repo.list_collection_proposals(case["case_id"], "tenant-a")

    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"],
        remote_agent_id="agent-topology",
        remote_pid=remote_pid,
    )
    authorized = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            **payload,
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
        },
        headers=_headers(),
    )

    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["data"]["task"]["agent_id"] == "agent-topology"
    assert authorized.json()["data"]["task"]["target_pid"] == remote_pid
    assert authorized.json()["data"]["proposal"]["validation_result"][
        "discovery_scope_expansion"
    ] is True


@pytest.mark.parametrize("pid", [4321, None])
def test_top_level_target_scope_preserves_explicit_agent_semantics(
    client: TestClient, pid: int | None,
):
    repo.register_agent(
        "agent-top-level", "top-level-host", "10.31.0.10",
        version="0.3.0", capabilities=["sys_metrics"],
    )
    case = _create_top_level_target_case(
        client, agent_id="agent-top-level", pid=pid,
    )
    target_pid = pid or 9876
    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": "agent-top-level", "target_pid": target_pid,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["task"]["agent_id"] == "agent-top-level"
    assert response.json()["data"]["task"]["target_pid"] == target_pid
    assert response.json()["data"]["proposal"]["validation_result"][
        "discovery_scope_expansion"
    ] is False


@pytest.mark.parametrize(
    ("authority_kwargs", "expected_error"),
    [
        (
            {
                "membership_snapshot_id": "",
                "create_membership_snapshot": False,
            },
            "DISCOVERY_AUTHORITY_MEMBERSHIP_SNAPSHOT_REQUIRED:ev-discovered-remote",
        ),
        (
            {"create_membership_snapshot": False},
            "DISCOVERY_AUTHORITY_MEMBERSHIP_SNAPSHOT_NOT_FOUND:snap-discovery-authority",
        ),
        (
            {"membership_scope_revision_delta": 1},
            "DISCOVERY_AUTHORITY_MEMBERSHIP_SCOPE_MISMATCH:snap-discovery-authority",
        ),
        (
            {"include_remote_member": False},
            "DISCOVERY_AUTHORITY_TARGET_NOT_IN_MEMBERSHIP:agent-discovered-remote",
        ),
        (
            {"projection_membership_snapshot_id": "snap-other"},
            "DISCOVERY_AUTHORITY_PROJECTION_MEMBERSHIP_MISMATCH:ev-discovered-remote",
        ),
    ],
)
def test_discovered_remote_authority_requires_current_bound_membership_snapshot(
    client: TestClient,
    authority_kwargs: dict,
    expected_error: str,
):
    case = _create_topology_case(client)
    repo.register_agent(
        "agent-discovered-remote", "remote-host", "10.30.0.20",
        version="0.3.0", capabilities=["sys_metrics"],
    )
    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"], **authority_kwargs,
    )

    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": "agent-discovered-remote", "target_pid": 9876,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == expected_error
    assert not repo.list_collection_proposals(case["case_id"], "tenant-a")


@pytest.mark.parametrize(
    "entity_type",
    ["external_unmanaged_endpoint", "virtual_endpoint"],
)
def test_discovered_remote_authority_rejects_non_collectable_endpoint_types(
    client: TestClient, entity_type: str,
):
    case = _create_topology_case(client)
    repo.register_agent(
        "agent-discovered-remote", "remote-host", "10.30.0.20",
        version="0.3.0", capabilities=["sys_metrics"],
    )
    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"], remote_entity_type=entity_type,
    )
    target_entity_id = f"{entity_type}:tcp://10.30.0.20:8080"

    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": "agent-discovered-remote",
                "target_pid": 9876,
                "target_entity_id": target_entity_id,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"DISCOVERY_AUTHORITY_ENDPOINT_NOT_COLLECTABLE:{entity_type}"
    )


def test_discovery_authority_cannot_be_reused_for_an_unproven_agent_pid(
    client: TestClient,
):
    case = _create_topology_case(client)
    for agent_id, address in (
        ("agent-discovered-remote", "10.30.0.20"),
        ("agent-unproven", "10.30.0.30"),
    ):
        repo.register_agent(
            agent_id, f"{agent_id}-host", address,
            version="0.3.0", capabilities=["sys_metrics"],
        )
    run_id, evidence_id = _persist_discovered_remote_authority(case["case_id"])

    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": "agent-unproven", "target_pid": 7777,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "DISCOVERY_AUTHORITY_TARGET_NOT_IN_MEMBERSHIP:agent-unproven"
    )
    assert not [
        task for task in repo.tasks.values()
        if task.agent_id == "agent-unproven" and task.target_pid == 7777
    ]


@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"discovery_run_id": ""}, "DISCOVERY_AUTHORITY_RUN_REQUIRED"),
        ({"discovery_run_id": "discovery-aaaaaaaaaaaaaaaaaaaa"},
         "DISCOVERY_AUTHORITY_RUN_EVIDENCE_MISMATCH:ev-discovered-remote"),
        ({"target_selector": {"agent_id": "agent-discovered-remote", "target_pid": 9999}},
         "DISCOVERY_AUTHORITY_TARGET_NOT_FOUND"),
        ({"discovery_evidence_refs": ["ev-not-active"]},
         "DISCOVERY_AUTHORITY_EVIDENCE_NOT_ACTIVE:ev-not-active"),
    ],
)
def test_collection_proposal_rejects_unproven_discovered_remote_target(
    client: TestClient, override: dict, expected_error: str,
):
    case = _create_topology_case(client)
    remote_agent_id = "agent-discovered-remote"
    remote_pid = 9876
    repo.register_agent(
        remote_agent_id, "remote-host", "10.30.0.20", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"], remote_agent_id=remote_agent_id, remote_pid=remote_pid,
    )
    payload = {
        "case_id": case["case_id"],
        "collector_id": "sys_metrics",
        "target_selector": {
            "agent_id": remote_agent_id, "target_pid": remote_pid,
        },
        "parameters": {"duration_sec": 15},
        "information_goal": "主机和目标进程资源饱和度",
        "input_evidence_refs": [evidence_id],
        "discovery_run_id": run_id,
        "discovery_evidence_refs": [evidence_id],
        "expected_control_revision": case["control_revision"],
        "expected_scope_revision": case["scope_revision"],
        "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
    }
    payload.update(override)

    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json=payload,
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == expected_error
    assert not repo.list_collection_proposals(case["case_id"], "tenant-a")
    assert not [
        task for task in repo.tasks.values()
        if task.agent_id == remote_agent_id and task.collector_type == "sys_metrics"
    ]


def test_discovered_remote_authority_is_revalidated_on_human_approval(client: TestClient):
    case = _create_topology_case(client)
    remote_agent_id = "agent-discovered-remote"
    remote_pid = 9876
    repo.register_agent(
        remote_agent_id, "remote-host", "10.30.0.20", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"], remote_agent_id=remote_agent_id, remote_pid=remote_pid,
    )
    proposed = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": remote_agent_id, "target_pid": remote_pid,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers=_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()["data"]["proposal"]
    assert proposal["status"] == "PROPOSED"
    pinned = proposal["validation_result"]["approval_context"]["dispatch_context"]
    assert pinned["discovery_authority_evidence_refs"] == [evidence_id]
    assert pinned["membership_snapshot_id"] == "snap-discovery-authority"
    assert pinned["expected_boot_id"] == "boot-remote"
    assert pinned["expected_process_start_time"] == 200
    assert pinned["expected_entity_id"] == (
        f"process:{remote_agent_id}:boot-remote:{remote_pid}:200"
    )

    approved = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{proposal['proposal_id']}/decision",
        json={
            "decision": "APPROVE",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["task"]["agent_id"] == remote_agent_id
    assert approved.json()["data"]["task"]["target_pid"] == remote_pid


def test_human_approval_rejects_discovery_authority_excluded_after_proposal(
    client: TestClient,
):
    case = _create_topology_case(client)
    remote_agent_id = "agent-discovered-remote"
    remote_pid = 9876
    repo.register_agent(
        remote_agent_id, "remote-host", "10.30.0.20", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    run_id, evidence_id = _persist_discovered_remote_authority(
        case["case_id"], remote_agent_id=remote_agent_id, remote_pid=remote_pid,
    )
    proposed = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {
                "agent_id": remote_agent_id, "target_pid": remote_pid,
            },
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "discovery_run_id": run_id,
            "discovery_evidence_refs": [evidence_id],
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers=_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()["data"]["proposal"]
    assert proposal["status"] == "PROPOSED"
    repo.exclude_case_evidence(case["case_id"], "tenant-a", evidence_id)

    approved = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/"
        f"{proposal['proposal_id']}/decision",
        json={
            "decision": "APPROVE",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
        },
    )

    assert approved.status_code == 409
    assert approved.json()["detail"] == (
        f"DISCOVERY_AUTHORITY_EVIDENCE_NOT_ACTIVE:{evidence_id}"
    )
    assert not [
        task for task in repo.tasks.values()
        if task.agent_id == remote_agent_id
        and task.target_pid == remote_pid
        and task.collector_type == "sys_metrics"
    ]


def test_duplicate_collection_proposal_reuses_request_without_budget(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-dedupe", "node-a", "192.168.9.12", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    payload = {
        "case_id": case["case_id"],
        "collector_id": "sys_metrics",
        "target_selector": {"agent_id": "agent-dedupe", "target_pid": 1},
        "parameters": {"duration_sec": 15},
        "information_goal": "主机和目标进程资源饱和度",
    }
    first = client.post(
        "/internal/agent/tools/collection-proposal", json=payload, headers=_headers(),
    )
    duplicate = client.post(
        "/internal/agent/tools/collection-proposal", json=payload, headers=_headers(),
    )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    first_data = first.json()["data"]
    duplicate_data = duplicate.json()["data"]
    assert duplicate_data["collection_request"]["collection_request_id"] == first_data["collection_request"]["collection_request_id"]
    assert duplicate_data["task"]["id"] == first_data["task"]["id"]
    validation = duplicate_data["proposal"]["validation_result"]
    assert validation["duplicate"] is True
    assert validation["budget_consumed"] is False
    assert len(repo.list_collection_requests(case["case_id"], "tenant-a")) == 1


def test_collection_request_count_budget_is_a_hard_limit(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-count-budget", "node-a", "192.168.9.13", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    for index in range(2):
        response = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"],
                "collector_id": "sys_metrics",
                "target_selector": {"agent_id": "agent-count-budget", "target_pid": 1},
                "parameters": {"duration_sec": 15, "sample_rate": index + 1},
                "information_goal": "主机和目标进程资源饱和度",
                "runtime_policy": {"max_collection_requests": 1},
            },
            headers=_headers(),
        )
        if index == 0:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 409
            assert "COLLECTION_REQUEST_COUNT_BUDGET_EXHAUSTED" in response.json()["detail"]
    assert len(repo.list_collection_requests(case["case_id"], "tenant-a")) == 1


def test_collection_duration_budget_is_a_hard_limit(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-duration-budget", "node-a", "192.168.9.14", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    for sample_rate in (1, 2):
        response = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"],
                "collector_id": "sys_metrics",
                "target_selector": {"agent_id": "agent-duration-budget", "target_pid": 1},
                "parameters": {"duration_sec": 15, "sample_rate": sample_rate},
                "information_goal": "主机和目标进程资源饱和度",
                "runtime_policy": {"max_collection_duration_sec": 20},
            },
            headers=_headers(),
        )
        if sample_rate == 1:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 409
            assert "COLLECTION_REQUEST_DURATION_BUDGET_EXHAUSTED" in response.json()["detail"]
    assert len(repo.list_collection_requests(case["case_id"], "tenant-a")) == 1


def test_runtime_policy_cannot_expand_collection_budget():
    from pydantic import ValidationError

    from server.app.agent_runtime.policy import RuntimePolicy

    with pytest.raises(ValidationError):
        RuntimePolicy(max_collection_requests=9)
    with pytest.raises(ValidationError):
        RuntimePolicy(max_collection_duration_sec=241)


def test_propose_only_collection_can_be_approved_without_regenerating_proposal(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-human-approval", "node-a", "192.168.9.15", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    proposed = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"], "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-human-approval", "target_pid": 1},
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "idempotency_key": "human-approved-collection",
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers=_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()["data"]["proposal"]
    assert proposal["status"] == "PROPOSED"
    assert proposal["validation_result"]["awaiting_execution_authority"] is True
    assert proposed.json()["data"]["task"] is None

    approved = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{proposal['proposal_id']}/decision",
        json={
            "decision": "APPROVE", "reason": "read-only collection approved",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
        },
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert data["proposal"]["status"] == "ACCEPTED"
    assert data["proposal"]["validation_result"]["approval_decision"] == "APPROVE"
    assert data["collection_request"]["status"] == "DISPATCHED"
    assert data["task"]["collector_type"] == "sys_metrics"
    assert len(repo.list_collection_proposals(case["case_id"], "tenant-a")) == 1


def test_pending_collection_reject_and_revision_fence(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-human-reject", "node-a", "192.168.9.16", version="0.3.0",
        capabilities=["sys_metrics"],
    )

    def propose(key: str) -> dict:
        response = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"], "collector_id": "sys_metrics",
                "target_selector": {"agent_id": "agent-human-reject", "target_pid": 1},
                "parameters": {"duration_sec": 15},
                "information_goal": "主机和目标进程资源饱和度",
                "idempotency_key": key,
                "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
            },
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]["proposal"]

    rejected_proposal = propose("human-rejected-collection")
    rejected = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{rejected_proposal['proposal_id']}/decision",
        json={"decision": "REJECT", "reason": "not needed"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["proposal"]["status"] == "REJECTED"
    assert rejected.json()["data"]["collection_request"] is None

    fenced_proposal = propose("revision-fenced-collection")
    fenced = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{fenced_proposal['proposal_id']}/decision",
        json={
            "decision": "APPROVE",
            "expected_control_revision": case["control_revision"] + 1,
            "expected_scope_revision": case["scope_revision"],
        },
    )
    assert fenced.status_code == 409
    assert fenced.json()["detail"] == "APPROVAL_CONTROL_REVISION_MISMATCH"
    assert repo.get_collection_proposal(
        fenced_proposal["proposal_id"], case["case_id"], "tenant-a",
    )["status"] == "PROPOSED"

    current_state_proposal = propose("current-state-fenced-collection")
    original_get_case = repo.get_incident_case

    def changed_scope(case_id: str, tenant_id: str):
        current = original_get_case(case_id, tenant_id)
        return {**current, "scope_revision": int(current["scope_revision"]) + 1}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repo, "get_incident_case", changed_scope)
    try:
        stale_current = client.post(
            f"/api/v1/cases/{case['case_id']}/collection-proposals/{current_state_proposal['proposal_id']}/decision",
            json={
                "decision": "APPROVE",
                "expected_control_revision": case["control_revision"],
                "expected_scope_revision": case["scope_revision"],
            },
        )
    finally:
        monkeypatch.undo()
    assert stale_current.status_code == 409
    assert "COLLECTION_PROPOSAL_FENCED:STALE_SCOPE_REVISION" == stale_current.json()["detail"]
    assert repo.get_collection_proposal(
        current_state_proposal["proposal_id"], case["case_id"], "tenant-a",
    )["status"] == "REJECTED"


def test_collection_proposal_is_not_accepted_when_task_dispatch_fails(
    client: TestClient, monkeypatch,
):
    case = _create_case(client)
    repo.register_agent(
        "agent-dispatch-fail", "node-a", "192.168.9.17", version="0.3.0",
        capabilities=["sys_metrics"],
    )

    def fail_create_task(*args, **kwargs):
        raise RuntimeError("simulated dispatcher failure")

    monkeypatch.setattr(repo, "create_task", fail_create_task)
    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"], "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-dispatch-fail", "target_pid": 1},
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "COLLECTION_TASK_DISPATCH_FAILED"
    proposal = repo.list_collection_proposals(case["case_id"], "tenant-a")[-1]
    collection_request = repo.list_collection_requests(case["case_id"], "tenant-a")[-1]
    assert proposal["status"] == "FAILED"
    assert collection_request["status"] == "DISPATCH_FAILED"
    assert collection_request["task_id"] is None
    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    goal = workspace.json()["data"]["information_goals"][0]
    assert goal["status"] == "BLOCKED"


def test_internal_finish_requires_evidence_refs(client: TestClient):
    case = _create_case(client)
    invalid_state = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"], "summary": "invalid",
            "evidence_ids": [], "state": "DONE",
        },
        headers=_headers(),
    )
    assert invalid_state.status_code == 400
    assert invalid_state.json()["detail"] == "INVALID_CONCLUSION_STATE"
    missing = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "evidence_ids": []},
        headers=_headers(),
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "NO_EVIDENCE_REFS"
    unknown = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "evidence_ids": ["ev-1"]},
        headers=_headers(),
    )
    assert unknown.status_code == 400
    assert unknown.json()["detail"].startswith("INVALID_EVIDENCE_REFS")


def test_internal_finish_accepts_known_evidence_refs(client: TestClient):
    case = _create_case(client)
    turn_id = "turn-finish-with-trigger"
    repo.record_agent_runtime_turn(
        turn_id=turn_id,
        case_id=case["case_id"],
        tenant_id="tenant-a",
        runtime_session_id=case["case_id"],
        runtime_generation=1,
        user_message="finish the investigation",
        requested_mode=None,
        status="ACCEPTED",
        accepted_mode="pi",
    )
    repo.upsert_case_attachment(
        case["case_id"],
        "tenant-a",
        {
            "attachment_id": "attach-valid",
            "resource_type": "task",
            "resource_id": "task-valid",
            "label": "valid task",
            "source": "user_mention",
            "status": "ACCEPTED",
            "evidence_ids": ["ev-valid"],
        },
    )
    ok = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "根因是 CPU 饱和",
            "evidence_ids": ["ev-valid"],
            "state": "CONCLUDED",
            "trigger_turn_id": turn_id,
        },
        headers=_headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["accepted"] is True
    assert ok.json()["data"]["state"] == "PARTIALLY_CONFIRMED"
    assert ok.json()["data"]["assistant_message_id"]
    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert events[-1]["event_type"] == "agent_finish_investigation"
    updated = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert updated["summary"]["current_finding"]["status"] == "concluded"
    assert updated["summary"]["current_finding"]["evidence_refs"] == ["ev-valid"]
    assert updated["state"] == "WAITING_USER"
    messages = repo.list_assistant_messages(case["case_id"], "tenant-a")
    assert len(messages) == 1
    assert messages[0]["content"].startswith("PARTIALLY_CONFIRMED：根因是 CPU 饱和")
    assert repo.get_agent_runtime_turn(turn_id, "tenant-a")["status"] == "COMPLETED"


def test_tool_policy_error_enforces_needs_approval(monkeypatch):
    from server.app.agent_runtime.catalog import ToolSpec
    from server.app.agent_runtime.policy import RuntimePolicy
    from server.app.diagnosis import v6_policy

    fake_spec = ToolSpec(
        name="propose_collection",
        description="sensitive operation request",
        parameters={"type": "object"},
        internal_path="/internal/agent/tools/collection-proposal",
        policy="PROPOSE_ONLY",
        needs_approval=True,
    )
    monkeypatch.setattr(v6_policy, "get_tool_spec", lambda name: fake_spec if name == "propose_collection" else None)

    policy = RuntimePolicy(side_effect_policy="PROPOSE_ONLY")
    assert v6_policy.tool_policy_error("propose_collection", policy) == "TOOL_REQUIRES_APPROVAL"

    auto = RuntimePolicy(side_effect_policy="PROPOSE_ONLY", auto_approve=True)
    assert v6_policy.tool_policy_error("propose_collection", auto) is None


def test_operation_risk_in_require_approval_for_is_rejected_at_gateway(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-approval", "node-a", "192.168.9.11", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    resp = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-approval", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "runtime_policy": {
                "side_effect_policy": "AUTO_READ_LOW",
                "allowed_risk_levels": ["R1"],
                "require_approval_for": ["R1"],
                "auto_approve": False,
            },
        },
        headers=_headers(),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "COLLECTOR_REQUIRES_APPROVAL"


def test_finish_autofills_single_projection_hash_when_claim_omits_it(client: TestClient):
    case = _create_case(client)
    repo.upsert_case_evidence(
        case_id=case["case_id"],
        tenant_id="tenant-a",
        evidence_id="ev-auto-proj",
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="sys_metrics",
        collector_id="sys_metrics",
        source_type="task_artifact",
        target_ref="task:auto",
        content_hash="content-hash",
        projection_hash="will-be-replaced",
        time_window={},
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-auto-proj",
        case_id=case["case_id"],
        tenant_id="tenant-a",
        projection_kind="TOP_ITEMS",
        content={"summary": "cpu 100%"},
    )
    resp = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "结论：CPU 热点",
            "evidence_ids": ["ev-auto-proj"],
            "claims": [{"evidence_id": "ev-auto-proj"}],
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accepted"] is True


def test_finish_accepts_model_friendly_claim_shapes(client: TestClient):
    case = _create_case(client)
    repo.upsert_case_evidence(
        case_id=case["case_id"],
        tenant_id="tenant-a",
        evidence_id="ev-friendly",
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="sys_metrics",
        collector_id="sys_metrics",
        source_type="task_artifact",
        target_ref="task:friendly",
        content_hash="content-hash",
        projection_hash="will-be-replaced",
        time_window={},
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-friendly",
        case_id=case["case_id"],
        tenant_id="tenant-a",
        projection_kind="TOP_ITEMS",
        content={"summary": "cpu 100%"},
    )
    resp = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "结论：CPU 热点",
            "evidence_ids": ["ev-friendly"],
            "claims": [
                {"claim": "CPU hotspot", "confidence": 0.9, "supporting_evidence": ["ev-friendly"]},
                {"evidence_ids": ["ev-friendly"], "text": "user-mode spin"},
                {"evidence": "ev-friendly"},
            ],
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accepted"] is True
