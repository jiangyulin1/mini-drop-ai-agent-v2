"""Incident Case collaboration, tenant isolation and safety-control tests."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.case_collaboration import build_case_diagnosis_query
from server.app.database import init_db, reset_engine
from server.app.main import app, diagnosis_orchestrator
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


def _case_payload(*, scoped: bool = True) -> dict:
    return {
        "title": "checkout 延迟事故",
        "problem_description": "checkout 服务过去十分钟延迟显著升高",
        "recovery_goal": "p95 延迟恢复到 300ms 以下并稳定 10 分钟",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": (
            {"cluster_id": "prod-a", "service_id": "checkout"}
            if scoped else {}
        ),
        "time_range": {
            "start": "2026-08-05T11:00:00Z",
            "end": "2026-08-05T11:10:00Z",
            "source": "user_expression",
        },
    }


def test_case_requires_scope_then_correction_opens_investigation(client: TestClient):
    created = client.post("/api/v1/cases", json=_case_payload(scoped=False))
    assert created.status_code == 200
    case = created.json()["data"]
    assert case["state"] == "NEEDS_SCOPE_CONFIRMATION"
    assert case["summary"]["need_you"]["required"] is True

    corrected = client.post(
        f"/api/v1/cases/{case['case_id']}/corrections",
        json={
            "target_scope": {"cluster_id": "prod-a", "service_id": "checkout"},
            "reason": "补充目标服务",
            "expected_row_version": 0,
        },
    )
    assert corrected.status_code == 200
    updated = corrected.json()["data"]
    assert updated["state"] == "OPEN"
    assert updated["scope_revision"] == 2
    assert updated["summary"]["current_finding"]["status"] == "invalidated"

    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert [event["event_type"] for event in events] == ["case_created", "case_corrected"]
    assert events[-1]["payload"]["invalidates_pending_plan"] is True


def test_case_message_pause_resume_and_optimistic_version(client: TestClient):
    case = client.post("/api/v1/cases", json=_case_payload()).json()["data"]
    case_id = case["case_id"]

    message = client.post(
        f"/api/v1/cases/{case_id}/messages",
        json={"content": "发布刚刚发生过，请优先检查变更", "kind": "message"},
    )
    assert message.status_code == 200

    stale = client.post(
        f"/api/v1/cases/{case_id}/pause",
        json={"reason": "先暂停调查", "expected_row_version": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "CASE_VERSION_CONFLICT"

    paused = client.post(
        f"/api/v1/cases/{case_id}/pause",
        json={"reason": "先暂停调查", "expected_row_version": 1},
    )
    assert paused.status_code == 200
    assert paused.json()["data"]["state"] == "PAUSED"

    resumed = client.post(
        f"/api/v1/cases/{case_id}/resume",
        json={"reason": "继续调查", "expected_row_version": 2},
    )
    assert resumed.status_code == 200
    assert resumed.json()["data"]["state"] == "OPEN"


def test_case_access_is_tenant_scoped(client: TestClient, monkeypatch):
    case_id = client.post("/api/v1/cases", json=_case_payload()).json()["data"]["case_id"]

    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-b")
    assert client.get(f"/api/v1/cases/{case_id}").status_code == 404
    listing = client.get("/api/v1/cases").json()["data"]
    assert listing["total"] == 0
    assert client.get(f"/api/v1/cases/{case_id}/events").status_code == 404


def test_stopping_case_revokes_case_grants_and_blocks_messages(client: TestClient):
    case = client.post("/api/v1/cases", json=_case_payload()).json()["data"]
    case_id = case["case_id"]
    grant_payload = {
        "principal_id": "local-development",
        "tenant_id": "tenant-a",
        "source_ids": ["mini-drop-agent-metrics"],
        "operations": ["metrics.read"],
        "resource_scope": {"cluster_id": ["prod-a"], "service_id": ["checkout"]},
        "mode": "case",
        "case_id": case_id,
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "constraints": {"max_queries": 10},
        "created_by": "ignored-client-value",
    }
    grant = client.post("/api/v1/grants", json=grant_payload)
    assert grant.status_code == 200

    stopped = client.post(
        f"/api/v1/cases/{case_id}/stop",
        json={"reason": "用户停止 Case", "expected_row_version": 0},
    )
    assert stopped.status_code == 200
    assert stopped.json()["data"]["state"] == "STOPPED"

    grants = client.get(
        "/api/v1/grants?tenant_id=tenant-a&include_inactive=true",
    ).json()["data"]["items"]
    matching = next(item for item in grants if item["case_id"] == case_id)
    assert matching["status"] == "REVOKED"

    blocked = client.post(
        f"/api/v1/cases/{case_id}/messages",
        json={"content": "不应再触发调查", "kind": "message"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "CASE_STOPPED"


def test_resolving_case_records_recovery_and_event(client: TestClient):
    case = client.post("/api/v1/cases", json=_case_payload()).json()["data"]
    case_id = case["case_id"]

    resolved = client.post(
        f"/api/v1/cases/{case_id}/resolve",
        json={"reason": "service recovered", "expected_row_version": 0},
    )

    assert resolved.status_code == 200
    data = resolved.json()["data"]
    assert data["state"] == "RESOLVED"
    assert data["resolved_at"]
    assert data["summary"]["recovery"]["status"] == "verified"
    events = client.get(f"/api/v1/cases/{case_id}/events").json()["data"]["items"]
    assert events[-1]["event_type"] == "case_resolved"


def test_task_artifact_reader_passes_only_the_task_artifact_list(monkeypatch):
    import server.app.main as main_module

    repository = type("Repository", (), {
        "artifacts": {"task-1": [{"artifact_type": "sys_metrics", "value": 1}]},
    })()
    received = []

    def fake_extract(artifacts, artifact_type):
        received.append((artifacts, artifact_type))
        return {"ok": True}

    monkeypatch.setattr(main_module, "_extract_artifact_json", fake_extract)

    assert main_module._extract_task_artifact_json(repository, "task-1", "sys_metrics") == {"ok": True}
    assert received == [(repository.artifacts["task-1"], "sys_metrics")]


def test_case_diagnosis_query_includes_recent_user_facts_only():
    query = build_case_diagnosis_query(
        {"problem_description": "checkout latency is high"},
        [
            {"event_type": "case_paused", "payload": {"content": "ignore me"}},
            {"event_type": "user_message", "payload": {"content": "release started at 10:05"}},
            {"event_type": "user_message", "payload": {"content": "only worker-2 is affected"}},
        ],
        max_chars=120,
    )

    assert query.startswith("checkout latency is high")
    assert "release started at 10:05" in query
    assert "only worker-2 is affected" in query
    assert "ignore me" not in query
    assert len(query) <= 120


def test_case_rejects_unknown_linked_objects(client: TestClient):
    payload = _case_payload()
    payload["diagnosis_session_id"] = "diag_missing"
    response = client.post("/api/v1/cases", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "DIAGNOSIS_SESSION_NOT_FOUND"


def test_case_pause_and_resume_control_linked_diagnosis(client: TestClient):
    diagnosis = client.post(
        "/api/v1/diagnoses",
        json={"query": "checkout 为什么变慢"},
    )
    assert diagnosis.status_code == 200
    diagnosis_id = diagnosis.json()["data"]["diagnosis_id"]
    assert diagnosis_orchestrator.store.get_session(diagnosis_id)["status"] == (
        "NEEDS_SCOPE_CONFIRMATION"
    )

    payload = _case_payload()
    payload["diagnosis_session_id"] = diagnosis_id
    case = client.post("/api/v1/cases", json=payload).json()["data"]

    paused = client.post(
        f"/api/v1/cases/{case['case_id']}/pause",
        json={"reason": "等待用户补充范围", "expected_row_version": 0},
    )
    assert paused.status_code == 200
    diagnosis_state = diagnosis_orchestrator.store.get_session(diagnosis_id)
    assert diagnosis_state["status"] == "PAUSED"
    assert diagnosis_state["paused_from_status"] == "NEEDS_SCOPE_CONFIRMATION"

    resumed = client.post(
        f"/api/v1/cases/{case['case_id']}/resume",
        json={"reason": "范围已确认", "expected_row_version": 1},
    )
    assert resumed.status_code == 200
    diagnosis_state = diagnosis_orchestrator.store.get_session(diagnosis_id)
    assert diagnosis_state["status"] == "NEEDS_SCOPE_CONFIRMATION"
    assert diagnosis_state["paused_from_status"] is None


def test_case_diagnosis_persists_context_and_model_attempt_audit(
    client: TestClient, monkeypatch,
):
    import json
    import server.app.ai_provider as ai_provider

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            arguments = {
                "symptom": "latency_increase",
                "time_range": {
                    "start": "2026-08-05T11:00:00Z",
                    "end": "2026-08-05T11:10:00Z",
                    "source": "user_expression",
                },
            }
            return {
                "model": "test-model-snapshot",
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "function": {
                                "name": "emit_diagnosis_intent",
                                "arguments": json.dumps(arguments),
                            },
                        }],
                    },
                }],
            }

    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "nlp-only")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "test-key")
    monkeypatch.setenv("MINI_DROP_AI_PROVIDER", "openai")
    monkeypatch.setenv("MINI_DROP_AI_MODEL", "test-model")
    monkeypatch.setattr(ai_provider, "_post_json", lambda *args, **kwargs: FakeResponse())

    create_payload = _case_payload()
    create_payload["target_scope"]["api_key"] = "must-be-redacted"
    case = client.post("/api/v1/cases", json=create_payload).json()["data"]
    started = client.post(
        f"/api/v1/cases/{case['case_id']}/diagnoses",
        json={"expected_row_version": 0},
    )
    assert started.status_code == 200, started.text
    assert started.json()["data"]["case"]["state"] == "WAITING_USER"

    packets = client.get(
        f"/api/v1/cases/{case['case_id']}/context-packets",
    ).json()["data"]["items"]
    assert len(packets) == 1
    packet = packets[0]
    assert packet["schema_version"] == "case-context.v1"
    assert packet["payload"]["scope"]["target_scope"]["api_key"] == "[REDACTED]"
    assert len(packet["content_hash"]) == 64

    attempts = client.get(
        f"/api/v1/cases/{case['case_id']}/model-attempts",
    ).json()["data"]["items"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["status"] == "SUCCEEDED"
    assert attempt["model"] == "test-model"
    assert attempt["model_snapshot"] == "test-model-snapshot"
    assert attempt["input_tokens"] == 123
    assert attempt["output_tokens"] == 45
    assert len(attempt["response_hash"]) == 64
    assert "response" not in attempt

    graph = client.get(
        f"/api/v1/cases/{case['case_id']}/hypotheses",
    ).json()["data"]
    assert graph["hypotheses"]
    assert any(item["hypothesis_id"] == "OTHER_UNKNOWN" for item in graph["hypotheses"])
    iterations = client.get(
        f"/api/v1/cases/{case['case_id']}/iterations",
    ).json()["data"]["items"]
    assert len(iterations) == 1
    assert iterations[0]["selected_action"]["action_id"] == "diagnosis-orchestrator.start"
    assert iterations[0]["policy_decision"]["decision"] == "AUTO_REVIEWED"

    corrected = client.post(
        f"/api/v1/cases/{case['case_id']}/corrections",
        json={
            "target_scope": {"service_id": "checkout-v2"},
            "reason": "用户确认实际服务版本",
            "expected_row_version": 1,
        },
    )
    assert corrected.status_code == 200
    invalidated = client.get(
        f"/api/v1/cases/{case['case_id']}/hypotheses",
    ).json()["data"]["hypotheses"]
    assert invalidated
    assert all(item["status"] in {"WEAKENED", "RULED_OUT"} for item in invalidated)

    restarted = client.post(
        f"/api/v1/cases/{case['case_id']}/diagnoses",
        json={"expected_row_version": corrected.json()["data"]["row_version"]},
    )
    assert restarted.status_code == 200, restarted.text
    iterations = client.get(
        f"/api/v1/cases/{case['case_id']}/iterations",
    ).json()["data"]["items"]
    assert sorted(item["iteration_no"] for item in iterations) == [0, 1]


def test_case_correction_cancels_and_detaches_superseded_diagnosis(client: TestClient):
    diagnosis = client.post(
        "/api/v1/diagnoses",
        json={"query": "checkout 为什么变慢"},
    ).json()["data"]
    payload = _case_payload()
    payload["diagnosis_session_id"] = diagnosis["diagnosis_id"]
    case = client.post("/api/v1/cases", json=payload).json()["data"]

    corrected = client.post(
        f"/api/v1/cases/{case['case_id']}/corrections",
        json={
            "target_scope": {"cluster_id": "prod-a", "service_id": "checkout-v2"},
            "reason": "确认实际目标是 checkout-v2",
            "expected_row_version": 0,
        },
    )
    assert corrected.status_code == 200
    updated = corrected.json()["data"]
    assert updated["diagnosis_session_id"] is None
    assert updated["scope_revision"] == 2
    assert diagnosis_orchestrator.store.get_session(diagnosis["diagnosis_id"])["status"] == (
        "USER_CANCELED"
    )
    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert events[-1]["payload"]["superseded_diagnosis_id"] == diagnosis["diagnosis_id"]
