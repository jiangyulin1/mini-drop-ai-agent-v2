"""E1: ResourceRef + EvidenceAttachment unified data entry tests."""

from __future__ import annotations

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


def _completed_task(name: str = "attach-task") -> str:
    repo.register_agent("a1", "host-1", "10.0.0.1", capabilities=["sys_metrics"])
    task = repo.create_task(CreateTaskRequest(
        name=name, agent_id="a1", target_pid=1234,
        collector_type="sys_metrics", duration_sec=15,
    ))
    repo.transition_task(task.id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "object_key": f"tasks/{task.id}/sys_metrics.json",
        "metadata": {"data": {"sample_count": 1, "summary": {}}},
    }])
    repo.transition_task(task.id, TaskStatus.DONE, "done", Actor.ANALYZER)
    return task.id


def _case_payload() -> dict:
    return {
        "title": "attachment-entry",
        "problem_description": "支付接口超时，请定位",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {
            "service_id": "service-a",
            "instances": [{
                "service_id": "service-a", "instance_id": "service-a-1",
                "host_id": "host-1", "agent_id": "a1", "pid": 1234,
                "environment": "production",
            }],
        },
    }


def _create_case(client: TestClient) -> str:
    created = client.post("/api/v1/cases", json=_case_payload())
    assert created.status_code == 200, created.text
    return created.json()["data"]["case_id"]


def test_reference_search_finds_task_and_agent(client: TestClient):
    task_id = _completed_task("payment-cpu-collect")
    candidates = client.post(
        "/api/v1/references/search",
        json={"query": "payment", "type": "task"},
    ).json()["data"]["items"]
    assert any(item["id"] == task_id for item in candidates)


def test_attach_completed_task_is_accepted(client: TestClient):
    task_id = _completed_task()
    case_id = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case_id}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"]["items"][0]
    assert result["result"] == "ACCEPTED"

    attachments = client.get(f"/api/v1/cases/{case_id}/attachments").json()["data"]["items"]
    assert attachments[0]["status"] == "ACCEPTED"
    assert attachments[0]["resource_ref"] == {"type": "task", "id": task_id, "revision": None}


def test_attach_unknown_task_is_rejected_with_reason(client: TestClient):
    case_id = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case_id}/attachments",
        json={"references": [{"type": "task", "id": "task-missing"}]},
    )
    assert resp.status_code == 200
    result = resp.json()["data"]["items"][0]
    assert result["result"] == "REJECTED"
    assert result["rejection_reason"] == "TASK_NOT_FOUND"


def test_duplicate_attach_returns_duplicate_skipped(client: TestClient):
    task_id = _completed_task()
    case_id = _create_case(client)
    payload = {"references": [{"type": "task", "id": task_id}]}
    first = client.post(f"/api/v1/cases/{case_id}/attachments", json=payload)
    second = client.post(f"/api/v1/cases/{case_id}/attachments", json=payload)
    assert first.json()["data"]["items"][0]["result"] == "ACCEPTED"
    assert second.json()["data"]["items"][0]["result"] == "DUPLICATE_SKIPPED"


def test_exclude_attachment_removes_it_from_diagnosis_input(client: TestClient):
    task_id = _completed_task()
    case_id = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case_id}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    attachment_id = attached["attachment_id"]

    excluded = client.post(
        f"/api/v1/cases/{case_id}/attachments/{attachment_id}/exclude",
        json={"reason": "该数据来自压测环境"},
    )
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["data"]["status"] == "EXCLUDED_BY_USER"

    resp = client.post(
        f"/api/v1/cases/{case_id}/diagnoses",
        json={"budget_profile": "development"},
    )
    assert resp.status_code == 200, resp.text
    diagnosis = resp.json()["data"]["diagnosis"]
    # EXCLUDED 附件不得进入诊断输入
    assert task_id not in (diagnosis.get("initial_evidence_loaded") or [])


def test_attach_collection_expands_to_member_tasks(client: TestClient):
    task_a = _completed_task("collection-a")
    task_b = _completed_task("collection-b")
    case_id = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case_id}/attachments",
        json={"references": [{
            "type": "collection",
            "id": "col-1",
            "member_task_ids": [task_a, task_b],
        }]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"][0]["result"] == "ACCEPTED"

    resp = client.post(
        f"/api/v1/cases/{case_id}/diagnoses",
        json={"budget_profile": "development"},
    )
    assert resp.status_code == 200, resp.text
    diagnosis = resp.json()["data"]["diagnosis"]
    loaded = diagnosis.get("initial_evidence_loaded") or []
    assert task_a in loaded and task_b in loaded
