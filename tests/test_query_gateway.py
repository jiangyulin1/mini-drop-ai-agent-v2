"""G4: Query Gateway compiles registered operations to native Tasks."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "test-token")
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
    created = client.post("/api/v1/cases", json={
        "title": "query-gateway-case",
        "problem_description": "服务变慢，请定位",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def test_query_operation_catalog_is_read_only_registry(client: TestClient):
    resp = client.get("/api/v1/query-operations")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    ids = {item["operation_id"] for item in items}
    assert {"process.list", "system.metrics", "service.connection", "service.logs"} <= ids
    for item in items:
        assert item["risk"] == "READ_LOW"
        assert item["collector_id"]


def test_query_creates_native_task_with_case_provenance(client: TestClient):
    repo.register_agent(
        "agent-q", "node-q", "192.168.50.10", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={"operation": "process.list", "parameters": {}, "idempotency_key": "q-1"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    task = repo.tasks[data["task"]["id"]]
    assert task.collector_type == "process_scan"
    assert task.status == "PENDING"
    options = task.request_params.get("options")
    assert options["source"] == "query_gateway"
    assert options["case_id"] == case["case_id"]
    assert options["query_operation"] == "process.list"
    assert options["risk"] == "READ_LOW"


def test_query_rejects_unknown_and_dangerous_parameters(client: TestClient):
    repo.register_agent(
        "agent-q2", "node-q2", "192.168.50.11", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    case = _create_case(client)
    unknown = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={"operation": "nope", "parameters": {}},
    )
    assert unknown.status_code == 400

    dangerous = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={"operation": "system.metrics", "parameters": {"executable": "bash"}},
    )
    assert dangerous.status_code == 400
    assert dangerous.json()["detail"].startswith("INVALID_QUERY_PARAMETERS")

    direct_shell = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={"operation": "system.metrics", "parameters": {"shell": "/bin/sh"}},
    )
    assert direct_shell.status_code == 400


def test_query_without_online_agent_returns_conflict(client: TestClient):
    case = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={"operation": "process.list", "parameters": {}},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "QUERY_TARGET_UNAVAILABLE"


def test_internal_query_tool_creates_native_task(client: TestClient):
    repo.register_agent(
        "agent-q3", "node-q3", "192.168.50.12", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    resp = client.post(
        "/internal/agent/tools/query",
        json={"case_id": case["case_id"], "operation": "process.list", "parameters": {}},
        headers={"X-Internal-Token": "test-token"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    task = repo.tasks[data["task"]["id"]]
    assert task.collector_type == "process_scan"
    options = task.request_params.get("options")
    assert options["created_by"] == "mini-drop-pi-runtime"


def test_internal_query_tool_respects_dry_run_and_deny_write(client: TestClient):
    repo.register_agent(
        "agent-q4", "node-q4", "192.168.50.13", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    before = len(repo.tasks)

    dry = client.post(
        "/internal/agent/tools/query",
        json={
            "case_id": case["case_id"],
            "operation": "process.list",
            "parameters": {},
            "runtime_policy": {"execution_mode": "dry_run"},
        },
        headers={"X-Internal-Token": "test-token"},
    )
    assert dry.status_code == 403, dry.text
    assert dry.json()["detail"].startswith("ACTION_BLOCKED_BY_EXECUTION_MODE")
    assert len(repo.tasks) == before

    denied = client.post(
        "/internal/agent/tools/query",
        json={
            "case_id": case["case_id"],
            "operation": "process.list",
            "parameters": {},
            "runtime_policy": {"execution_mode": "deny_write"},
        },
        headers={"X-Internal-Token": "test-token"},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"] == "WRITE_DENIED_BY_RUNTIME_POLICY"
    assert len(repo.tasks) == before
