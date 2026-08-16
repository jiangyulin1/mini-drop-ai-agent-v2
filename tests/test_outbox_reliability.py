"""Transactional Outbox crash-point and recovery contracts."""

from __future__ import annotations

from datetime import timedelta

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.jobs.outbox_relay import IdempotentOutboxConsumer, OutboxRelay
from server.app.models import Base, CaseRuntimeLeaseModel, DomainOutboxModel
from server.app.persistence.fencing import (
    CaseLeaseFence,
    LeaseFenceViolation,
    case_lease_fence,
)
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import Actor, TaskStatus, now_utc


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _task(repository: SqlRepository):
    repository.register_agent("outbox-agent", "node", "127.0.0.1")
    return repository.create_task(CreateTaskRequest(
        name="outbox crash gate",
        agent_id="outbox-agent",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=1,
        duration_sec=1,
    ))


def test_uow_rolls_back_state_and_outbox_together(monkeypatch):
    repository = SqlRepository()
    task = _task(repository)
    original = repository._enqueue_domain_outbox_in_session

    def crash_before_outbox(*args, **kwargs):
        if kwargs.get("aggregate_revision") == 1:
            raise RuntimeError("crash before state+outbox commit")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "_enqueue_domain_outbox_in_session", crash_before_outbox)
    with pytest.raises(RuntimeError, match="crash before"):
        repository.transition_task(task.id, TaskStatus.RUNNING, "dispatch", Actor.SERVER)

    assert repository.tasks[task.id].status == TaskStatus.PENDING.value
    events = repository.list_domain_outbox()
    assert [(item["aggregate_revision"], item["payload"]["to_status"]) for item in events] == [
        (0, "PENDING"),
    ]


def test_committed_state_and_outbox_survive_crash_before_relay():
    repository = SqlRepository()
    task = _task(repository)
    repository.transition_task(task.id, TaskStatus.RUNNING, "dispatch", Actor.SERVER)

    restarted = SqlRepository()
    assert restarted.tasks[task.id].status == TaskStatus.RUNNING.value
    pending = restarted.list_domain_outbox(status="PENDING")
    assert {item["aggregate_revision"] for item in pending} == {0, 1}


def test_outbox_reclaim_and_idempotent_delivery():
    repository = SqlRepository()
    event = repository.enqueue_domain_outbox(
        aggregate_type="case",
        aggregate_id="case-1",
        event_type="CASE_WAKE",
        payload={"case_id": "case-1"},
        dedupe_key="crash-after-send",
        max_attempts=3,
    )
    consumer = IdempotentOutboxConsumer(repository, consumer_name="runtime-wakeup")

    class ProcessCrash(BaseException):
        pass

    def publish_then_crash(claimed):
        first = consumer.consume(claimed, effect_key="wake:case-1")
        assert first["applied"] is True
        raise ProcessCrash("relay died before delivered ACK")

    with pytest.raises(ProcessCrash, match="before delivered"):
        OutboxRelay(
            repository,
            publish_then_crash,
            relay_id="relay-crashed",
            lease_seconds=5,
        ).run_once(limit=1)

    # Downstream effect committed, while the outbox claim remains unacknowledged.
    with new_session() as session:
        row = session.get(DomainOutboxModel, event["outbox_id"])
        row.claim_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()

    repository.reclaim_expired_outbox("relay-restarted")
    with new_session() as session:
        row = session.get(DomainOutboxModel, event["outbox_id"])
        row.available_at = now_utc() - timedelta(seconds=1)
        session.commit()

    observed: list[bool] = []

    def publish(replayed):
        observed.append(
            consumer.consume(replayed, effect_key="wake:case-1")["applied"],
        )

    result = OutboxRelay(
        repository,
        publish,
        relay_id="relay-restarted",
    ).run_once(limit=1)
    assert observed == [False]
    assert result.delivered == 1
    assert repository.list_domain_outbox(status="DELIVERED")[0]["outbox_id"] == event["outbox_id"]


def test_dead_outbox_is_visible_and_operator_recoverable():
    repository = SqlRepository()
    event = repository.enqueue_domain_outbox(
        aggregate_type="case",
        aggregate_id="case-dead",
        event_type="FAIL_ALWAYS",
        dedupe_key="dead-visible",
        max_attempts=1,
    )
    result = OutboxRelay(
        repository,
        lambda _event: (_ for _ in ()).throw(RuntimeError("downstream unavailable")),
        relay_id="relay-dead",
    ).run_once(limit=1)
    assert result.dead == 1
    dead = repository.list_domain_outbox(status="DEAD")
    assert dead[0]["outbox_id"] == event["outbox_id"]
    assert "downstream unavailable" in dead[0]["last_error"]

    recovered = repository.recover_dead_outbox(event["outbox_id"])
    assert recovered["status"] == "PENDING"
    assert recovered["attempts"] == 0


def test_expired_lease_fences_old_owner_late_commit():
    repository = SqlRepository()
    case_id = repository.create_incident_case({
        "tenant_id": "tenant-a",
        "created_by": "outbox-test",
        "title": "fence case",
        "problem_description": "old owner must not commit",
        "recovery_goal": "one owner",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "environment": "test",
        "target_scope": {"service_id": "checkout"},
    })["case_id"]
    old_token = repository.acquire_case_lease_token(
        case_id,
        "tenant-a",
        owner="old-owner",
        ttl_seconds=60,
    )
    with new_session() as session:
        lease = session.query(CaseRuntimeLeaseModel).filter_by(case_id=case_id).one()
        lease.lease_until = now_utc() - timedelta(seconds=1)
        session.commit()
    new_token = SqlRepository().acquire_case_lease_token(
        case_id,
        "tenant-a",
        owner="new-owner",
        ttl_seconds=60,
    )
    assert new_token and new_token > old_token

    with case_lease_fence(CaseLeaseFence(
        case_id=case_id,
        tenant_id="tenant-a",
        owner="old-owner",
        token=old_token,
    )):
        with pytest.raises(LeaseFenceViolation, match="CASE_LEASE_FENCED"):
            repository.record_case_event(
                case_id,
                "tenant-a",
                event_type="old-owner-effect",
                payload={},
                actor_id="old-owner",
            )
    assert not [
        item for item in repository.list_case_events(case_id, "tenant-a")
        if item["event_type"] == "old-owner-effect"
    ]
