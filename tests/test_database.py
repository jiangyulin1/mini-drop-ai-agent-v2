"""Database singleton initialization regression tests."""

import threading

from sqlalchemy import inspect, text

from server.app.database import _get_engine, init_db, new_session, reset_engine


def test_fresh_session_initialization_does_not_deadlock(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    reset_engine()
    result = []

    def create_session():
        session = new_session()
        session.close()
        result.append("created")

    thread = threading.Thread(target=create_session, daemon=True)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result == ["created"]
    reset_engine()


def test_init_db_adds_v2_columns_to_legacy_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'legacy.db'}")
    reset_engine()
    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE tasks (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE diagnosis_sessions (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE diagnosis_probe_executions (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE diagnosis_evidence (id VARCHAR(128) PRIMARY KEY)"))

    init_db()
    inspector = inspect(engine)
    assert "diagnosis_step_id" in {item["name"] for item in inspector.get_columns("tasks")}
    assert "traceparent" in {item["name"] for item in inspector.get_columns("tasks")}
    assert {"row_version", "deadline_at", "paused_from_status"}.issubset(
        item["name"] for item in inspector.get_columns("diagnosis_sessions")
    )
    assert {"retry_count", "error_code", "error_message"}.issubset(
        item["name"] for item in inspector.get_columns("diagnosis_probe_executions")
    )
    assert "evidence_role" in {
        item["name"] for item in inspector.get_columns("diagnosis_evidence")
    }
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0025_evidence_contract"
        )
    reset_engine()


def test_init_db_creates_fresh_schema_at_head(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'new.db'}")
    reset_engine()

    init_db()

    engine = _get_engine()
    inspector = inspect(engine)
    assert {
        "agents", "tasks", "artifacts", "diagnosis_sessions", "authorization_grants",
        "incident_cases", "case_events",
        "case_context_packets", "case_model_attempts",
        "case_hypothesis_nodes", "case_hypothesis_edges", "case_investigation_iterations",
        "case_recovery_plans", "diagnostic_target_sessions", "target_signals",
        "profile_windows",
        "agent_runtime_bindings", "agent_runtime_turns", "agent_runtime_events", "case_evidence",
        "domain_outbox", "outbox_consumer_effects",
    }.issubset(
        inspector.get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0025_evidence_contract"
        )
    reset_engine()


def test_migration_0018_backfill_handles_real_case_data(monkeypatch, tmp_path):
    """E1/E8 回归：0018 回填 initial_task_ids 不得用 TextClause 当绑定参数。

    真实 VM 数据库（schema 0017 + 带 initial_task_ids 的 Case）升级时曾触发
    ``Error binding parameter 16: type 'TextClause' is not supported``。
    """
    from datetime import datetime, timezone

    from alembic import command
    from sqlalchemy import create_engine

    from server.app.migration import _config

    engine = create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    config = _config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0017_system_controls")

        now = datetime.now(timezone.utc).isoformat()
        connection.execute(text(
            "INSERT INTO incident_cases (id, tenant_id, created_by, title, "
            "problem_description, recovery_goal, run_mode, environment, state, "
            "state_reason, scope_revision, row_version, created_at, updated_at, "
            "initial_task_ids, source_task_id) VALUES "
            "(:id, :t, :by, :title, :pd, :rg, 'COLLABORATE', 'production', "
            "'ACTIVE', '', 1, 0, :now, :now, :tasks, :src)"
        ), {
            "id": "case-backfill-1", "t": "tenant-a", "by": "user-1",
            "title": "迁移回填测试", "pd": "支付超时", "rg": "定位根因",
            "now": now, "tasks": '["task-1", "task-2"]', "src": "task-0",
        })

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT resource_type, resource_id FROM case_resource_attachments "
            "WHERE case_id='case-backfill-1'"
        )).fetchall()
        resources = sorted((row[0], row[1]) for row in rows)
    assert ("task", "task-1") in resources
    assert ("task", "task-2") in resources
    assert ("task", "task-0") in resources
    engine.dispose()
    reset_engine()
