"""Registered source and durable Grant policy tests."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
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


def _grant_payload() -> dict:
    return {
        "principal_id": "user-1",
        "tenant_id": "tenant-a",
        "source_ids": ["mini-drop-agent-metrics"],
        "operations": ["metrics.read"],
        "resource_scope": {
            "cluster_id": ["prod-a"],
            "service_id": ["checkout-*"],
        },
        "mode": "session",
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "constraints": {
            "max_result_bytes": 50_000,
            "max_queries": 10,
            "allowed_time_range_minutes": 30,
        },
        "created_by": "admin-1",
    }


def _evaluation_payload() -> dict:
    return {
        "principal_id": "user-1",
        "tenant_id": "tenant-a",
        "source_id": "mini-drop-agent-metrics",
        "operation": "metrics.read",
        "resource": {"cluster_id": "prod-a", "service_id": "checkout-api"},
        "requested_result_bytes": 20_000,
        "requested_time_range_minutes": 15,
    }


def test_source_registry_does_not_expose_credential_reference(client: TestClient):
    response = client.get("/api/v1/sources")
    assert response.status_code == 200
    sources = response.json()["data"]["items"]
    assert sources
    assert all("credential_ref" not in source for source in sources)
    assert any(source["credential_configured"] for source in sources)


def test_source_access_requires_matching_grant(client: TestClient):
    before = client.post("/api/v1/policy/evaluate-source", json=_evaluation_payload()).json()["data"]
    assert before["decision"] == "USER_APPROVAL"
    assert "NO_MATCHING_GRANT" in before["reason_codes"]

    created = client.post("/api/v1/grants", json=_grant_payload())
    assert created.status_code == 200
    grant = created.json()["data"]

    after = client.post("/api/v1/policy/evaluate-source", json=_evaluation_payload()).json()["data"]
    assert after["decision"] == "AUTO_GRANTED"
    assert after["matched_grant_id"] == grant["grant_id"]
    assert after["impact_level"] == "I0"


def test_scope_and_budget_cannot_be_expanded_by_request(client: TestClient):
    client.post("/api/v1/grants", json=_grant_payload())
    outside = _evaluation_payload()
    outside["resource"]["service_id"] = "payments-api"
    decision = client.post("/api/v1/policy/evaluate-source", json=outside).json()["data"]
    assert decision["decision"] == "USER_APPROVAL"
    assert "RESOURCE_OUT_OF_SCOPE" in decision["reason_codes"]

    oversized = _evaluation_payload()
    oversized["requested_result_bytes"] = 60_000
    decision = client.post("/api/v1/policy/evaluate-source", json=oversized).json()["data"]
    assert decision["decision"] == "USER_APPROVAL"
    assert "GRANT_RESULT_BUDGET_EXCEEDED" in decision["reason_codes"]


def test_revoked_grant_stops_automatic_access(client: TestClient):
    grant = client.post("/api/v1/grants", json=_grant_payload()).json()["data"]
    revoked = client.delete(f"/api/v1/grants/{grant['grant_id']}?revoked_by=admin-2")
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "REVOKED"

    decision = client.post("/api/v1/policy/evaluate-source", json=_evaluation_payload()).json()["data"]
    assert decision["decision"] == "USER_APPROVAL"
    assert "GRANT_NOT_ACTIVE" in decision["reason_codes"]


def test_grant_rejects_unregistered_source_and_resource_dimension(client: TestClient):
    payload = _grant_payload()
    payload["source_ids"] = ["unknown-source"]
    assert client.post("/api/v1/grants", json=payload).status_code == 400

    payload = _grant_payload()
    payload["resource_scope"]["database_password"] = ["*"]
    assert client.post("/api/v1/grants", json=payload).status_code == 400
