"""G3: Task artifacts are materialized as canonical Case Evidence."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus

TOKEN = "test-internal-token"


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
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


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "case-evidence-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _done_task_with_artifact() -> str:
    repo.register_agent(
        "agent-ev", "node-ev", "192.168.40.10", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    task = repo.create_task(CreateTaskRequest(
        name="evidence-task",
        agent_id="agent-ev",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=11,
        duration_sec=15,
        options={"source": "manual"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "metadata": {"samples": 100, "window_sec": 15, "cpu_percent": 80},
    }])
    repo.transition_task(task.id, TaskStatus.RUNNING, "start", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "upload", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyze", Actor.WEB)
    repo.transition_task(task.id, TaskStatus.DONE, "done", Actor.AGENT)
    return task.id


def test_attachment_materializes_task_artifacts_as_case_evidence(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}], "purpose": "已有采集"},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"]["items"][0]
    assert item["result"] == "ACCEPTED"
    assert item["evidence_ids"], "task artifacts must produce evidence IDs"

    evidence = repo.list_case_evidence(case["case_id"], "tenant-a")
    assert len(evidence) == 1
    assert evidence[0]["task_id"] == task_id
    assert evidence[0]["artifact_type"] == "sys_metrics"
    assert evidence[0]["projection_hash"]
    assert evidence[0]["source_id"] == "sys_metrics"
    assert evidence[0]["schema_version"] == "1"
    assert evidence[0]["completeness"] == "COMPLETE"
    assert evidence[0]["trust_level"] == "INTERNAL"
    assert evidence[0]["sha256"]
    assert evidence[0]["lineage"]["task_id"] == task_id
    attachment = repo.list_case_attachments(case["case_id"], "tenant-a")[0]
    assert attachment["evidence_ids"] == item["evidence_ids"]
    listed = client.get(f"/api/v1/cases/{case['case_id']}/evidence")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["evidence_id"] == item["evidence_ids"][0]


def test_finish_accepts_canonical_evidence_and_persists_conclusion(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_ids = attached["evidence_ids"]
    resp = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "根因是 CPU 饱和", "evidence_ids": evidence_ids},
        headers={"X-Internal-Token": TOKEN},
    )
    assert resp.status_code == 200, resp.text
    updated = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert updated["summary"]["current_finding"]["status"] == "concluded"
    assert updated["summary"]["current_finding"]["evidence_refs"] == evidence_ids


def test_excluded_case_evidence_is_not_consumed_by_finish(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    repo.exclude_case_evidence(case["case_id"], "tenant-a", evidence_id)
    resp = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "x", "evidence_ids": [evidence_id]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("INVALID_EVIDENCE_REFS")


def test_evidence_review_excluded_updates_canonical_store(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "EXCLUDED", "reason": "outlier"},
    )
    assert review.status_code == 200, review.text
    stored = repo.get_case_evidence(case["case_id"], "tenant-a", evidence_id)
    assert stored["status"] == "EXCLUDED"
    resp = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "x", "evidence_ids": [evidence_id]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert resp.status_code == 400


def test_excluding_supporting_evidence_appends_downgraded_conclusion(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    finished = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "CPU 证据支持当前结论",
            "evidence_ids": [evidence_id],
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert finished.status_code == 200, finished.text
    original = repo.get_conclusion(case["case_id"], "tenant-a")
    assert original["revision"] == 1

    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "EXCLUDED", "reason": "outlier"},
    )
    assert review.status_code == 200, review.text
    downgraded = repo.get_conclusion(case["case_id"], "tenant-a")
    assert downgraded["revision"] == 2
    assert downgraded["state"] == "INSUFFICIENT_EVIDENCE"
    assert downgraded["verifier_version"] == "causal-report-verifier.v2-revalidation"
    assert downgraded["claim_evidence_bindings"][0]["verifier_result"] == "EVIDENCE_EXCLUDED"
