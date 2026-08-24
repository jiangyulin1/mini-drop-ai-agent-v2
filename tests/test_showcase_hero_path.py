"""Executable contract for the intentionally narrow presentation path."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus


TOKEN = "showcase-hero-path-token"


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


def _done_task_with_artifact() -> str:
    """Create the same durable Task/Artifact shape used by the collector path."""
    repo.register_agent(
        "agent-showcase", "node-showcase", "192.168.40.20", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    task = repo.create_task(CreateTaskRequest(
        name="showcase-sys-metrics",
        agent_id="agent-showcase",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=11,
        duration_sec=15,
        options={"source": "showcase-collector"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "metadata": {
            "samples": 100,
            "window_sec": 15,
            "cpu_percent": 96,
            "completeness": "COMPLETE",
        },
    }])
    repo.transition_task(task.id, TaskStatus.RUNNING, "start", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "upload", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyze", Actor.WEB)
    repo.transition_task(task.id, TaskStatus.DONE, "done", Actor.AGENT)
    return task.id


def test_evidence_native_showcase_hero_path(client: TestClient):
    created = client.post("/api/v1/cases", json={
        "title": "展示闭环",
        "problem_description": "checkout latency increased",
        "recovery_goal": "identify the supported mechanism",
        "run_mode": "COLLABORATE",
        "environment": "staging",
        "target_scope": {"service_id": "checkout"},
    })
    assert created.status_code == 200, created.text
    case = created.json()["data"]

    branch_response = client.post(
        f"/api/v1/cases/{case['case_id']}/branches",
        json={"label": "CPU hypothesis", "reason": "showcase"},
    )
    assert branch_response.status_code == 200, branch_response.text
    branch_id = branch_response.json()["data"]["branch_id"]

    task_id = _done_task_with_artifact()
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}], "purpose": "主路径真实采集产物"},
    )
    assert attached.status_code == 200, attached.text
    attachment_item = attached.json()["data"]["items"][0]
    assert attachment_item["result"] == "ACCEPTED"
    assert attachment_item["evidence_ids"]
    evidence_id = attachment_item["evidence_ids"][0]
    stored_evidence = repo.get_case_evidence(case["case_id"], "tenant-a", evidence_id)
    assert stored_evidence["task_id"] == task_id
    assert stored_evidence["artifact_id"]
    assert stored_evidence["source_type"] == "task_artifact"
    assert stored_evidence["lineage"]["artifact_id"] == stored_evidence["artifact_id"]
    projection = repo.list_evidence_projections(case["case_id"], "tenant-a", evidence_id)[0]
    assert projection["projection_hash"] == stored_evidence["projection_hash"]

    headers = {"X-Internal-Token": TOKEN}
    visible = client.post(
        "/internal/agent/tools/list-case-evidence",
        json={"case_id": case["case_id"], "branch_id": branch_id}, headers=headers,
    )
    assert visible.status_code == 200, visible.text
    assert {item["evidence_id"] for item in visible.json()["data"]["items"]} == {evidence_id}

    envelope = {
        "case_id": case["case_id"], "branch_id": branch_id,
        "expected_scope_revision": case["scope_revision"],
        "expected_control_revision": case["control_revision"],
    }
    hypotheses = client.post(
        "/internal/agent/tools/hypotheses",
        json={**envelope, "hypotheses": [{
            "hypothesis_id": "cpu-saturation",
            "statement": "CPU saturation increases checkout latency",
            "status": "SUPPORTED", "supporting_evidence_refs": [evidence_id],
        }]}, headers=headers,
    )
    assert hypotheses.status_code == 200, hypotheses.text

    graph = client.post(
        "/internal/agent/tools/causal-graph",
        json={
            **envelope, "expected_evidence_watermark": 1,
            "nodes": [
                {"node_id": "cpu", "entity_ref": "service:checkout", "mechanism": "CPU saturation", "role": "PRIMARY_ROOT_CAUSE", "supporting_evidence_refs": [evidence_id]},
                {"node_id": "latency", "entity_ref": "service:checkout", "mechanism": "request latency", "role": "SYMPTOM", "supporting_evidence_refs": [evidence_id]},
            ],
            "edges": [{"edge_id": "cpu-latency", "source_node_id": "cpu", "target_node_id": "latency", "relation": "CAUSES", "supporting_evidence_refs": [evidence_id]}],
        }, headers=headers,
    )
    assert graph.status_code == 200, graph.text

    finished = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"], "branch_id": branch_id,
            "summary": "CPU saturation is the supported mechanism",
            "state": "CONFIRMED", "evidence_ids": [evidence_id],
            "primary_root_causes": [{"summary": "CPU saturation"}],
        }, headers=headers,
    )
    assert finished.status_code == 200, finished.text
    # The verifier keeps unresolved alternatives visible instead of upgrading
    # a single supporting signal into a false absolute conclusion.
    assert finished.json()["data"]["state"] == "PARTIALLY_CONFIRMED"

    workspace = client.get(
        f"/api/v1/cases/{case['case_id']}/workspace", params={"branch_id": branch_id},
    )
    assert workspace.status_code == 200, workspace.text
    data = workspace.json()["data"]
    assert data["branch_id"] == branch_id
    assert data["evidence"][0]["evidence_id"] == evidence_id
    assert data["evidence"][0]["task_id"] == task_id
    assert data["evidence"][0]["artifact_id"] == stored_evidence["artifact_id"]
    assert data["evidence"][0]["source_type"] == "task_artifact"
    assert data["hypothesis_graph"]["hypotheses"]
    assert data["causal_graph"]["edges"]
    assert data["conclusion"]["state"] == "PARTIALLY_CONFIRMED"


def test_review_exclude_moves_workspace_conclusion_to_recheck_required(client: TestClient):
    case = client.post("/api/v1/cases", json={
        "title": "展示复核闭环",
        "problem_description": "checkout latency increased",
        "recovery_goal": "revalidate the supported mechanism",
        "run_mode": "COLLABORATE",
        "environment": "staging",
        "target_scope": {"service_id": "checkout"},
    }).json()["data"]
    task_id = _done_task_with_artifact()
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    finished = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "CPU saturation is the supported mechanism",
            "evidence_ids": [evidence_id],
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert finished.status_code == 200, finished.text

    preview = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews/preview",
        json={"decision": "EXCLUDED", "assessment": {}},
    )
    assert preview.status_code == 200, preview.text
    impact = preview.json()["data"]
    assert impact["predicted_conclusion_state"] == "RECHECK_REQUIRED"
    reviewed = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={
            "evidence_id": evidence_id,
            "decision": "EXCLUDED",
            "expected_review_revision": impact["current_review_revision"],
            "impact_token": impact["impact_token"],
            "reason_code": "USER_EXCLUDED",
            "reason": "showcase review exclusion",
            "assessment": {},
        },
    )
    assert reviewed.status_code == 200, reviewed.text

    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    data = workspace.json()["data"]
    assert data["conclusion"]["state"] == "RECHECK_REQUIRED"
    assert data["conclusion"]["revision"] == 2
    assert data["conclusion"]["verifier_version"] == "causal-report-verifier.v2-revalidation"
    assert data["conclusion"]["invalidated_claims"]
    assert data["conclusion_history"][0]["state"] == "RECHECK_REQUIRED"
    assert data["conclusion_history"][1]["state"] == "PARTIALLY_CONFIRMED"
