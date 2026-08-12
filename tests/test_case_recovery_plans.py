"""Durable Case recovery workflow: plan, preflight, approval, execute, verify, rollback."""

import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import ACTUATION_GATEWAY, app, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("MINI_DROP_QUARANTINE_ROOT", str(tmp_path / "quarantine"))
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    ACTUATION_GATEWAY._attempts.clear()
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    ACTUATION_GATEWAY._attempts.clear()
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _create_case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "Mini-Drop 缓存空间压力",
        "problem_description": "过期诊断缓存占用空间，需要安全清理",
        "recovery_goal": "过期缓存进入隔离区且可以恢复",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "mini-drop-control-plane"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _seed_expired_cache(task_id: str = "task_20260701_000000_recovery1") -> Path:
    task_dir = Path(os.environ["MINI_DROP_ARTIFACT_ROOT"]) / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "artifact.json").write_text("{}", encoding="utf-8")
    old = time.time() - 10 * 86400
    os.utime(task_dir, (old, old))
    return task_dir


def _create_plan(client: TestClient, case: dict) -> dict:
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans",
        json={
            "action_id": "mini-drop.cleanup-expired-cache",
            "parameters": {"retention_days": 7},
            "value_after_fix": "释放过期诊断缓存占用，同时保留可恢复副本",
            "verification_method": "确认源目录消失且隔离区目录存在",
            "expected_case_version": case["row_version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _prepare_and_approve(client: TestClient, case: dict, plan: dict) -> dict:
    dry = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/dry-run",
        json={"expected_plan_version": plan["row_version"]},
    )
    assert dry.status_code == 200, dry.text
    plan = dry.json()["data"]
    assert plan["status"] == "DRY_RUN_COMPLETED"
    approved = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/decision",
        json={
            "decision": "approve",
            "reason": "已核对影响清单和可回滚路径",
            "expected_plan_version": plan["row_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    return approved.json()["data"]


def test_case_recovery_plan_full_verified_flow(client: TestClient):
    source = _seed_expired_cache()
    case = _create_case(client)
    plan = _prepare_and_approve(client, case, _create_plan(client, case))

    executed = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/execute",
        json={"expected_plan_version": plan["row_version"]},
    )
    assert executed.status_code == 200, executed.text
    plan = executed.json()["data"]
    assert plan["status"] == "EXECUTED"
    assert not source.exists()

    verified = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/verify",
        json={"expected_plan_version": plan["row_version"]},
    )
    assert verified.status_code == 200, verified.text
    result = verified.json()["data"]
    assert result["judgment"]["status"] == "recovered"
    assert result["recovery_plan"]["status"] == "VERIFIED"

    current_case = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert current_case["state"] == "VERIFYING"
    assert current_case["summary"]["recovery"]["status"] == "verified"
    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    event_types = [item["event_type"] for item in events]
    assert event_types[-5:] == [
        "recovery_plan_dry_run_completed",
        "recovery_plan_approved",
        "recovery_plan_executing",
        "recovery_plan_executed",
        "recovery_plan_verified",
    ]


def test_recovery_execute_rehydrates_persisted_dry_run(client: TestClient):
    _seed_expired_cache()
    case = _create_case(client)
    plan = _prepare_and_approve(client, case, _create_plan(client, case))
    ACTUATION_GATEWAY._attempts.clear()  # simulate server process restart
    executed = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/execute",
        json={"expected_plan_version": plan["row_version"]},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["data"]["status"] == "EXECUTED"


def test_recovery_execute_reconciles_crash_after_side_effect(client: TestClient):
    source = _seed_expired_cache()
    case = _create_case(client)
    plan = _prepare_and_approve(client, case, _create_plan(client, case))
    executing = repo.transition_case_recovery_plan(
        case["case_id"], "tenant-a", plan["recovery_plan_id"],
        to_status="EXECUTING",
        actor_id="local-development",
        expected_plan_version=plan["row_version"],
    )
    ACTUATION_GATEWAY.execute(
        plan["action_id"], plan["dry_run_attempt_id"], environment="production",
    )
    assert not source.exists()
    ACTUATION_GATEWAY._attempts.clear()  # process died before EXECUTED was persisted

    resumed = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/execute",
        json={"expected_plan_version": executing["row_version"]},
    )
    assert resumed.status_code == 200, resumed.text
    recovered = resumed.json()["data"]
    assert recovered["status"] == "EXECUTED"
    assert recovered["execution"]["reconciled_from_postconditions"] is True
    assert len(recovered["execution"]["executed"]) == 1


def test_recovery_plan_cannot_execute_without_approval(client: TestClient):
    _seed_expired_cache()
    case = _create_case(client)
    plan = _create_plan(client, case)
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/execute",
        json={"expected_plan_version": plan["row_version"]},
    )
    assert response.status_code == 409
    assert "NOT_APPROVED" in response.json()["detail"]


def test_policy_only_action_cannot_become_case_recovery_plan(client: TestClient):
    case = _create_case(client)
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans",
        json={
            "action_id": "service.restart-single-stateless-instance",
            "parameters": {},
            "value_after_fix": "恢复单实例服务能力",
            "verification_method": "验证健康检查和流量指标",
            "expected_case_version": case["row_version"],
        },
    )
    assert response.status_code == 409
    assert "POLICY_ONLY" in response.json()["detail"]


def test_failed_postcondition_does_not_claim_successful_rollback(client: TestClient):
    _seed_expired_cache()
    case = _create_case(client)
    plan = _prepare_and_approve(client, case, _create_plan(client, case))
    plan = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/execute",
        json={"expected_plan_version": plan["row_version"]},
    ).json()["data"]
    shutil.rmtree(Path(os.environ["MINI_DROP_QUARANTINE_ROOT"]))
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans/{plan['recovery_plan_id']}/verify",
        json={"expected_plan_version": plan["row_version"]},
    )
    assert response.status_code == 409
    assert "ROLLBACK_TARGET_MISSING" in response.json()["detail"]
    stored = client.get(
        f"/api/v1/cases/{case['case_id']}/recovery-plans",
    ).json()["data"]["items"][0]
    assert stored["status"] == "VERIFICATION_FAILED"
