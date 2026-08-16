"""G2: /agent/turn routes through AgentRuntimePort in pi and pi_shadow modes."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from server.app.agent_runtime.dispatcher import reset_runtime
from server.app.database import init_db, reset_engine
from server.app.main import app, repo, case_evidence_service
from server.app.models import Base


class MockSidecarHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, str, dict]] = []

    def log_message(self, *args):
        pass

    def _respond(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.__class__.received.append((self.path, "POST", body))
        if self.path.endswith("/resume"):
            self._respond({"ok": True, "data": {
                "case_id": body["context"]["case_id"],
                "runtime_type": "pi",
                "runtime_version": "pi-0.83.0",
                "runtime_session_id": "mock-session-1",
                "runtime_generation": 1,
                "status": "READY",
                "last_event_seq": 0,
                "last_context_snapshot_id": None,
                "lease_owner": "mock-sidecar",
            }})
        elif self.path.endswith("/turn"):
            self._respond({"ok": True, "data": {
                "turn_id": "turn-mock-1",
                "accepted": True,
                "mode": "pi" if body.get("shadow") is not True else "pi_shadow",
                "detail": "ok",
            }})
        else:
            self._respond({"ok": True, "data": {"accepted": True}})


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "test-token")
    reset_engine()
    reset_runtime()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()
    reset_runtime()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


@contextmanager
def _start_sidecar(monkeypatch, mode: str):
    MockSidecarHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), MockSidecarHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", mode)
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "test-token")
    reset_runtime()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "runtime-turn-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


@pytest.mark.parametrize("mode", ["pi", "pi_shadow"])
def test_agent_turn_routes_through_agent_runtime_port(client, monkeypatch, mode):
    with _start_sidecar(monkeypatch, mode) as sidecar_url:
        case = _create_case(client)
        resp = client.post(
            f"/api/v1/cases/{case['case_id']}/agent/turn",
            json={"message": "商城变慢，请自行定位"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "runtime_turn_accepted"
        assert data["turn_id"] == "turn-mock-1"
        assert any(item[0].endswith("/turn") for item in MockSidecarHandler.received)
        binding = repo.get_agent_runtime_binding(case["case_id"], "tenant-a")
        assert binding is not None
        assert binding["runtime_generation"] == 1
        turns = repo.list_agent_runtime_turns(case["case_id"], "tenant-a")
        assert [item["turn_id"] for item in turns] == ["turn-mock-1"]
        state = client.get(f"/api/v1/cases/{case['case_id']}/agent/runtime-state")
        assert state.status_code == 200, state.text
        assert state.json()["data"]["binding"]["runtime_generation"] == 1
        assert state.json()["data"]["turns"][0]["status"] == "ACCEPTED"
        second = client.post(
            f"/api/v1/cases/{case['case_id']}/agent/turn",
            json={"message": "请继续"},
        )
        assert second.status_code == 200, second.text
        resume_requests = [
            item for item in MockSidecarHandler.received
            if item[0].endswith("/resume")
        ]
        assert resume_requests[-1][2]["context"]["runtime_generation"] == 2


def test_agent_turn_fails_closed_when_sidecar_url_missing(client, monkeypatch):
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", "")
    reset_runtime()
    case = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={"message": "继续调查"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "runtime_unavailable"
    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert events[-1]["event_type"] == "agent_runtime_turn_rejected"


def test_internal_runtime_events_are_deduplicated_by_idempotency_key(client):
    case = _create_case(client)
    repo.upsert_agent_runtime_binding(
        case["case_id"],
        "tenant-a",
        runtime_type="pi",
        runtime_version="pi-0.83.0",
        runtime_session_id="mock-session-1",
        runtime_generation=1,
        status="READY",
    )
    payload = {
        "runtime_generation": 1,
        "events": [
            {
                "event_id": "evt-1",
                "event_seq": 1,
                "event_type": "assistant_message",
                "payload": {"text": "hello"},
                "idempotency_key": "idem-1",
            }
        ],
    }
    headers = {"X-Internal-Token": "test-token"}
    first = client.post(
        f"/internal/runtime/v1/cases/{case['case_id']}/events",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["accepted"] == 1
    replay = client.post(
        f"/internal/runtime/v1/cases/{case['case_id']}/events",
        json=payload,
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    events = repo.list_agent_runtime_events(case["case_id"], "tenant-a")
    assert len(events) == 1


def test_task_done_wakes_pi_runtime_with_materialized_case_evidence(client, monkeypatch):
    import server.app.main as main_module
    from server.app.schemas import CreateTaskRequest

    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    reset_runtime()
    case = _create_case(client)
    repo.register_agent(
        "agent-wake", "node-wake", "192.168.90.10", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    repo.upsert_agent_runtime_binding(
        case["case_id"],
        "tenant-a",
        runtime_type="pi",
        runtime_version="pi-0.83.0",
        runtime_session_id="session-wake",
        runtime_generation=1,
        status="READY",
    )
    task = repo.create_task(CreateTaskRequest(
        name="wake-task",
        agent_id="agent-wake",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=11,
        duration_sec=15,
        options={"case_id": case["case_id"], "tenant_id": "tenant-a"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "metadata": {"samples": 10, "cpu_percent": 91},
    }])

    class FakeRuntime:
        def __init__(self):
            self.calls = []

        def follow_up(self, case_id, instruction):
            self.calls.append((case_id, instruction))

    fake = FakeRuntime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: fake)
    main_module._wake_case_from_task(task.id, "DONE")
    assert len(fake.calls) == 1
    assert fake.calls[0][1].evidence_ids
    stored = case_evidence_service.list_evidence(case["case_id"], "tenant-a")
    assert len(stored) == 1
    assert stored[0]["task_id"] == task.id
