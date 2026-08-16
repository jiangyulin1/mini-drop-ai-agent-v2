"""v6 audit regression suite (M0).

Each test was written to reproduce one of the 2026-08-16 audit breaks.  They
assert the machine contract after the v6 core fix, never a mock-only signal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus

TOKEN = "v6-audit-token"


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _headers():
    return {"X-Internal-Token": TOKEN}


def _create_case(client: TestClient) -> dict:
    resp = client.post("/api/v1/cases", json={
        "title": "v6-audit-case",
        "problem_description": "checkout 延迟升高，请定位根因",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _done_task_with_artifact(collector_type="sys_metrics", metadata=None) -> str:
    repo.register_agent("agent-v6", "node-v6", "192.168.50.10", version="0.3.0", capabilities=[collector_type])
    task = repo.create_task(CreateTaskRequest(
        name="v6-audit-task",
        agent_id="agent-v6",
        target_pid=4242,
        collector_type=collector_type,
        sample_rate=11,
        duration_sec=15,
        options={"source": "manual"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": collector_type,
        "metadata": metadata or {"cpu_percent": 91.2, "rss_mb": 428, "window_sec": 15},
    }])
    for status in (TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING, TaskStatus.DONE):
        repo.transition_task(task.id, status, "audit", Actor.AGENT)
    return task.id


def test_evidence_projection_contains_real_values_not_metadata_only(client):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    listing = client.get(f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/projections")
    assert listing.status_code == 200, listing.text
    projection = listing.json()["data"]["items"][0]
    content = projection["content"]
    assert content["signals"].get("cpu_percent") == 91.2
    assert content["signals"].get("rss_mb") == 428
    assert projection["projection_hash"]
    assert content["summary"]
    assert content["interpretation_hints"][0]["kind"] == "derived"


def test_runtime_final_event_persists_assistant_message_and_completes_turn(client):
    case = _create_case(client)
    repo.record_agent_runtime_turn(
        turn_id="turn-final-1",
        case_id=case["case_id"],
        tenant_id="tenant-a",
        runtime_session_id="sess",
        runtime_generation=1,
        user_message="解释 CPU 图",
        requested_mode="explain",
        status="ACCEPTED",
        accepted_mode="pi",
        disposition="ANSWER_ONLY",
        side_effect_policy="READ_ONLY",
    )
    resp = client.post(
        f"/internal/runtime/v1/cases/{case['case_id']}/events",
        json={
            "runtime_generation": 1,
            "events": [{
                "event_id": "evt-final-1",
                "event_seq": 1,
                "event_type": "turn_end",
                "payload": {"text": "结论：CPU p95 为 91.2%（证据 ev-x）", "trigger_turn_id": "turn-final-1"},
            }],
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    messages = repo.list_assistant_messages(case["case_id"], "tenant-a")
    assert len(messages) == 1
    assert messages[0]["trigger_turn_id"] == "turn-final-1"
    assert "91.2" in messages[0]["content"]
    turn = repo.get_agent_runtime_turn("turn-final-1", "tenant-a")
    assert turn["status"] == "COMPLETED"
    events = repo.list_case_events(case["case_id"], "tenant-a")
    assert any(item["event_type"] == "assistant.message" for item in events)
    assert any(item["event_type"] == "turn.completed" for item in events)


def test_answer_only_machine_policy_blocks_write_tool_and_has_zero_side_effects(client):
    case = _create_case(client)
    turn = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={
            "message": "这张图表示什么？只解释",
            "requested_disposition": "ANSWER_ONLY",
            "execute_safe_tools": False,
        },
    )
    assert turn.status_code == 200, turn.text
    before = repo.list_case_evidence(case["case_id"], "tenant-a")
    denied = client.post(
        "/internal/agent/tools/query",
        json={
            "case_id": case["case_id"],
            "operation": "process.list",
            "parameters": {},
            "side_effect_policy": "READ_ONLY",
            "runtime_generation": 0,
        },
        headers=_headers(),
    )
    assert denied.status_code == 409
    assert denied.json()["detail"] == "TURN_READ_ONLY"
    after = repo.list_case_evidence(case["case_id"], "tenant-a")
    assert len(after) == len(before)
    stored_turn = repo.list_agent_runtime_turns(case["case_id"], "tenant-a")[0]
    assert stored_turn["disposition"] == "ANSWER_ONLY"
    assert stored_turn["side_effect_policy"] == "READ_ONLY"


def test_terminal_case_accepts_answer_only_but_rejects_new_investigation(client):
    case = _create_case(client)
    stop = client.post(
        f"/api/v1/cases/{case['case_id']}/commands",
        json={"command": "STOP", "reason": "user stopped"},
    )
    assert stop.status_code == 200, stop.text
    answer = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={"message": "解释已有证据", "requested_disposition": "ANSWER_ONLY"},
    )
    assert answer.status_code == 200, answer.text
    investigate = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={"message": "继续调查", "requested_disposition": "INVESTIGATE"},
    )
    assert investigate.status_code == 409
    assert investigate.json()["detail"] == "CASE_TERMINAL_NEW_INVESTIGATION_REQUIRES_NEW_RUN"


def test_late_write_tool_after_stop_is_fenced(client):
    case = _create_case(client)
    stop = client.post(
        f"/api/v1/cases/{case['case_id']}/commands",
        json={"command": "STOP", "reason": "user stopped"},
    )
    assert stop.status_code == 200, stop.text
    denied = client.post(
        "/internal/agent/tools/query",
        json={"case_id": case["case_id"], "operation": "process.list", "parameters": {}},
        headers=_headers(),
    )
    assert denied.status_code == 409
    assert denied.json()["detail"] == "RUN_TERMINAL"
    denied_plan = client.post(
        "/internal/agent/tools/plan",
        json={"case_id": case["case_id"], "goal": "late plan", "expected_plan_revision": 0},
        headers=_headers(),
    )
    assert denied_plan.status_code == 409
    assert denied_plan.json()["detail"] == "RUN_TERMINAL"


def test_task_wake_is_durable_when_sidecar_is_absent(client, monkeypatch):
    case = _create_case(client)
    repo.register_agent("agent-v6-wake", "node-v6-wake", "192.168.50.11", version="0.3.0", capabilities=["sys_metrics"])
    task = repo.create_task(CreateTaskRequest(
        name="v6-audit-wake-task",
        agent_id="agent-v6-wake",
        target_pid=4242,
        collector_type="sys_metrics",
        sample_rate=11,
        duration_sec=15,
        options={"source": "manual", "case_id": case["case_id"], "tenant_id": "tenant-a"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "metadata": {"cpu_percent": 91.2, "window_sec": 15},
    }])
    for status in (TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING, TaskStatus.DONE):
        repo.transition_task(task.id, status, "audit", Actor.AGENT)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", "")
    from server.app.agent_runtime.dispatcher import reset_runtime
    reset_runtime()
    from server.app import main as main_module
    main_module._wake_case_from_task(task.id, TaskStatus.DONE.value)
    wakeups = repo.list_runtime_wakeups(case["case_id"], "tenant-a")
    assert wakeups, "task wake must be durable before sidecar delivery"
    assert wakeups[0]["status"] in {"PENDING", "SEALED"}
    cycles = repo.list_agent_cycles(case["case_id"], "tenant-a")
    assert cycles, "wakeup dispatcher must create a persistent AgentCycle"
    assert cycles[0]["trigger_type"] == "EVIDENCE_COMMITTED"


def test_finish_rejects_wrong_projection_field_or_missing_projection(client):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    projection = client.get(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/projections"
    ).json()["data"]["items"][0]
    bad_field = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "错误字段结论",
            "evidence_ids": [evidence_id],
            "claims": [{
                "evidence_id": evidence_id,
                "projection_hash": projection["projection_hash"],
                "field_path": "signals.not_a_real_field",
                "predicate": {"operator": "gte", "value": 1},
            }],
        },
        headers=_headers(),
    )
    assert bad_field.status_code == 400
    assert bad_field.json()["detail"].startswith("CLAIM_VERIFICATION_FAILED")
    missing_projection = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "x", "evidence_ids": ["ev-absent"]},
        headers=_headers(),
    )
    assert missing_projection.status_code == 400
    assert missing_projection.json()["detail"].startswith("INVALID_EVIDENCE_REFS")


def test_case_events_do_not_invalidate_semantic_cas(client):
    case = _create_case(client)
    before = repo.get_incident_case(case["case_id"], "tenant-a")
    repo.record_case_event(
        case["case_id"], "tenant-a", event_type="system.note", payload={},
    )
    after = repo.get_incident_case(case["case_id"], "tenant-a")
    assert after["case_command_revision"] == before["case_command_revision"]
    assert after["control_revision"] == before["control_revision"]
    assert after["scope_revision"] == before["scope_revision"]


def test_case_event_cursor_uses_monotonic_sequence(client):
    case = _create_case(client)
    repo.record_case_event(case["case_id"], "tenant-a", event_type="a", payload={})
    repo.record_case_event(case["case_id"], "tenant-a", event_type="b", payload={})
    events = [item for item in repo.list_case_events(case["case_id"], "tenant-a") if item["event_type"] in {"a", "b"}]
    seqs = [item["case_event_seq"] for item in events]
    assert seqs[0] is not None and seqs[1] is not None
    assert seqs[0] < seqs[1]
    page = client.get(f"/api/v1/cases/{case['case_id']}/events", params={"after_seq": seqs[0]})
    assert page.status_code == 200
    assert [item["event_type"] for item in page.json()["data"]["items"] if item["event_type"] in {"a", "b"}] == ["b"]


def test_evidence_upsert_never_reassigns_another_case(client):
    first = _create_case(client)
    second = _create_case(client)
    task_id = _done_task_with_artifact()
    attached = client.post(
        f"/api/v1/cases/{first['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    with pytest.raises(ValueError):
        repo.upsert_case_evidence(
            case_id=second["case_id"],
            tenant_id="tenant-a",
            evidence_id=evidence_id,
            attachment_id=None,
            task_id=task_id,
            artifact_id=1,
            artifact_type="sys_metrics",
            collector_id="sys_metrics",
            source_type="task_artifact",
            target_ref="task:other",
            content_hash="x",
            projection_hash="y",
        )
