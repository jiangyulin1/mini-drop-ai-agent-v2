"""G4: manual and AI share one Campaign API that compiles to InvestigationPlan."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app
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


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "campaign-case",
        "problem_description": "支付接口超时",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"cluster_id": "prod-a", "service_id": "checkout"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def test_campaign_compiles_common_baseline_and_heterogeneous_assignments(client: TestClient):
    case = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/campaigns",
        json={
            "goal": "异构集群调查",
            "expected_case_row_version": case["row_version"],
            "expected_scope_revision": case["scope_revision"],
            "expected_plan_revision": 0,
            "common_baseline": {
                "role": "all",
                "collector_id": "sys_metrics",
                "target_refs": ["cluster:prod-a"],
                "selection_strategy": "ALL_IN_SCOPE",
                "purpose": "共同基线",
                "priority": 80,
            },
            "assignments": [
                {
                    "role": "gateway",
                    "collector_id": "connection_probe",
                    "target_refs": ["service:gateway"],
                    "purpose": "服务连通性",
                    "priority": 90,
                },
                {
                    "role": "db",
                    "collector_id": "log_scan",
                    "target_refs": ["service:db"],
                    "purpose": "数据库日志",
                    "priority": 70,
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["plan"]["plan_revision"] == 1
    steps = data["plan"]["steps"]
    assert len(steps) == 3
    by_collector = {item["collector_id"]: item for item in steps}
    assert by_collector["sys_metrics"]["selection_strategy"] == "ALL_IN_SCOPE"
    assert {item["collector_id"] for item in steps} == {
        "sys_metrics", "connection_probe", "log_scan",
    }
    matrix = data["matrix"]
    assert matrix["present"] is True
    assert matrix["plan_revision"] == 1


def test_campaign_stale_revision_is_rejected(client: TestClient):
    case = _create_case(client)
    payload = {
        "goal": "旧矩阵",
        "expected_case_row_version": case["row_version"],
        "expected_scope_revision": case["scope_revision"],
        "expected_plan_revision": 1,
        "common_baseline": {
            "role": "all",
            "collector_id": "sys_metrics",
            "target_refs": ["cluster:prod-a"],
            "purpose": "基线",
        },
        "assignments": [{
            "role": "api",
            "collector_id": "log_scan",
            "purpose": "日志",
        }],
    }
    resp = client.post(f"/api/v1/cases/{case['case_id']}/campaigns", json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("STALE_PLAN")
