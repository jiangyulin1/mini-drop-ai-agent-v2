"""Unknown-topology discovery: bounded cross-Agent workflow and Evidence output."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    repo.register_agent(
        "agent-a", "worker-a", "10.0.0.1",
        capabilities=["network_discovery", "sys_metrics"],
    )
    repo.register_agent(
        "agent-b", "worker-b", "10.0.0.2",
        capabilities=["network_discovery", "sys_metrics"],
    )
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _create_case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "未知拓扑延迟调查",
        "problem_description": "只知道 worker-a 上的客户端 PID，需要发现真实上下游",
        "recovery_goal": "定位依赖路径并保留无法解析的边界",
        "environment": "test",
        "target_scope": {
            "service_id": "client-service",
            "instances": [{
                "service_id": "client-service",
                "instance_id": "client-a-1",
                "host_id": "worker-a",
                "agent_id": "agent-a",
                "pid": 111,
            }],
        },
    })
    assert response.status_code == 200
    return response.json()["data"]


def _endpoint(address: str, port: int) -> dict:
    return {"address": address, "port": port, "protocol": "tcp"}


def _process(agent_id: str, boot_id: str, pid: int, start: int, name: str) -> dict:
    return {
        "agent_id": agent_id,
        "boot_id": boot_id,
        "pid": pid,
        "process_start_time": start,
        "comm": name,
        "executable": name,
        "netns": "net-1",
        "cgroup_id": "",
    }


def _complete_network_task(task, payload: dict, artifact_root: Path) -> None:
    task_id = task.id
    output_dir = artifact_root / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "network_discovery.json"
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    output_path.write_bytes(raw)
    repo.transition_task(task_id, TaskStatus.RUNNING, "测试 Agent 领取", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "测试 Agent 上传", Actor.AGENT)
    repo.add_artifacts(task_id, [{
        "artifact_type": "network_discovery",
        "filename": output_path.name,
        "local_path": str(output_path),
        "content_type": "application/json",
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "metadata": {
            "schema_version": "network_discovery.v1",
            "process_count": len(payload.get("processes") or []),
            "listener_count": len(payload.get("listeners") or []),
            "connection_count": len(payload.get("connections") or []),
            "coverage_status": "complete",
        },
    }])
    repo.transition_task(task_id, TaskStatus.ANALYZING, "测试分析", Actor.ANALYZER)
    repo.transition_task(task_id, TaskStatus.DONE, "测试完成", Actor.ANALYZER)


def _task_for_phase(run_id: str, phase: str):
    for task in repo.tasks.values():
        options = (task.request_params or {}).get("options") or {}
        if options.get("discovery_run_id") == run_id and options.get("discovery_phase") == phase:
            return task
    raise AssertionError(f"missing discovery task phase={phase}")


def test_cross_agent_discovery_resolves_listener_expands_process_and_materializes_evidence(
    client: TestClient, tmp_path: Path,
):
    case = _create_case(client)
    started = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs",
        json={
            "seed_agent_id": "agent-a",
            "seed_pid": 111,
            "max_hops": 2,
            "include_loopback": True,
            "wait_timeout_sec": 0,
        },
    )
    assert started.status_code == 200
    start_data = started.json()["data"]
    assert start_data["status"] == "COLLECTING"
    run_id = start_data["run_id"]
    seed_task = _task_for_phase(run_id, "seed")
    seed_options = (seed_task.request_params or {})["options"]
    assert seed_options["scope"] == "target"
    assert seed_options["include_loopback"] is True
    membership_snapshot_id = seed_options["membership_snapshot_id"]
    assert repo.get_membership_snapshot(
        case["case_id"], "local-development", membership_snapshot_id,
    )["members"]

    now = datetime.now(timezone.utc).isoformat()
    _complete_network_task(seed_task, {
        "schema_version": "network_discovery.v1",
        "agent_id": "agent-a",
        "boot_id": "boot-a",
        "observed_at": now,
        "host_id": "worker-a",
        "host_addresses": ["10.0.0.1"],
        "clock_quality": "good",
        "processes": [_process("agent-a", "boot-a", 111, 1001, "client")],
        "listeners": [],
        "connections": [{
            "event_id": "edge-a-b",
            "observed_at": now,
            "pid": 111,
            "local": _endpoint("10.0.0.1", 41000),
            "remote": _endpoint("10.0.0.2", 9000),
            "observation_point": "client",
            "result": "success",
        }],
    }, Path(tmp_path / "artifacts"))

    first_advance = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert first_advance.status_code == 200
    assert first_advance.json()["data"]["status"] == "COLLECTING"
    listener_task = _task_for_phase(run_id, "resolve_listener")
    assert listener_task.agent_id == "agent-b"
    assert listener_task.target_pid == 1
    listener_options = (listener_task.request_params or {})["options"]
    assert listener_options["scope"] == "host"
    assert listener_options["listener_ports"] == [9000]

    _complete_network_task(listener_task, {
        "schema_version": "network_discovery.v1",
        "agent_id": "agent-b",
        "boot_id": "boot-b",
        "observed_at": now,
        "host_id": "worker-b",
        "host_addresses": ["10.0.0.2"],
        "clock_quality": "good",
        "processes": [_process("agent-b", "boot-b", 222, 2002, "orders")],
        "listeners": [{
            "observed_at": now,
            "pid": 222,
            "local": _endpoint("0.0.0.0", 9000),
            "confidence": 0.9,
        }],
        "connections": [],
    }, Path(tmp_path / "artifacts"))

    second_advance = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert second_advance.status_code == 200
    assert second_advance.json()["data"]["status"] == "COLLECTING"
    process_task = _task_for_phase(run_id, "expand_process")
    assert process_task.agent_id == "agent-b"
    assert process_task.target_pid == 222

    _complete_network_task(process_task, {
        "schema_version": "network_discovery.v1",
        "agent_id": "agent-b",
        "boot_id": "boot-b",
        "observed_at": now,
        "host_id": "worker-b",
        "host_addresses": ["10.0.0.2"],
        "clock_quality": "good",
        "processes": [_process("agent-b", "boot-b", 222, 2002, "orders")],
        "listeners": [{
            "observed_at": now,
            "pid": 222,
            "local": _endpoint("0.0.0.0", 9000),
            "confidence": 0.9,
        }],
        "connections": [{
            "event_id": "edge-b-external",
            "observed_at": now,
            "pid": 222,
            "local": _endpoint("10.0.0.2", 42000),
            "remote": _endpoint("203.0.113.10", 5432),
            "observation_point": "client",
            "result": "timeout",
        }],
    }, Path(tmp_path / "artifacts"))

    final = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert final.status_code == 200
    result = final.json()["data"]
    assert result["status"] in {"COMPLETED", "PARTIAL"}
    graph = result["graph"]
    assert graph["summary"]["edge_count"] == 2
    assert any(node["entity_type"] == "process" and node["agent_id"] == "agent-b" for node in graph["nodes"])
    assert any(node["entity_type"] == "external_unmanaged_endpoint" for node in graph["nodes"])
    assert "external_unmanaged_endpoints_not_collectable" in graph["limitations"]
    assert graph["membership_snapshot_id"] == membership_snapshot_id

    evidence = repo.list_case_evidence(case["case_id"], "local-development")
    assert len([item for item in evidence if item["artifact_type"] == "network_discovery"]) == 3
    graph_evidence = next(item for item in evidence if item["artifact_type"] == "dependency_graph")
    assert graph_evidence["membership_snapshot_id"] == membership_snapshot_id
    projections = repo.list_evidence_projections(
        case["case_id"], "local-development", graph_evidence["evidence_id"],
    )
    assert projections[0]["projection_kind"] == "TOPOLOGY_GRAPH"
    assert len((projections[0]["content"].get("topology") or {}).get("edges") or []) == 2

    case_graph = client.get(
        f"/api/v1/cases/{case['case_id']}/dependency-graph",
    )
    assert case_graph.status_code == 200, case_graph.text
    assert case_graph.json()["data"]["coverage"]["edge_count"] == 2
    assert graph_evidence["evidence_id"] in case_graph.json()["data"]["evidence_refs"]

    detail = client.get(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}",
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["completed"]["graph_digest"] == graph["graph_digest"]


def test_discovery_waits_for_task_terminal_after_artifact_upload(
    client: TestClient, tmp_path: Path,
):
    case = _create_case(client)
    started = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs",
        json={"seed_agent_id": "agent-a", "seed_pid": 111, "wait_timeout_sec": 0},
    )
    run_id = started.json()["data"]["run_id"]
    seed_task = _task_for_phase(run_id, "seed")
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": "network_discovery.v1",
        "agent_id": "agent-a",
        "boot_id": "boot-a",
        "observed_at": now,
        "host_id": "worker-a",
        "host_addresses": ["10.0.0.1"],
        "clock_quality": "good",
        "coverage": {"status": "complete"},
        "processes": [_process("agent-a", "boot-a", 111, 1001, "client")],
        "listeners": [],
        "connections": [],
    }
    output_dir = Path(tmp_path / "artifacts" / seed_task.id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "network_discovery.json"
    raw = json.dumps(payload).encode()
    output_path.write_bytes(raw)
    repo.transition_task(seed_task.id, TaskStatus.RUNNING, "claimed", Actor.AGENT)
    repo.transition_task(seed_task.id, TaskStatus.UPLOADING, "uploading", Actor.AGENT)
    repo.add_artifacts(seed_task.id, [{
        "artifact_type": "network_discovery",
        "filename": output_path.name,
        "local_path": str(output_path),
        "content_type": "application/json",
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "metadata": {"coverage_status": "complete"},
    }])
    repo.transition_task(seed_task.id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)

    collecting = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert collecting.status_code == 200, collecting.text
    assert collecting.json()["data"]["status"] == "COLLECTING"
    detail = client.get(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}",
    ).json()["data"]
    assert detail["completed"] is None

    repo.transition_task(seed_task.id, TaskStatus.DONE, "done", Actor.ANALYZER)
    finished = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["data"]["status"] in {"COMPLETED", "PARTIAL"}
    case_graph = client.get(
        f"/api/v1/cases/{case['case_id']}/dependency-graph",
    ).json()["data"]
    completed = client.get(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}",
    ).json()["data"]["completed"]
    assert completed["snapshot_graph_digest"] == completed["graph_digest"]
    assert completed["case_aggregate_graph_digest"] == case_graph["graph_digest"]


def test_discovery_seed_cannot_escape_case_scope(client: TestClient):
    case = _create_case(client)
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs",
        json={"seed_agent_id": "agent-b", "seed_pid": 222, "wait_timeout_sec": 0},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "DISCOVERY_SEED_OUT_OF_CASE_SCOPE"


def test_discovery_run_is_fenced_when_case_scope_revision_changes(client: TestClient):
    case = _create_case(client)
    started = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs",
        json={"seed_agent_id": "agent-a", "seed_pid": 111, "wait_timeout_sec": 0},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["data"]["run_id"]
    current = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]

    corrected = client.post(
        f"/api/v1/cases/{case['case_id']}/corrections",
        json={
            "environment": "test-revised",
            "reason": "operator corrected the investigation scope",
            "expected_row_version": current["row_version"],
        },
    )
    assert corrected.status_code == 200, corrected.text

    advanced = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert advanced.status_code == 409
    assert advanced.json()["detail"] == "DISCOVERY_SCOPE_REVISION_CHANGED"


def test_failed_seed_finishes_as_partial_instead_of_staying_collecting(client: TestClient):
    case = _create_case(client)
    started = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs",
        json={"seed_agent_id": "agent-a", "seed_pid": 111, "wait_timeout_sec": 0},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["data"]["run_id"]
    seed_task = _task_for_phase(run_id, "seed")
    repo.transition_task(seed_task.id, TaskStatus.RUNNING, "agent claimed", Actor.AGENT)
    repo.transition_task(seed_task.id, TaskStatus.FAILED, "procfs unavailable", Actor.AGENT)

    advanced = client.post(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}/advance",
        json={"wait_timeout_sec": 0},
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["data"]["status"] == "PARTIAL"

    detail = client.get(
        f"/api/v1/cases/{case['case_id']}/topology/discovery-runs/{run_id}",
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "PARTIAL"
    assert detail.json()["data"]["completed"]["limitations"] == [
        "no_network_discovery_artifact_available",
    ]
