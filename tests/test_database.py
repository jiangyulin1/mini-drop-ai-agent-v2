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
            "0014_profile_windows"
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
    }.issubset(
        inspector.get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0014_profile_windows"
        )
    reset_engine()
