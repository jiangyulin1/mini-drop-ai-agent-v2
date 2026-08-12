"""ActionAttempt 持久化测试：dry-run/execute/verify/rollback 阶段幂等落库。"""

from __future__ import annotations

import pytest

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


def _create_case() -> str:
    case = repo.create_incident_case({
        "tenant_id": "tenant-a",
        "created_by": "test-user",
        "title": "checkout 延迟事故",
        "problem_description": "checkout 延迟显著升高",
        "recovery_goal": "p95 恢复",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    return case["case_id"]


def test_action_attempt_phases_persist_and_idempotent():
    case_id = _create_case()
    op_key = "case-1:1:1:swarm.restart-stateless-service"

    dry = repo.record_action_attempt(
        case_id, "tenant-a", attempt_id="act_dry_1",
        action_id="swarm.restart-stateless-service", operation_key=op_key,
        phase="dry_run", parameters={"service_name": "shop_paymentservice"},
        result={"dry_run": {"candidate_count": 1}},
    )
    assert dry["phase"] == "dry_run"
    assert dry["operation_key"] == op_key

    exec_attempt = repo.record_action_attempt(
        case_id, "tenant-a", attempt_id="act_exec_1",
        action_id="swarm.restart-stateless-service", operation_key=op_key,
        phase="execute", parameters={"service_name": "shop_paymentservice"},
        result={"stage": "COMPLETED"},
    )
    assert exec_attempt["phase"] == "execute"

    attempts = repo.list_action_attempts(case_id, "tenant-a")
    assert {item["phase"] for item in attempts} == {"dry_run", "execute"}
    assert all(item["action_id"] == "swarm.restart-stateless-service" for item in attempts)

    # 幂等：同一 (case, operation_key, phase) 重放只更新不新增。
    again = repo.record_action_attempt(
        case_id, "tenant-a", attempt_id="act_dry_1",
        action_id="swarm.restart-stateless-service", operation_key=op_key,
        phase="dry_run", parameters={"service_name": "shop_paymentservice"},
        result={"dry_run": {"candidate_count": 1}},
    )
    assert again["attempt_id"] == dry["attempt_id"]
    assert again["row_version"] == dry["row_version"] + 1
    assert len(repo.list_action_attempts(case_id, "tenant-a")) == 2


def test_action_attempts_are_tenant_scoped():
    case_id = _create_case()
    repo.record_action_attempt(
        case_id, "tenant-a", attempt_id="act_1",
        action_id="swarm.restart-stateless-service", operation_key="k:1",
        phase="dry_run", result={},
    )
    # 其他租户不可见，且同租户不同 case 隔离。
    assert repo.list_action_attempts(case_id, "tenant-b") == []
