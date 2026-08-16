"""T1 local longitudinal chain without a real model.

HTTP Turn -> AgentRuntimePort/Sidecar surface -> internal Query tool ->
native Task -> Task completion -> CaseEvidence -> Runtime follow_up.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from server.app.agent_runtime.dispatcher import reset_runtime
from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.state_machine import Actor, TaskStatus

TOKEN = "loop-token"


class LoopSidecarHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, str, dict]] = []

    def log_message(self, *args):
        pass

    def _respond(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
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
                "runtime_session_id": "loop-session",
                "runtime_generation": body["context"].get("runtime_generation") or 1,
                "status": "READY",
                "last_event_seq": 0,
                "last_context_snapshot_id": None,
                "lease_owner": "loop-sidecar",
            }})
        elif self.path.endswith("/turn"):
            self._respond({"ok": True, "data": {
                "turn_id": "turn-loop", "accepted": True, "mode": "pi", "detail": "ok",
            }})
        elif self.path.endswith("/follow-up"):
            self._respond({"ok": True, "data": {"accepted": True}})
        else:
            self._respond({"ok": True, "data": {"accepted": True}})


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
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
def _sidecar(monkeypatch):
    LoopSidecarHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), LoopSidecarHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    reset_runtime()
    try:
        yield
    finally:
        server.shutdown()


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "local-loop-case",
        "problem_description": "商城变慢，请自行定位",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def test_local_longitudinal_chain_turn_query_task_evidence_wakeup(client, monkeypatch):
    import server.app.app_factory as main_module
    from server.app.schemas import CreateTaskRequest

    repo.register_agent(
        "agent-loop", "node-loop", "192.168.77.10", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    with _sidecar(monkeypatch):
        # 1. User Turn enters AgentRuntimePort.
        turn = client.post(
            f"/api/v1/cases/{case['case_id']}/agent/turn",
            json={"message": "请自行定位"},
        )
        assert turn.status_code == 200, turn.text
        assert turn.json()["data"]["status"] == "runtime_turn_accepted"

        # 2. Simulated Pi tool call through internal gateway creates a native Task.
        query = client.post(
            "/internal/agent/tools/query",
            json={"case_id": case["case_id"], "operation": "process.list", "parameters": {}},
            headers={"X-Internal-Token": TOKEN},
        )
        assert query.status_code == 200, query.text
        task_id = query.json()["data"]["task"]["id"]
        assert repo.tasks[task_id].collector_type == "process_scan"

    # 3. Artifact arrives and task completes.
    repo.add_artifacts(task_id, [{
        "artifact_type": "process_scan",
        "metadata": {"processes": [{"pid": 1, "comm": "init"}]},
    }])
    repo.transition_task(task_id, TaskStatus.RUNNING, "start", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "upload", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyze", Actor.WEB)
    repo.transition_task(task_id, TaskStatus.DONE, "done", Actor.AGENT)

    # 4. Task completion wakes Runtime with materialized Case Evidence.
    class FakeRuntime:
        def __init__(self):
            self.notes = []

        def follow_up(self, case_id, instruction):
            self.notes.append((case_id, instruction))

    fake = FakeRuntime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: fake)
    main_module._wake_case_from_task(task_id, "DONE")
    assert len(fake.notes) == 1
    evidence_ids = fake.notes[0][1].evidence_ids
    assert evidence_ids
    stored = repo.list_case_evidence(case["case_id"], "tenant-a")
    assert {item["evidence_id"] for item in stored} == set(evidence_ids)
