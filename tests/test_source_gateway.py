"""Enterprise source gateway, capability token and action policy tests."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.capability_tokens import (
    CapabilityTokenError,
    issue_capability_token,
    verify_capability_token,
)
from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_CAPABILITY_TOKEN_SECRET", "test-capability-secret-value-32-bytes-minimum")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("MINI_DROP_API_PRINCIPAL_ID", raising=False)
    monkeypatch.delenv("MINI_DROP_API_ROLES", raising=False)
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    repo.register_agent("agent-1", "worker-1", "10.0.0.11", capabilities=["sys_metrics"])
    repo.record_agent_metrics("agent-1", {
        "self": {"cpu_percent": 7.5, "rss_mb": 42},
        "api_key": "must-not-reach-model",
    })
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _grant_payload(*, uses_remaining=None) -> dict:
    payload = {
        "principal_id": "local-development",
        "tenant_id": "tenant-a",
        "source_ids": ["mini-drop-agent-metrics"],
        "operations": ["metrics.read"],
        "resource_scope": {"agent_id": ["agent-1"], "cluster_id": ["prod-a"]},
        "mode": "session",
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "constraints": {
            "max_result_bytes": 30_000,
            "max_queries": 2,
            "allowed_time_range_minutes": 30,
        },
    }
    if uses_remaining is not None:
        payload["uses_remaining"] = uses_remaining
    return payload


def _query_payload() -> dict:
    return {
        "tenant_id": "tenant-a",
        "operation": "metrics.read",
        "resource": {"agent_id": "agent-1", "cluster_id": "prod-a"},
        "parameters": {},
        "requested_result_bytes": 20_000,
        "requested_time_range_minutes": 15,
    }


def test_gateway_requires_grant_and_records_denial(client: TestClient):
    response = client.post("/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload())
    assert response.status_code == 403
    assert "SOURCE_APPROVAL_REQUIRED" in response.json()["detail"]
    assert any(item.event_type == "SOURCE_ACCESS_DENIED" for item in repo.audit_logs)


def test_ai_control_apis_cannot_switch_server_bound_tenant(client: TestClient):
    grant = _grant_payload()
    grant["tenant_id"] = "tenant-b"
    response = client.post("/api/v1/grants", json=grant)
    assert response.status_code == 403
    assert response.json()["detail"] == "GRANT_TENANT_MISMATCH"

    query = _query_payload()
    query["tenant_id"] = "tenant-b"
    response = client.post(
        "/api/v1/sources/mini-drop-agent-metrics/query",
        json=query,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "SOURCE_TENANT_MISMATCH"

    assert client.get("/api/v1/grants?tenant_id=tenant-b").status_code == 403

    action = {
        "tenant_id": "tenant-b",
        "environment": "production",
        "target_count": 1,
        "healthy_replicas_after_action": 3,
        "rollback_ready": True,
        "dry_run_passed": True,
    }
    response = client.post(
        "/api/v1/actions/service.drain-unhealthy-instance/evaluate",
        json=action,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "ACTION_TENANT_MISMATCH"


def test_gateway_returns_bounded_envelope_and_consumes_grant(client: TestClient):
    grant = client.post("/api/v1/grants", json=_grant_payload()).json()["data"]
    response = client.post("/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload())

    assert response.status_code == 200
    envelope = response.json()["data"]
    assert envelope["schema_version"] == "evidence-envelope.v1"
    assert envelope["principal_id"] == "local-development"
    assert envelope["policy"]["decision"] == "AUTO_GRANTED"
    assert envelope["policy"]["grant_id"] == grant["grant_id"]
    assert envelope["content_projection"]["metrics"]["api_key"] == "[REDACTED]"
    assert len(envelope["content_hash"]) == 64
    assert len(envelope["projection_hash"]) == 64
    stored = client.get("/api/v1/grants?include_inactive=true").json()["data"]["items"][0]
    assert stored["query_count"] == 1
    access_audit = next(item for item in repo.audit_logs if item.event_type == "SOURCE_ACCESS_GRANTED")
    assert access_audit.meta_json["content_hash"] == envelope["content_hash"]
    assert access_audit.meta_json["projection_hash"] == envelope["projection_hash"]
    assert access_audit.meta_json["capability_token_fingerprint"] == (
        envelope["policy"]["capability_token_fingerprint"]
    )


def test_single_use_grant_cannot_be_reused(client: TestClient):
    payload = _grant_payload(uses_remaining=1)
    payload["mode"] = "single_use"
    client.post("/api/v1/grants", json=payload)

    first = client.post("/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload())
    second = client.post("/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload())

    assert first.status_code == 200
    assert second.status_code == 403
    assert "GRANT_EXHAUSTED" in second.json()["detail"]


def test_grant_query_budget_is_enforced_before_third_read(client: TestClient):
    client.post("/api/v1/grants", json=_grant_payload())
    assert client.post(
        "/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload(),
    ).status_code == 200
    assert client.post(
        "/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload(),
    ).status_code == 200
    third = client.post(
        "/api/v1/sources/mini-drop-agent-metrics/query", json=_query_payload(),
    )
    assert third.status_code == 403
    assert "GRANT_EXHAUSTED" in third.json()["detail"]


def test_topology_connector_reads_only_the_granted_case(client: TestClient):
    diagnosis = client.post("/api/v1/diagnoses", json={
        "query": "服务 checkout CPU 飙高，请定位原因",
        "context": {
            "service_id": "checkout",
            "environment": "production",
            "instances": [{
                "service_id": "checkout",
                "instance_id": "checkout-1",
                "host_id": "host-1",
                "agent_id": "agent-1",
                "pid": 1234,
                "environment": "production",
            }],
        },
    }).json()["data"]
    diagnosis_id = diagnosis["diagnosis_id"]
    grant = {
        "principal_id": "local-development",
        "tenant_id": "tenant-a",
        "source_ids": ["mini-drop-topology-context"],
        "operations": ["topology.read"],
        "resource_scope": {
            "diagnosis_id": [diagnosis_id],
            "service_id": ["checkout"],
        },
        "mode": "case",
        "case_id": diagnosis_id,
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    assert client.post("/api/v1/grants", json=grant).status_code == 200
    result = client.post("/api/v1/sources/mini-drop-topology-context/query", json={
        "tenant_id": "tenant-a",
        "operation": "topology.read",
        "resource": {"diagnosis_id": diagnosis_id, "service_id": "checkout"},
        "parameters": {},
        "case_id": diagnosis_id,
    })

    assert result.status_code == 200
    projection = result.json()["data"]["content_projection"]
    assert projection["diagnosis_id"] == diagnosis_id
    assert projection["topology"]["nodes"]


def test_capability_token_is_bound_to_resource_and_parameters():
    token = issue_capability_token(
        principal_id="p1",
        tenant_id="t1",
        grant_id="grant-1",
        case_id="case-1",
        capability_type="source",
        capability_id="source-1",
        operation="read",
        resource={"service_id": "checkout"},
        parameters={"window": 5},
        max_result_bytes=10_000,
    )
    claims = verify_capability_token(
        token,
        principal_id="p1",
        tenant_id="t1",
        capability_type="source",
        capability_id="source-1",
        operation="read",
        resource={"service_id": "checkout"},
        parameters={"window": 5},
    )
    assert claims.grant_id == "grant-1"

    with pytest.raises(CapabilityTokenError, match="parameters_hash"):
        verify_capability_token(
            token,
            principal_id="p1",
            tenant_id="t1",
            capability_type="source",
            capability_id="source-1",
            operation="read",
            resource={"service_id": "checkout"},
            parameters={"window": 10},
        )


def test_action_registry_is_policy_only_and_blocks_unsafe_scope(client: TestClient):
    listed = client.get("/api/v1/actions").json()["data"]
    # Mini-Drop 自身维护动作已开放执行；业务动作仍 policy_only
    assert listed["execution_enabled"] is True
    executable_ids = {
        item["action_id"] for item in listed["items"]
        if item["implementation_status"] == "executable"
    }
    assert executable_ids == {
        "mini-drop.cleanup-expired-cache",
        "mini-drop.restore-cache-quarantine",
    }
    service_ids = {
        item["action_id"] for item in listed["items"]
        if item["action_id"].startswith("service.")
    }
    assert service_ids and all(item["implementation_status"] == "policy_only" for item in listed["items"] if item["action_id"].startswith("service."))

    safe_shape = {
        "tenant_id": "tenant-a",
        "environment": "production",
        "target_count": 1,
        "healthy_replicas_after_action": 3,
        "change_freeze": False,
        "rollback_ready": True,
        "dry_run_passed": True,
    }
    evaluated = client.post(
        "/api/v1/actions/service.drain-unhealthy-instance/evaluate", json=safe_shape,
    ).json()["data"]
    assert evaluated["decision"] == "USER_APPROVAL"
    assert evaluated["executable"] is False
    assert "ACTION_POLICY_ONLY_NOT_EXECUTABLE" in evaluated["reason_codes"]

    unsafe = {**safe_shape, "target_count": 10, "healthy_replicas_after_action": 0}
    evaluated = client.post(
        "/api/v1/actions/service.drain-unhealthy-instance/evaluate", json=unsafe,
    ).json()["data"]
    assert evaluated["decision"] == "DENIED"
    assert evaluated["impact_level"] == "I4"


def test_authenticated_api_key_without_admin_role_cannot_create_grant(client: TestClient, monkeypatch):
    monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_API_KEY", "operator-key")
    monkeypatch.setenv("MINI_DROP_API_ROLES", "operator")
    headers = {"X-API-Key": "operator-key"}

    identity = client.get("/api/v1/identity", headers=headers)
    assert identity.status_code == 200
    assert identity.json()["data"]["principal_id"].startswith("api-key:")
    denied = client.post("/api/v1/grants", json=_grant_payload(), headers=headers)
    assert denied.status_code == 403


def test_authenticated_production_mode_keeps_source_gateway_disabled_until_enabled(
    client: TestClient, monkeypatch,
):
    monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_API_KEY", "enterprise-key")
    monkeypatch.setenv("MINI_DROP_API_ROLES", "operator,authorization_admin")
    monkeypatch.delenv("MINI_DROP_AI_SOURCE_ACCESS_ENABLED", raising=False)
    headers = {"X-API-Key": "enterprise-key"}
    principal = client.get("/api/v1/identity", headers=headers).json()["data"]["principal_id"]
    grant = _grant_payload()
    grant["principal_id"] = principal
    assert client.post("/api/v1/grants", json=grant, headers=headers).status_code == 200

    denied = client.post(
        "/api/v1/sources/mini-drop-agent-metrics/query",
        json=_query_payload(),
        headers=headers,
    )
    assert denied.status_code == 503
    assert denied.json()["detail"] == "GLOBAL_SOURCE_ACCESS_DISABLED"
