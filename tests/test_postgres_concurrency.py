"""Real PostgreSQL concurrency gates; opt in with MINI_DROP_TEST_POSTGRES_URL."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect, text

from server.app.database import init_db, new_session, reset_engine
from server.app.jobs.outbox_relay import IdempotentOutboxConsumer, OutboxRelay
from server.app.models import (
    CaseRuntimeLeaseModel,
    DomainOutboxModel,
    OutboxConsumerEffectModel,
)
from server.app.persistence.fencing import (
    CaseLeaseFence,
    LeaseFenceViolation,
    case_lease_fence,
)
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc


POSTGRES_URL = os.getenv("MINI_DROP_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL gate not configured")


@pytest.fixture(scope="module", autouse=True)
def postgres_schema():
    engine = create_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    os.environ["DATABASE_URL"] = POSTGRES_URL
    reset_engine()
    init_db()
    yield
    reset_engine()


def test_concurrent_idempotent_task_create_has_one_effect():
    seed = SqlRepository()
    seed.register_agent("pg-agent", "pg-node", "127.0.0.1")
    payload = CreateTaskRequest(
        name="postgres-idempotency",
        agent_id="pg-agent",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=1,
        duration_sec=1,
    )
    barrier = Barrier(2)

    def create(repository: SqlRepository) -> str:
        barrier.wait(timeout=5)
        return repository.create_task(payload, idempotency_key="pg-same-key").id

    with ThreadPoolExecutor(max_workers=2) as pool:
        task_ids = list(pool.map(create, [SqlRepository(), SqlRepository()]))

    assert len(set(task_ids)) == 1
    assert len([event for event in seed.events if event.task_id == task_ids[0]]) == 1


def test_concurrent_case_lease_has_one_owner():
    seed = SqlRepository()
    case_id = seed.create_incident_case({
        "tenant_id": "tenant-pg",
        "created_by": "postgres-gate",
        "title": "lease race",
        "problem_description": "two supervisors race",
        "recovery_goal": "single owner",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "environment": "test",
        "target_scope": {"service_id": "checkout"},
    })["case_id"]
    barrier = Barrier(2)

    def acquire(args) -> tuple[str, bool]:
        owner, repository = args
        barrier.wait(timeout=5)
        return owner, repository.acquire_case_lease(
            case_id,
            "tenant-pg",
            owner=owner,
            ttl_seconds=60,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(acquire, [
            ("owner-a", SqlRepository()),
            ("owner-b", SqlRepository()),
        ]))

    assert sum(acquired for _, acquired in outcomes) == 1


def test_0024_postgres_schema_contract_is_explicit():
    engine = create_engine(POSTGRES_URL)
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns("domain_outbox")}
    assert {
        "aggregate_revision",
        "payload_schema_version",
        "claimed_at",
        "max_attempts",
        "delivered_at",
        "dead_at",
    } <= columns
    assert "outbox_consumer_effects" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("domain_outbox")}
    assert "ix_domain_outbox_claim_expiry" in indexes
    engine.dispose()


def test_two_postgres_relays_deliver_each_effect_once():
    with new_session() as session:
        session.query(OutboxConsumerEffectModel).delete()
        session.query(DomainOutboxModel).delete()
        session.commit()
    seed = SqlRepository()
    for index in range(20):
        seed.enqueue_domain_outbox(
            aggregate_type="relay-gate",
            aggregate_id=f"aggregate-{index}",
            event_type="RELAY_GATE",
            payload={"index": index},
            dedupe_key=f"relay-gate-{index}",
        )

    barrier = Barrier(2)

    def run(relay_id: str):
        repository = SqlRepository()
        consumer = IdempotentOutboxConsumer(
            repository,
            consumer_name="pg-runtime-wakeup",
        )

        def publish(event):
            result = consumer.consume(
                event,
                effect_key=f"wake:{event['aggregate_id']}",
            )
            assert result["applied"] is True

        barrier.wait(timeout=5)
        return OutboxRelay(
            repository,
            publish,
            relay_id=relay_id,
        ).run_once(limit=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["relay-a", "relay-b"]))

    assert sum(item.delivered for item in results) == 20
    assert len(seed.list_domain_outbox(status="DELIVERED")) == 20
    with new_session() as session:
        assert session.query(OutboxConsumerEffectModel).count() == 20


def test_postgres_expired_lease_fences_old_owner_commit():
    repository = SqlRepository()
    case_id = repository.create_incident_case({
        "tenant_id": "tenant-fence",
        "created_by": "postgres-gate",
        "title": "late owner fence",
        "problem_description": "lease expires during work",
        "recovery_goal": "no stale commit",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "environment": "test",
        "target_scope": {"service_id": "checkout"},
    })["case_id"]
    old_token = repository.acquire_case_lease_token(
        case_id,
        "tenant-fence",
        owner="old-owner",
        ttl_seconds=60,
    )
    with new_session() as session:
        lease = session.query(CaseRuntimeLeaseModel).filter_by(case_id=case_id).one()
        lease.lease_until = now_utc() - timedelta(seconds=1)
        session.commit()
    new_token = SqlRepository().acquire_case_lease_token(
        case_id,
        "tenant-fence",
        owner="new-owner",
        ttl_seconds=60,
    )
    assert new_token and new_token > old_token

    with case_lease_fence(CaseLeaseFence(
        case_id=case_id,
        tenant_id="tenant-fence",
        owner="old-owner",
        token=old_token,
    )):
        with pytest.raises(LeaseFenceViolation, match="CASE_LEASE_FENCED"):
            repository.record_case_event(
                case_id,
                "tenant-fence",
                event_type="stale-owner-effect",
                payload={},
                actor_id="old-owner",
            )
    assert not [
        item for item in repository.list_case_events(case_id, "tenant-fence")
        if item["event_type"] == "stale-owner-effect"
    ]
