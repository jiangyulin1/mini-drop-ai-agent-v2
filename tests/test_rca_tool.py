"""E9: rca 候选归因分析器作为只读 Tool 暴露，不产生 Task、不捏造证据。"""

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


def test_rca_tool_requires_internal_token(client: TestClient):
    resp = client.post("/internal/agent/tools/rca-analysis", json={})
    assert resp.status_code in {401, 403}


def test_rca_tool_returns_structured_candidates(client: TestClient):
    resp = client.post("/internal/agent/tools/rca-analysis", json={
        "task_metadata": {"collector_type": "sys_metrics", "status": "DONE"},
        "top_functions": [{"function": "checkout_payment", "cpu_percent": 90.0}],
        "sys_metrics": {"cpu_percent": 85.0},
    }, headers=_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "candidates" in data
    assert isinstance(data["candidates"], list)
    # 候选携带证据引用与缺失证据，但绝不新增任务
    for candidate in data["candidates"]:
        assert "candidate_id" in candidate
        assert "evidence_refs" in candidate
        assert "missing_evidence" in candidate
    tasks = client.get("/api/tasks").json()["data"]["items"]
    assert tasks == []


def test_rca_tool_is_read_only_no_task_created(client: TestClient):
    before = client.get("/api/tasks").json()["data"]["items"]
    resp = client.post("/internal/agent/tools/rca-analysis", json={
        "task_metadata": {"collector_type": "perf_cpu", "status": "DONE"},
        "top_functions": [{"function": "process_loop", "cpu_percent": 99.0}],
    }, headers=_headers())
    assert resp.status_code == 200
    after = client.get("/api/tasks").json()["data"]["items"]
    assert len(after) == len(before) == 0
