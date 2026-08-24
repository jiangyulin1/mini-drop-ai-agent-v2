"""Default-off gates for retired DiagnosisSession product paths."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, diagnosis_orchestrator, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS", raising=False)
    reset_engine()
    repo._cache.clear()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _diagnosis_payload() -> dict:
    return {
        "query": "checkout CPU 飙高，请定位原因",
        "context": {
            "service_id": "checkout",
            "environment": "production",
            "instances": [],
        },
        "budget_profile": "production_safe",
    }


def _case_payload() -> dict:
    return {
        "title": "legacy gate",
        "problem_description": "checkout latency",
        "recovery_goal": "latency recovers",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    }


def test_independent_legacy_diagnosis_write_is_disabled_by_default(client: TestClient):
    response = client.post("/api/v1/diagnoses", json=_diagnosis_payload())
    assert response.status_code == 410
    assert response.json()["detail"].startswith("LEGACY_DIAGNOSIS_DISABLED")
    assert diagnosis_orchestrator.store.count_sessions() == 0


def test_case_legacy_diagnosis_write_is_disabled_without_mutating_case(client: TestClient):
    case = client.post("/api/v1/cases", json=_case_payload()).json()["data"]
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/diagnoses",
        json={"budget_profile": "production_safe"},
    )
    assert response.status_code == 410
    current = repo.get_incident_case(case["case_id"], "tenant-a")
    assert current["diagnosis_session_id"] is None
    assert diagnosis_orchestrator.store.count_sessions() == 0


def test_legacy_read_does_not_advance_historical_session(client: TestClient, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS", "1")
    created = client.post("/api/v1/diagnoses", json=_diagnosis_payload())
    assert created.status_code == 200
    diagnosis_id = created.json()["data"]["diagnosis_id"]
    before = diagnosis_orchestrator.store.get_session(diagnosis_id)["status"]

    monkeypatch.delenv("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS", raising=False)
    response = client.get(f"/api/v1/diagnoses/{diagnosis_id}")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == before


def test_autonomous_callback_reports_compatibility_disabled(monkeypatch):
    from server.app.routes.actuation import _autonomy_start_diagnosis

    monkeypatch.delenv("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS", raising=False)
    result = _autonomy_start_diagnosis({"case_id": "case-not-created"})
    assert result["outcome"] == "LEGACY_DIAGNOSIS_DISABLED"


def test_case_read_projections_do_not_consult_legacy_diagnosis(client: TestClient, monkeypatch):
    case = client.post("/api/v1/cases", json=_case_payload()).json()["data"]

    def legacy_store_must_not_be_read(*args, **kwargs):
        raise AssertionError("legacy DiagnosisSession was consulted")

    monkeypatch.setattr(diagnosis_orchestrator.store, "get_detail", legacy_store_must_not_be_read)
    proposals = client.get(f"/api/v1/cases/{case['case_id']}/proposals")
    understanding = client.get(f"/api/v1/cases/{case['case_id']}/understanding")
    assert proposals.status_code == 200
    assert proposals.json()["data"]["proposals"] == []
    assert understanding.status_code == 200
    assert understanding.json()["data"]["diagnosis_id"] is None
