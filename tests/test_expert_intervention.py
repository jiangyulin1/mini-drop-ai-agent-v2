"""Expert chat intervention and service/process focus contracts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.agent_runtime.dispatcher import reset_runtime
from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.diagnosis.case_control import parse_chat_control


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    reset_engine()
    reset_runtime()
    init_db()
    yield
    Base.metadata.drop_all(bind=__import__("server.app.database", fromlist=["_get_engine"])._get_engine())
    reset_engine()
    reset_runtime()


@pytest.fixture
def client():
    return TestClient(app)


def _case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "expert-intervention",
        "problem_description": "支付链路延迟",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "paymentservice"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_chat_parser_extracts_pid_reference():
    assert parse_chat_control("切换到 PID 123，先看进程") == {
        "kind": "FOCUS",
        "focus_kind": "PROCESS",
        "focus_ref": "123",
        "reason": "切换到 PID 123，先看进程",
        "correction": False,
    }


def test_chat_pause_is_deterministic_and_does_not_submit_runtime_turn(client):
    case = _case(client)
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={"message": "请暂停当前调查，专家需要先看证据"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "answered"
    assert "control=PAUSE" in data["decision_summary"]
    updated = repo.get_incident_case(case["case_id"], "tenant-a")
    assert updated["state"] == "PAUSED"
    assert repo.list_agent_runtime_turns(case["case_id"], "tenant-a") == []
    assert any(
        event["event_type"] == "agent_control_applied"
        for event in repo.list_case_events(case["case_id"], "tenant-a")
    )


def test_chat_focus_updates_scope_and_summary(client):
    case = _case(client)
    before = int(case["scope_revision"])
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={"message": "改查 paymentservice，先从服务关系开始"},
    )
    assert response.status_code == 200, response.text
    updated = repo.get_incident_case(case["case_id"], "tenant-a")
    assert int(updated["scope_revision"]) > before
    assert updated["target_scope"]["active_focus"]["focus_kind"] == "SERVICE"
    summary = client.get(f"/api/v1/cases/{case['case_id']}/investigation-summary")
    assert summary.status_code == 200, summary.text
    data = summary.json()["data"]
    assert data["focus"]["focus_ref"] == "paymentservice"
    assert data["dependency_semantics"] == "dependency_only_not_causal"


def test_process_focus_requires_discovery_evidence(client):
    case = _case(client)
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/focus",
        json={
            "focus_kind": "PROCESS",
            "focus_ref": "process:agent:boot:123:456",
            "reason": "专家指定 PID",
            "expected_scope_revision": case["scope_revision"],
            "expected_control_revision": case["control_revision"],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "FOCUS_TARGET_NOT_AUTHORIZED_BY_DISCOVERY"


def test_focus_endpoint_service_uses_revision_cas(client):
    case = _case(client)
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/focus",
        json={
            "focus_kind": "SERVICE",
            "focus_ref": "paymentservice",
            "reason": "专家切换到支付服务",
            "expected_scope_revision": case["scope_revision"],
            "expected_control_revision": case["control_revision"],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["focus"]["focus_kind"] == "SERVICE"
    stale = client.post(
        f"/api/v1/cases/{case['case_id']}/focus",
        json={
            "focus_kind": "SERVICE",
            "focus_ref": "paymentservice",
            "expected_scope_revision": case["scope_revision"],
        },
    )
    assert stale.status_code == 409, stale.text
