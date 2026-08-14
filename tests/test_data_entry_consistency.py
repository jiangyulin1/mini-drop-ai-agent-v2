"""E0/E1: the two AI entry points must converge on one evidence inventory.

The plan (docs/ai_agent_runtime_integration_plan.md, G-01 / AC-02 / AC-03)
requires that every data source — first-page single Task handoff, batch
Collection update, and conversation `@` reference — be provably consumed by the
next diagnosis.  This file pins the two working entry points and documents the
known batch-field disconnect as a failing test that E1 must fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus


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


def _completed_task(collector_type: str = "sys_metrics", *, pid: int = 1234) -> str:
    """Create a DONE task with a structured sys_metrics artifact."""
    repo.register_agent("a1", "host-1", "10.0.0.1", capabilities=[collector_type, "perf_cpu"])
    task = repo.create_task(CreateTaskRequest(
        name="entry-point-consistency",
        agent_id="a1",
        target_pid=pid,
        collector_type=collector_type,
        duration_sec=15,
    ))
    repo.transition_task(task.id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    repo.add_artifacts(task.id, [{
        "artifact_type": collector_type,
        "object_key": f"tasks/{task.id}/{collector_type}.json",
        "metadata": {"data": {
            "sample_count": 10,
            "summary": {
                "avg_cpu_user_pct": 92.0, "avg_cpu_sys_pct": 5.0, "avg_cpu_iowait_pct": 1.0,
                "load1m": 8.0, "thread_count": 20, "fd_count": 20, "vmrss_mb": 200,
            },
        }},
    }])
    repo.transition_task(task.id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)
    return task.id


def _case_payload(*, initial_tasks: list[str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "title": "batch-entry-consistency",
        "problem_description": "支付接口偶尔超时，请定位",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {
            "cluster_id": "prod-a",
            "service_id": "service-a",
            "instances": [{
                "service_id": "service-a", "instance_id": "service-a-1",
                "host_id": "host-1", "agent_id": "a1", "pid": 1234,
                "environment": "production",
            }],
        },
        "time_range": {
            "start": (now - timedelta(minutes=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
            "source": "user_expression",
        },
    }
    if initial_tasks:
        payload["initial_tasks"] = initial_tasks
    return payload


def test_entry_point_data_driven_initial_tasks_are_consumed(client: TestClient):
    """入口 A（数据驱动，第一页交给 AI）：initial_tasks 必须被诊断消费。"""
    task_id = _completed_task()
    created = client.post("/api/v1/cases", json=_case_payload(initial_tasks=[task_id]))
    assert created.status_code == 200, created.text
    case = created.json()["data"]
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/diagnoses",
        json={"budget_profile": "development"},
    )
    assert resp.status_code == 200, resp.text
    diagnosis = resp.json()["data"]["diagnosis"]
    assert task_id in (diagnosis.get("child_task_ids") or [])
    assert task_id in (diagnosis.get("initial_evidence_loaded") or [])


def test_entry_point_problem_driven_still_runs_without_data(client: TestClient):
    """入口 B（问题驱动，无已有数据）：模糊问题可进入调查并给出阶段结论。"""
    # 只注册 Agent（让探针可选），不提供任何已有 Task 数据——这才是真正的问题驱动。
    repo.register_agent("a1", "host-1", "10.0.0.1", capabilities=["sys_metrics", "perf_cpu"])
    created = client.post("/api/v1/cases", json=_case_payload())
    assert created.status_code == 200, created.text
    case = created.json()["data"]
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/diagnoses",
        json={"budget_profile": "development"},
    )
    assert resp.status_code == 200, resp.text
    diagnosis = resp.json()["data"]["diagnosis"]
    # 无已有数据时应主动提出补采探针（COLLECTING 等待采集），而不是编造结论。
    assert diagnosis["status"] in {"COLLECTING", "COMPLETED"}
    assert diagnosis["probes"]
    assert diagnosis["initial_evidence_loaded"] == []


def test_batch_evidence_task_ids_are_consumed_by_diagnosis(client: TestClient):
    """入口 A 的批次更新路径：correction 写入 evidence_task_ids 后必须被消费。

    曾是 G-01 断链失败测试；E1 统一为 ResourceRef + EvidenceAttachment 后转绿。
    """
    task_id = _completed_task()
    created = client.post("/api/v1/cases", json=_case_payload())
    assert created.status_code == 200, created.text
    case = created.json()["data"]

    corrected = client.post(
        f"/api/v1/cases/{case['case_id']}/corrections",
        json={
            "target_scope": {
                **case["target_scope"],
                "evidence_task_ids": [task_id],
            },
            "reason": "关联采集批次",
            "expected_row_version": case["row_version"],
        },
    )
    assert corrected.status_code == 200, corrected.text

    # E1：旧字段不再落入 target_scope，应被投影为 Attachment
    updated_case = corrected.json()["data"]
    assert "evidence_task_ids" not in (updated_case["target_scope"] or {})

    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/diagnoses",
        json={"budget_profile": "development"},
    )
    assert resp.status_code == 200, resp.text
    diagnosis = resp.json()["data"]["diagnosis"]
    assert task_id in (diagnosis.get("child_task_ids") or []), (
        "批次 Task 未被诊断消费：UI 已关联但诊断未加载（G-01 应已修复）"
    )
    assert task_id in (diagnosis.get("initial_evidence_loaded") or [])

    # 附件应可列出且为 ACCEPTED
    attachments = client.get(
        f"/api/v1/cases/{case['case_id']}/attachments",
    ).json()["data"]["items"]
    assert any(
        item["resource_ref"]["type"] == "task"
        and item["resource_ref"]["id"] == task_id
        and item["status"] == "ACCEPTED"
        for item in attachments
    )
