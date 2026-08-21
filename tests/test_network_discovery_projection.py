"""Network discovery -> EvidenceProjection -> Case dependency graph contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis.network_discovery import (
    aggregate_dependency_graph,
    build_network_discovery_projection,
)
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


def _create_case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "unknown-topology",
        "problem_description": "checkout 调用异常，只知道 seed PID",
        "recovery_goal": "识别一跳依赖并保留不确定性",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _seed_payload() -> dict:
    return {
        "schema_version": "network_discovery.v1",
        "agent_id": "worker-a",
        "boot_id": "boot-a",
        "hostname": "node-a",
        "host_addresses": ["10.0.0.1"],
        "observed_at": "2026-08-21T01:00:00Z",
        "summary": {"seed_pid": 100, "event_count": 1},
        "coverage": {"status": "complete", "owner_resolution_ratio": 1.0},
        "processes": [{
            "pid": 100,
            "start_time_ticks": 1000,
            "comm": "checkout",
            "netns": 11,
        }],
        "listeners": [],
        "connections": [{
            "event_id": "event-a-b",
            "observed_at": "2026-08-21T01:00:00Z",
            "pid": 100,
            "local": {"address": "10.0.0.1", "port": 41000, "protocol": "tcp"},
            "remote": {"address": "10.0.0.2", "port": 8080, "protocol": "tcp"},
            "result": "success",
            "observation_point": "client",
            "direction_confidence": 0.9,
        }],
        "limitations": ["point_in_time_snapshot_misses_completed_short_connections"],
    }


def _remote_listener_payload() -> dict:
    return {
        "schema_version": "network_discovery.v1",
        "agent_id": "worker-b",
        "boot_id": "boot-b",
        "hostname": "node-b",
        "host_addresses": ["10.0.0.2"],
        "observed_at": "2026-08-21T01:00:01Z",
        "summary": {"seed_pid": 200, "event_count": 1},
        "coverage": {"status": "complete", "owner_resolution_ratio": 1.0},
        "processes": [{
            "pid": 200,
            "start_time_ticks": 2000,
            "comm": "payment",
            "netns": 22,
        }],
        "listeners": [{
            "event_id": "listen-b",
            "observed_at": "2026-08-21T01:00:01Z",
            "pid": 200,
            "endpoint": {"address": "10.0.0.2", "port": 8080, "protocol": "tcp"},
            "local": {"address": "10.0.0.2", "port": 8080, "protocol": "tcp"},
            "local_port": 8080,
            "source": "procfs",
        }],
        "connections": [],
        "limitations": [],
    }


def _context() -> dict:
    return {
        "membership_snapshot_id": "snap-1",
        "membership_snapshot": {
            "snapshot_id": "snap-1",
            "captured_at": "2026-08-21T00:59:59Z",
            "members": [
                {"agent_id": "worker-a", "hostname": "node-a", "ip_addr": "10.0.0.1", "online": True},
                {"agent_id": "worker-b", "hostname": "node-b", "ip_addr": "10.0.0.2", "online": True},
            ],
        },
        "discovery_run_id": "discovery-1",
        "scope_revision": 3,
    }


def _persist_projection(case_id: str, evidence_id: str, projection: dict) -> None:
    repo.upsert_case_evidence(
        case_id=case_id,
        tenant_id="tenant-a",
        evidence_id=evidence_id,
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="network_discovery",
        collector_id="network_discovery",
        source_type="task_artifact",
        target_ref="seed",
        content_hash=projection["projection_hash"],
        projection_hash=projection["projection_hash"],
        membership_snapshot_id="snap-1",
        time_window=projection["content"]["window"],
    )
    repo.upsert_evidence_projection(
        evidence_id=evidence_id,
        case_id=case_id,
        tenant_id="tenant-a",
        projection_kind=projection["projection_kind"],
        content=projection["content"],
        projection_schema=projection["projection_schema"],
        projection_version=projection["projection_version"],
        truncated=projection["truncated"],
        source_bytes=projection["source_bytes"],
        parser_version="deterministic-network-discovery.v1",
    )


def test_projection_is_deterministic_and_membership_aware():
    first = build_network_discovery_projection(_seed_payload(), projection_context=_context())
    second = build_network_discovery_projection(_seed_payload(), projection_context=_context())

    assert first["projection_kind"] == "DEPENDENCY_GRAPH"
    assert first["projection_hash"] == second["projection_hash"]
    assert first["content"]["graph_digest"] == second["content"]["graph_digest"]
    assert first["content"]["graph_semantics"] == "dependency_only_not_causal"
    edge = first["content"]["graph"]["edges"][0]
    assert edge["relation"] == "calls"
    target = next(
        node for node in first["content"]["graph"]["nodes"]
        if node["entity_id"] == edge["target_entity"]
    )
    assert target["entity_type"] == "managed_host_endpoint"
    assert target["agent_id"] == "worker-b"
    assert first["content"]["coverage"]["conclusion"] == "insufficient_coverage"
    assert first["content"]["quality"] == "partial"


def test_projection_keeps_canonical_evidence_and_raw_event_lineage_separate():
    context = {**_context(), "evidence_id": "ev-network-seed"}
    projection = build_network_discovery_projection(_seed_payload(), projection_context=context)
    edge = projection["content"]["graph"]["edges"][0]
    assert edge["evidence_refs"] == ["ev-network-seed"]
    assert edge["event_refs"] == ["event-a-b"]
    assert projection["content"]["source_evidence_id"] == "ev-network-seed"


def test_projection_binds_canonical_evidence_and_keeps_event_lineage_separate():
    projection = build_network_discovery_projection(
        _seed_payload(),
        projection_context={**_context(), "evidence_id": "ev-seed"},
    )
    edge = projection["content"]["graph"]["edges"][0]

    assert edge["evidence_refs"] == ["ev-seed"]
    assert edge["event_refs"] == ["event-a-b"]
    assert "event-a-b" not in edge["evidence_refs"]
    assert projection["content"]["source_evidence_id"] == "ev-seed"


def test_projection_binds_canonical_evidence_id_and_preserves_event_lineage():
    context = {**_context(), "evidence_id": "ev-network-seed"}
    projection = build_network_discovery_projection(_seed_payload(), projection_context=context)
    edge = projection["content"]["graph"]["edges"][0]
    assert edge["evidence_refs"] == ["ev-network-seed"]
    assert edge["event_refs"] == ["event-a-b"]


def test_case_merge_reconciles_initial_host_endpoint_to_remote_process():
    seed = build_network_discovery_projection(_seed_payload(), projection_context=_context())
    remote = build_network_discovery_projection(
        _remote_listener_payload(), projection_context=_context(),
    )
    merged = aggregate_dependency_graph(
        [
            {"evidence_id": "ev-seed", "status": "ACTIVE"},
            {"evidence_id": "ev-remote", "status": "ACTIVE"},
        ],
        [
            {"evidence_id": "ev-seed", **seed},
            {"evidence_id": "ev-remote", **remote},
        ],
    )

    assert merged["coverage"]["edge_count"] == 1
    edge = merged["graph"]["edges"][0]
    assert edge["source_entity"].startswith("process:worker-a:boot-a:100:")
    assert edge["target_entity"].startswith("process:worker-b:boot-b:200:")
    assert merged["graph_semantics"] == "dependency_only_not_causal"


def test_partial_or_unknown_projection_cannot_claim_dependency_completion():
    payload = _seed_payload()
    payload["coverage"] = {"status": "unknown", "reasons": []}
    projection = build_network_discovery_projection(
        payload,
        projection_context={**_context(), "evidence_id": "ev-unknown"},
    )
    assert projection["content"]["coverage"]["conclusion"] == "insufficient_coverage"

    merged = aggregate_dependency_graph(
        [{"evidence_id": "ev-unknown", "status": "ACTIVE"}],
        [{"evidence_id": "ev-unknown", **projection}],
    )
    assert merged["coverage"]["conclusion"] == "insufficient_coverage"


def test_projection_without_coverage_metadata_is_unknown_not_complete():
    payload = _seed_payload()
    projection = build_network_discovery_projection(
        payload,
        projection_context={**_context(), "evidence_id": "ev-missing-coverage"},
    )
    content = dict(projection["content"])
    content.pop("coverage", None)
    merged = aggregate_dependency_graph(
        [{"evidence_id": "ev-missing-coverage", "status": "ACTIVE"}],
        [{
            "evidence_id": "ev-missing-coverage",
            "projection_kind": "DEPENDENCY_GRAPH",
            "content": content,
        }],
    )
    assert merged["coverage"]["conclusion"] == "insufficient_coverage"


def test_case_merge_deduplicates_separate_client_and_server_snapshots():
    seed = build_network_discovery_projection(
        _seed_payload(), projection_context={**_context(), "evidence_id": "ev-seed"},
    )
    remote_payload = _remote_listener_payload()
    remote_payload["connections"] = [{
        "event_id": "event-b-a",
        "observed_at": "2026-08-21T01:00:02Z",
        "pid": 200,
        "local": {"address": "10.0.0.2", "port": 8080, "protocol": "tcp"},
        "remote": {"address": "10.0.0.1", "port": 41000, "protocol": "tcp"},
        "result": "success",
        "observation_point": "server",
        "direction_confidence": 0.9,
    }]
    remote = build_network_discovery_projection(
        remote_payload, projection_context={**_context(), "evidence_id": "ev-remote"},
    )

    merged = aggregate_dependency_graph(
        [
            {"evidence_id": "ev-seed", "status": "ACTIVE"},
            {"evidence_id": "ev-remote", "status": "ACTIVE"},
        ],
        [
            {"evidence_id": "ev-seed", **seed},
            {"evidence_id": "ev-remote", **remote},
        ],
    )

    assert merged["coverage"]["edge_count"] == 1
    edge = merged["graph"]["edges"][0]
    assert edge["source_entity"].startswith("process:worker-a:boot-a:100:")
    assert edge["target_entity"].startswith("process:worker-b:boot-b:200:")
    assert edge["observation_points"] == ["client", "server"]
    assert edge["evidence_refs"] == ["ev-remote", "ev-seed"]
    assert edge["event_refs"] == ["event-a-b", "event-b-a"]
    assert edge["window"] == {
        "start": "2026-08-21T01:00:00Z",
        "end": "2026-08-21T01:00:02Z",
    }


def test_dependency_graph_is_available_through_case_api_workspace_and_agent_tool(
    client: TestClient,
):
    case = _create_case(client)
    seed = build_network_discovery_projection(_seed_payload(), projection_context=_context())
    remote = build_network_discovery_projection(
        _remote_listener_payload(), projection_context=_context(),
    )
    _persist_projection(case["case_id"], "ev-network-seed", seed)
    _persist_projection(case["case_id"], "ev-network-remote", remote)

    public = client.get(f"/api/v1/cases/{case['case_id']}/dependency-graph")
    assert public.status_code == 200, public.text
    public_data = public.json()["data"]
    assert public_data["coverage"]["edge_count"] == 1
    assert public_data["evidence_refs"] == ["ev-network-remote", "ev-network-seed"]

    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["data"]["dependency_graph"]["graph_digest"] == public_data["graph_digest"]

    tool = client.post(
        "/internal/agent/tools/get-dependency-graph",
        json={"case_id": case["case_id"]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert tool.status_code == 200, tool.text
    assert tool.json()["data"]["graph_digest"] == public_data["graph_digest"]


def test_dependency_graph_keeps_dependency_separate_from_causal_graph(client: TestClient):
    case = _create_case(client)
    projection = build_network_discovery_projection(
        _seed_payload(), projection_context=_context(),
    )
    _persist_projection(case["case_id"], "ev-network-only", projection)

    dependency = client.get(
        f"/api/v1/cases/{case['case_id']}/dependency-graph",
    ).json()["data"]
    causal = client.post(
        "/internal/agent/tools/get-causal-graph",
        json={"case_id": case["case_id"]},
        headers={"X-Internal-Token": TOKEN},
    ).json()["data"]
    assert dependency["coverage"]["edge_count"] == 1
    assert causal["graph"] is None
