"""E3: internal Tool Gateway — token gate, read-only projections, STALE_PLAN."""

from __future__ import annotations

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


def test_internal_tool_requires_token(client: TestClient):
    resp = client.post("/internal/agent/tools/case-snapshot", json={"case_id": "x"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "INTERNAL_TOKEN_REQUIRED"


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


def test_internal_plan_write_requires_stale_check(client: TestClient):
    case = _create_case(client)
    # 用错误 revision=0 但当前尚无计划（revision 0）→ 成功创建 revision 1
    ok = client.post(
        "/internal/agent/tools/plan",
        json={
            "case_id": case["case_id"],
            "goal": "验证 CPU 饱和",
            "expected_case_row_version": case["row_version"],
            "expected_scope_revision": case["scope_revision"],
            "expected_plan_revision": 0,
            "steps": [{"collector_id": "sys_metrics", "purpose": "验证 CPU", "risk": "READ_LOW", "priority": 80}],
        },
        headers=_headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["plan_revision"] == 1

    # 再次用旧 revision=0 → STALE_PLAN 拒绝（模型不能静默覆盖新计划）
    stale = client.post(
        "/internal/agent/tools/plan",
        json={
            "case_id": case["case_id"],
            "goal": "旧计划重放",
            "expected_case_row_version": case["row_version"],
            "expected_scope_revision": case["scope_revision"],
            "expected_plan_revision": 0,
            "steps": [],
        },
        headers=_headers(),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("STALE_PLAN")


def test_internal_finish_requires_evidence_refs(client: TestClient):
    case = _create_case(client)
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
        },
        headers=_headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["accepted"] is True
    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert events[-1]["event_type"] == "agent_finish_investigation"
    updated = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert updated["summary"]["current_finding"]["status"] == "concluded"
    assert updated["summary"]["current_finding"]["evidence_refs"] == ["ev-valid"]
