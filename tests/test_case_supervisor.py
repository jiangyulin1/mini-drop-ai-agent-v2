"""Case Supervisor 测试：租约竞争、命令队列、Stop 优先、重启恢复。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, reset_engine
from server.app.diagnosis.case_supervisor import CaseSupervisor
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


def _create_case(**overrides) -> str:
    payload = {
        "tenant_id": "tenant-a",
        "created_by": "test-user",
        "title": "checkout 延迟事故",
        "problem_description": "checkout 延迟显著升高",
        "recovery_goal": "p95 恢复",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    }
    payload.update(overrides)
    return repo.create_incident_case(payload)["case_id"]


class _FakeAgent:
    def __init__(self):
        self.steps = []

    def step(self, case_id, tenant_id):
        self.steps.append(case_id)
        return {"outcome": "DIAGNOSING", "loop": {}}


def test_lease_prevents_concurrent_advance():
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    owner = "supervisor-1"
    assert repo.acquire_case_lease(case_id, "tenant-a", owner=owner, ttl_seconds=60)
    # 同一 owner 可续期；不同 owner 被拒绝。
    assert repo.renew_case_lease(case_id, "tenant-a", owner=owner, ttl_seconds=60)
    assert not repo.acquire_case_lease(case_id, "tenant-a", owner="supervisor-2", ttl_seconds=60)
    # 释放后其他 owner 可获取。
    repo.release_case_lease(case_id, "tenant-a", owner)
    assert repo.acquire_case_lease(case_id, "tenant-a", owner="supervisor-2", ttl_seconds=60)


def test_expired_lease_can_be_reacquired():
    case_id = _create_case()
    owner = "supervisor-1"
    assert repo.acquire_case_lease(case_id, "tenant-a", owner=owner, ttl_seconds=60)
    # 手动让租约过期
    from server.app.models import CaseRuntimeLeaseModel
    from server.app.database import _get_engine
    from sqlalchemy.orm import Session
    with Session(_get_engine()) as session:
        lease = session.query(CaseRuntimeLeaseModel).first()
        lease.lease_until = datetime.now(timezone.utc) - timedelta(seconds=10)
        session.commit()
    assert repo.acquire_case_lease(case_id, "tenant-a", owner="supervisor-2", ttl_seconds=60)


def test_scan_and_advance_steps_unleased_cases():
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    outcomes = supervisor.scan_and_advance("tenant-a")
    assert any(item["case_id"] == case_id for item in outcomes)
    assert case_id in agent.steps
    # 推进后释放租约，可再次推进。
    supervisor.scan_and_advance("tenant-a")
    assert agent.steps.count(case_id) == 2


def test_pause_stops_scan():
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    repo.transition_incident_case(
        case_id, "tenant-a", actor_id="test", action="pause", reason="freeze",
    )
    supervisor.scan_and_advance("tenant-a")
    assert agent.steps == [], "PAUSED Case 不应被推进"


def test_stop_command_is_applied_and_stops_advance():
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    supervisor.enqueue_command(
        case_id, "tenant-a", command_type="stop",
        idempotency_key="stop-1", payload={"reason": "red button"},
    )
    outcomes = supervisor.scan_and_advance("tenant-a")
    result = next(item for item in outcomes if item["case_id"] == case_id)
    assert result["outcome"] == "STOPPED_BY_COMMAND"
    assert agent.steps == [], "Stop 优先，不应再推进"
    case = repo.get_incident_case(case_id, "tenant-a")
    assert case["state"] == "STOPPED"


def test_commands_are_idempotent():
    case_id = _create_case()
    supervisor = CaseSupervisor(repo, _FakeAgent(), None, lease_ttl_seconds=60)
    first = supervisor.enqueue_command(
        case_id, "tenant-a", command_type="pause",
        idempotency_key="pause-1", payload={"reason": "freeze"},
    )
    second = supervisor.enqueue_command(
        case_id, "tenant-a", command_type="pause",
        idempotency_key="pause-1", payload={"reason": "freeze"},
    )
    assert first["command_id"] == second["command_id"]
    assert len(repo.list_pending_case_commands(case_id, "tenant-a")) == 1


def test_unleased_case_skips_held_lease():
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    repo.acquire_case_lease(case_id, "tenant-a", owner="other", ttl_seconds=60)
    supervisor.scan_and_advance("tenant-a")
    assert agent.steps == [], "被他人租约持有的 Case 不应被推进"
