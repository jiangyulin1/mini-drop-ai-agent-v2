"""E3: PiAgentRuntimeAdapter against a mock Mini-Drop internal-protocol sidecar."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from server.app.agent_runtime.port import (
    AgentTurnInput,
    CaseContextSnapshot,
    RuntimeFollowUp,
    RuntimeSteer,
)

MODULE = "server.app.agent_runtime.pi_adapter"


class MockSidecarHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, str, dict]] = []
    base = None

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
                "runtime_version": "pi-0.84.0",
                "runtime_session_id": "mock-session-1",
                "runtime_generation": 1,
                "status": "READY",
                "last_event_seq": 0,
                "last_context_snapshot_id": "ctx-mock",
                "lease_owner": "mock-sidecar",
            }})
        elif self.path.endswith("/turn"):
            self._respond({"ok": True, "data": {
                "turn_id": "turn-mock-1", "accepted": True, "mode": "pi",
                "detail": "已提交到 Pi Sidecar",
            }})
        elif self.path.endswith("/steer"):
            self._respond({"ok": True, "data": {"accepted": True}})
        elif self.path.endswith("/abort"):
            self._respond({"ok": True, "data": {"aborted": True}})
        else:
            self._respond({"ok": True, "data": {"accepted": True}})

    def do_GET(self):
        self.__class__.received.append((self.path, "GET", {}))
        if self.path.endswith("/state"):
            self._respond({"ok": True, "data": {
                "case_id": "case-mock", "status": "READY",
                "runtime_generation": 1, "last_event_seq": 5,
                "runtime_version": "pi-0.84.0", "detail": "",
            }})
        else:
            self._respond({"ok": False, "error": "not_found"})


@pytest.fixture()
def sidecar(monkeypatch):
    MockSidecarHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), MockSidecarHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _adapter(sidecar_url: str):
    from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter
    return PiAgentRuntimeAdapter(sidecar_url)


def _snapshot() -> CaseContextSnapshot:
    return CaseContextSnapshot(
        case_id="case-a", tenant_id="tenant-a", case_goal="定位支付超时",
        autonomy_mode="COLLABORATE", plan_revision=3, scope_revision=2,
        evidence_summary=[{"evidence_id": "ev-1", "summary": "cpu 饱和"}],
    )


def test_start_or_resume_binds_runtime(sidecar):
    adapter = _adapter(sidecar)
    binding = adapter.start_or_resume(_snapshot())
    assert binding.runtime_session_id == "mock-session-1"
    assert binding.runtime_generation == 1
    assert binding.status == "READY"


def test_submit_turn_forwards_message_and_references(sidecar):
    adapter = _adapter(sidecar)
    turn = AgentTurnInput(case_id="case-a", message="继续调查数据库", references=[{
        "type": "task", "id": "task-1",
    }])
    accepted = adapter.submit_turn("case-a", turn)
    assert accepted.accepted is True
    assert accepted.mode == "pi"
    paths = [item[0] for item in MockSidecarHandler.received]
    assert any(path.endswith("/turn") for path in paths)


def test_steer_and_abort_reach_sidecar(sidecar):
    adapter = _adapter(sidecar)
    adapter.steer("case-a", RuntimeSteer(case_id="case-a", instruction="改查发布",
                                         reason_code="USER_DIRECTION",
                                         scope_revision=2, plan_revision=3))
    adapter.abort("case-a", "用户停止")
    paths = [item[0] for item in MockSidecarHandler.received]
    assert any(path.endswith("/steer") for path in paths)
    assert any(path.endswith("/abort") for path in paths)


def test_get_state_returns_runtime_state(sidecar):
    adapter = _adapter(sidecar)
    state = adapter.get_state("case-a")
    assert state.status == "READY"
    assert state.last_event_seq == 5


def test_adapter_requires_sidecar_url(monkeypatch):
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", "")
    from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter
    with pytest.raises(RuntimeError, match="MINI_DROP_PI_RUNTIME_URL"):
        PiAgentRuntimeAdapter()
