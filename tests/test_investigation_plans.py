"""E2: persistent investigation plans, step state machine and dual-channel control."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, investigation_plan_service
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


def _create_case(client: TestClient) -> str:
    created = client.post("/api/v1/cases", json={
        "title": "plan-control-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _plan_payload(case) -> dict:
    return {
        "goal": "验证 CPU 饱和假设",
        "expected_case_row_version": case["row_version"],
        "expected_scope_revision": case["scope_revision"],
        "expected_plan_revision": 0,
        "steps": [{
            "kind": "COLLECTION",
            "collector_id": "sys_metrics",
            "target_refs": ["process:service-a/1"],
            "purpose": "验证 CPU 是否饱和",
            "hypothesis_refs": ["hyp_cpu"],
            "priority": 80,
            "risk": "READ_LOW",
            "status": "QUEUED",
        }],
    }


def test_create_plan_and_revision_bump(client: TestClient):
    case = _create_case(client)
    plan = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json=_plan_payload(case),
    )
    assert plan.status_code == 200, plan.text
    data = plan.json()["data"]
    assert data["plan_revision"] == 1
    assert len(data["steps"]) == 1
    assert data["steps"][0]["status"] == "QUEUED"

    # 第二次更新 → revision 2，旧步骤 SUPERSEDED
    case2 = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    payload2 = _plan_payload(case2)
    payload2["expected_plan_revision"] = 1
    plan2 = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json=payload2,
    )
    assert plan2.status_code == 200, plan2.text
    assert plan2.json()["data"]["plan_revision"] == 2


def test_stale_plan_revision_is_rejected(client: TestClient):
    case = _create_case(client)
    plan = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json=_plan_payload(case),
    )
    assert plan.status_code == 200

    # 用旧 revision=0 再提交 → STALE_PLAN
    stale = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json={
            "goal": "旧计划重放",
            "expected_case_row_version": case["row_version"],
            "expected_scope_revision": case["scope_revision"],
            "expected_plan_revision": 0,
            "steps": [],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("STALE_PLAN")


def test_step_state_machine_remove_cancel_reprioritize(client: TestClient):
    case = _create_case(client)
    plan = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json=_plan_payload(case),
    ).json()["data"]
    step_id = plan["steps"][0]["step_id"]

    # reprioritize → user lock
    reprioritized = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{step_id}/reprioritize",
        json={"priority": 99, "user_locked": True},
    )
    assert reprioritized.status_code == 200, reprioritized.text
    assert reprioritized.json()["data"]["priority"] == 99
    assert reprioritized.json()["data"]["priority_source"] == "USER"
    assert reprioritized.json()["data"]["user_locked"] is True

    # cancel QUEUED → CANCELLED
    cancelled = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{step_id}/cancel",
        json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "CANCELLED"

    # 取消后不能再 cancel（终态）
    again = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{step_id}/cancel",
        json={},
    )
    assert again.status_code == 200  # 幂等返回当前状态


def test_remove_step_marks_removed_by_user_and_increments_revision(client: TestClient):
    case = _create_case(client)
    plan = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json=_plan_payload(case),
    ).json()["data"]
    step_id = plan["steps"][0]["step_id"]

    removed = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{step_id}/remove",
        json={},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["data"]["status"] == "REMOVED_BY_USER"

    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert any(event["event_type"] == "step_removed" for event in events)


def test_verify_schedulable_rejects_stale_and_running_steps(client: TestClient):
    case = _create_case(client)
    plan = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json=_plan_payload(case),
    ).json()["data"]
    step_id = plan["steps"][0]["step_id"]

    # stale plan revision → STALE_PLAN
    with pytest.raises(ValueError) as exc_info:
        investigation_plan_service.verify_schedulable(
            case["case_id"], "tenant-a", step_id,
            plan_revision=0, scope_revision=case["scope_revision"],
        )
    assert "STALE_PLAN" in str(exc_info.value)

    # 正确 revision → 可调度
    step = investigation_plan_service.verify_schedulable(
        case["case_id"], "tenant-a", step_id,
        plan_revision=1, scope_revision=case["scope_revision"],
    )
    assert step["status"] in {"QUEUED", "WAITING_APPROVAL"}


def test_evidence_review_excludes_and_persists(client: TestClient):
    case = _create_case(client)
    reviewed = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/ev-102/reviews",
        json={
            "evidence_id": "ev-102",
            "decision": "LOW_TRUST",
            "reason_code": "TEST_TRAFFIC",
            "reason": "该时间段存在压测流量",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    data = reviewed.json()["data"]
    assert data["decision"] == "LOW_TRUST"
    assert data["review_revision"] == 1

    excluded = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/ev-102/reviews",
        json={
            "evidence_id": "ev-102",
            "decision": "EXCLUDED",
            "reason": "来自测试环境",
        },
    )
    assert excluded.status_code == 200
    assert excluded.json()["data"]["review_revision"] == 2

    reviews = client.get(
        f"/api/v1/cases/{case['case_id']}/evidence-reviews?evidence_id=ev-102",
    ).json()["data"]["items"]
    assert [item["decision"] for item in reviews] == ["EXCLUDED", "LOW_TRUST"]
