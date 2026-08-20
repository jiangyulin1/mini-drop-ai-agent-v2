"""Legacy RCA stays offline and is not exposed to the AI runtime."""

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
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    reset_engine()
    # 模块级 repo 有 2s TTL 缓存，跨 reset_engine 会泄漏上个测试的 Task
    repo._cache.clear()
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


def test_legacy_rca_tool_has_no_route_without_internal_token(client: TestClient):
    resp = client.post("/internal/agent/tools/rca-analysis", json={})
    assert resp.status_code == 404


def test_legacy_rca_tool_has_no_route_with_internal_token(client: TestClient):
    resp = client.post("/internal/agent/tools/rca-analysis", json={
        "task_metadata": {"collector_type": "sys_metrics", "status": "DONE"},
        "top_functions": [{"function": "checkout_payment", "cpu_percent": 90.0}],
        "sys_metrics": {"cpu_percent": 85.0},
    }, headers=_headers())
    assert resp.status_code == 404
    tasks = client.get("/api/tasks").json()["data"]["items"]
    assert tasks == []


def test_legacy_rca_tool_is_not_exposed_and_creates_no_task(client: TestClient):
    before = client.get("/api/tasks").json()["data"]["items"]
    resp = client.post("/internal/agent/tools/rca-analysis", json={
        "task_metadata": {"collector_type": "perf_cpu", "status": "DONE"},
        "top_functions": [{"function": "process_loop", "cpu_percent": 99.0}],
    }, headers=_headers())
    assert resp.status_code == 404
    after = client.get("/api/tasks").json()["data"]["items"]
    assert len(after) == len(before) == 0
