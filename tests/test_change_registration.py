"""变更登记（C 方案）：登记发布/配置变更，并关联进入 Case 上下文供 AI 前后对比。"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.case_collaboration import build_case_context_packet, build_case_diagnosis_query
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


def _change_payload() -> dict:
    return {
        "service_id": "order-svc",
        "environment": "production",
        "change_type": "release",
        "title": "order-svc v1.2 发布",
        "description": "改进了结账链路，新增库存预扣逻辑",
        "changed_at": "2026-08-06T14:00:00Z",
    }


def test_change_registration_create_and_list(client: TestClient):
    created = client.post("/api/v1/changes", json=_change_payload())
    assert created.status_code == 200, created.text
    change = created.json()["data"]
    assert change["change_id"].startswith("chg_")
    assert change["service_id"] == "order-svc"
    assert change["change_type"] == "release"

    listed = client.get("/api/v1/changes?service_id=order-svc").json()["data"]["items"]
    assert len(listed) == 1
    assert listed[0]["change_id"] == change["change_id"]

    filtered = client.get("/api/v1/changes?service_id=other-svc").json()["data"]["items"]
    assert filtered == []


def test_change_registration_invalid_type_rejected(client: TestClient):
    payload = _change_payload()
    payload["change_type"] = "unknown-type"
    created = client.post("/api/v1/changes", json=payload)
    assert created.status_code == 422


def test_context_packet_includes_recent_changes():
    case = {
        "case_id": "case_x",
        "problem_description": "发布后延迟升高",
        "recovery_goal": "延迟回落",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "order-svc"},
        "time_range": {},
        "scope_revision": 1,
    }
    changes = [{
        "change_id": "chg_1",
        "service_id": "order-svc",
        "change_type": "release",
        "title": "order-svc v1.2 发布",
        "description": "新增库存预扣逻辑",
        "changed_at": "2026-08-06T14:00:00Z",
    }]
    packet, _, _ = build_case_context_packet(case, recent_changes=changes)
    assert packet["recent_changes"] == [{
        "change_id": "chg_1",
        "service_id": "order-svc",
        "change_type": "release",
        "title": "order-svc v1.2 发布",
        "description": "新增库存预扣逻辑",
        "changed_at": "2026-08-06T14:00:00Z",
    }]

    query = build_case_diagnosis_query(case, recent_changes=changes)
    assert "用户登记的近期变更" in query
    assert "order-svc v1.2 发布" in query
    assert "只作为待验证相关性" in query
