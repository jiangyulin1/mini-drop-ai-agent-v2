"""P7 韧性测试：连接器失败降级、命令队列积压、Agent 离线分区。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis.case_supervisor import CaseSupervisor
from server.app.diagnosis.data_sources import PrometheusConnector
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


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def test_connector_failure_degrades_gracefully():
    """数据源连接失败必须返回可用的降级响应，不中断诊断。"""
    def failing(url, params):
        raise OSError("partitioned")

    connector = PrometheusConnector(http_get=failing, endpoint="http://prom")
    from server.app.diagnosis.source_gateway import SourceQueryRequest
    req = SourceQueryRequest(tenant_id="t", operation="metrics.query_range",
                             resource={}, parameters={"metric": "up"}, requested_time_range_minutes=15)
    result = connector.execute(req)
    assert result["available"] is False
    assert result["samples"] == []


class _FakeAgent:
    def __init__(self):
        self.steps = 0

    def step(self, case_id, tenant_id):
        self.steps += 1
        return {"outcome": "DIAGNOSING", "loop": {}}


def _create_case() -> str:
    return repo.create_incident_case({
        "tenant_id": "tenant-a", "created_by": "test",
        "title": "t", "problem_description": "d", "recovery_goal": "r",
        "run_mode": "AUTHORIZED_AUTONOMY", "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })["case_id"]


def test_command_queue_backlog_drains_without_stall():
    """大量命令入队后按序处理，不阻塞后续 Case 推进。"""
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    for i in range(20):
        supervisor.enqueue_command(
            case_id, "tenant-a", command_type="pause",
            idempotency_key=f"pause-{i}", payload={"reason": f"burst-{i}"},
        )
    # 暂停命令会把 Case 置为 PAUSED，之后不再推进。
    supervisor.scan_and_advance("tenant-a")
    case = repo.get_incident_case(case_id, "tenant-a")
    assert case["state"] == "PAUSED"
    pending = repo.list_pending_case_commands(case_id, "tenant-a")
    assert len(pending) == 0, "命令队列应全部消费"
    # PAUSED Case 不再被推进。
    supervisor.scan_and_advance("tenant-a")
    assert agent.steps == 0


def test_offline_agent_excluded_from_scope(client):
    """Agent 离线时范围解析应排除其实例，不扩散采集。"""
    repo.register_agent("a1", "host-1", "10.0.0.1", capabilities=["sys_metrics"])
    # 模拟 a2 离线：不注册 a2，但上下文引用它。
    payload = {
        "query": "service-a 延迟升高，请定位原因",
        "context": {
            "service_id": "service-a",
            "environment": "production",
            "instances": [
                {"service_id": "service-a", "instance_id": "service-a-1",
                 "host_id": "host-1", "agent_id": "a1", "pid": 1001, "environment": "production"},
                {"service_id": "service-a", "instance_id": "service-a-2",
                 "host_id": "host-2", "agent_id": "a2", "pid": 1002, "environment": "production"},
            ],
        },
        "budget_profile": "production_safe",
    }
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    scope = data["target_scope"]
    assert scope["scope_completeness"] in {"partial", "unresolved"}
    # 未注册 agent 的实例被排除，不能成为采集目标。
    instance_ids = [i["instance_id"] for i in scope.get("instances", [])]
    assert "service-a-2" not in instance_ids
