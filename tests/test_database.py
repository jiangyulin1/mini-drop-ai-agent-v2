"""Database singleton initialization regression tests."""

import threading

from server.app.database import new_session, reset_engine


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
