"""T1 local longitudinal chain without a real model.

HTTP Turn -> AgentRuntimePort/Sidecar surface -> Collector proposal ->
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

    monkeypatch.setenv("MINI_DROP_WAKEUP_QUIET_SEC", "0")

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
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"], "collector_id": "process_scan",
                "target_selector": {"agent_id": "agent-loop", "target_pid": 1},
                "parameters": {},
                "information_goal": "将服务或主机目标解析为具体 Linux 进程",
                "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
            },
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
    requests = repo.list_collection_requests(case["case_id"], "tenant-a")
    assert requests[0]["status"] == "COMPLETED"
    main_module._run_runtime_wakeup_pass()
    assert len(fake.notes) == 1
    evidence_ids = fake.notes[0][1].evidence_ids
    assert evidence_ids
    stored = repo.list_case_evidence(case["case_id"], "tenant-a")
    assert {item["evidence_id"] for item in stored} == set(evidence_ids)


def test_recovery_marks_request_terminal_when_evidence_was_materialized_early(client):
    import server.app.app_factory as main_module
    from server.app.runtime_services import case_evidence_service

    repo.register_agent(
        "agent-early-evidence", "node-early", "192.168.77.11", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    query = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"], "collector_id": "process_scan",
            "target_selector": {"agent_id": "agent-early-evidence", "target_pid": 1},
            "parameters": {},
            "information_goal": "将服务或主机目标解析为具体 Linux 进程",
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert query.status_code == 200, query.text
    task_id = query.json()["data"]["task"]["id"]
    repo.add_artifacts(task_id, [{
        "artifact_type": "process_scan",
        "metadata": {"processes": [{"pid": 1, "comm": "init"}]},
    }])
    for status in (
        TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING, TaskStatus.DONE,
    ):
        repo.transition_task(task_id, status, "early evidence", Actor.AGENT)

    evidence_ids = case_evidence_service.materialize_task_artifacts(
        case["case_id"], "tenant-a", task_id=task_id, actor_id="topology-workflow",
    )
    assert evidence_ids
    assert repo.list_collection_requests(case["case_id"], "tenant-a")[0]["status"] == "DISPATCHED"

    main_module._run_case_task_wake_pass()

    requests = repo.list_collection_requests(case["case_id"], "tenant-a")
    assert requests[0]["status"] == "COMPLETED"
    assert len(repo.list_case_evidence(case["case_id"], "tenant-a")) == len(evidence_ids)


def test_failed_collection_creates_durable_non_evidence_wakeup(client, monkeypatch):
    import server.app.app_factory as main_module

    monkeypatch.setenv("MINI_DROP_WAKEUP_QUIET_SEC", "0")
    repo.register_agent(
        "agent-loop", "node-loop", "192.168.77.10", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    with _sidecar(monkeypatch):
        turn = client.post(
            f"/api/v1/cases/{case['case_id']}/agent/turn",
            json={"message": "请自行定位"},
        )
        assert turn.status_code == 200, turn.text
        query = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"], "collector_id": "process_scan",
                "target_selector": {"agent_id": "agent-loop", "target_pid": 1},
                "parameters": {},
                "information_goal": "将服务或主机目标解析为具体 Linux 进程",
                "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
            },
            headers={"X-Internal-Token": TOKEN},
        )
        assert query.status_code == 200, query.text
        task_id = query.json()["data"]["task"]["id"]

    repo.transition_task(task_id, TaskStatus.FAILED, "collector permission denied", Actor.AGENT)
    evidence_watermark = len(repo.list_case_evidence(case["case_id"], "tenant-a"))

    class FakeRuntime:
        def __init__(self):
            self.notes = []

        def follow_up(self, case_id, instruction):
            self.notes.append((case_id, instruction))

    fake = FakeRuntime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: fake)
    # The periodic sweep must recover terminal events completed by another process.
    main_module._run_case_task_wake_pass()
    # Replaying the terminal event must not create a second outbox effect or wakeup.
    main_module._wake_case_from_task(task_id, TaskStatus.FAILED.value)

    requests = repo.list_collection_requests(case["case_id"], "tenant-a")
    assert requests[0]["status"] == "FAILED"
    terminal_events = [
        item for item in repo.list_domain_outbox(limit=100)
        if item["event_type"] == "COLLECTION_TERMINAL"
    ]
    assert len(terminal_events) == 1
    wakeups = repo.list_runtime_wakeups(case["case_id"], "tenant-a")
    assert len(wakeups) == 1
    assert wakeups[0]["reason_class"] == "COLLECTION_TERMINAL"
    assert wakeups[0]["from_evidence_watermark"] == evidence_watermark
    assert wakeups[0]["to_evidence_watermark"] == evidence_watermark

    main_module._run_runtime_wakeup_pass()
    assert len(fake.notes) == 1
    instruction = fake.notes[0][1]
    assert instruction.evidence_ids == []
    assert "CollectionRequest" in instruction.note
    assert "collector permission denied" in instruction.note
    assert "不要假设存在新 Evidence" in instruction.note
    assert "新 Evidence 已物化" not in instruction.note
    cycles = repo.list_agent_cycles(case["case_id"], "tenant-a")
    assert cycles[-1]["trigger_type"] == "COLLECTION_TERMINAL"
    assert len(repo.list_case_evidence(case["case_id"], "tenant-a")) == evidence_watermark


def test_completed_collection_without_artifacts_uses_terminal_wakeup(client, monkeypatch):
    import server.app.app_factory as main_module

    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    repo.register_agent(
        "agent-loop", "node-loop", "192.168.77.10", version="0.3.0",
        capabilities=["process_scan"],
    )
    case = _create_case(client)
    query = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"], "collector_id": "process_scan",
            "target_selector": {"agent_id": "agent-loop", "target_pid": 1},
            "parameters": {},
            "information_goal": "将服务或主机目标解析为具体 Linux 进程",
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert query.status_code == 200, query.text
    task_id = query.json()["data"]["task"]["id"]
    for status in (TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING, TaskStatus.DONE):
        repo.transition_task(task_id, status, "empty collector result", Actor.AGENT)

    main_module._wake_case_from_task(task_id, TaskStatus.DONE.value)

    assert repo.list_case_evidence(case["case_id"], "tenant-a") == []
    wakeups = repo.list_runtime_wakeups(case["case_id"], "tenant-a")
    assert len(wakeups) == 1
    assert wakeups[0]["reason_class"] == "COLLECTION_TERMINAL"
    assert wakeups[0]["from_evidence_watermark"] == wakeups[0]["to_evidence_watermark"] == 0


def test_runtime_delivery_failure_requeues_and_reuses_audit_cycle(client, monkeypatch):
    import server.app.app_factory as main_module

    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    case = _create_case(client)
    run = repo.create_investigation_run(
        case_id=case["case_id"], tenant_id="tenant-a",
    )
    wakeup = repo.create_runtime_wakeup(
        case_id=case["case_id"], tenant_id="tenant-a",
        investigation_run_id=run["run_id"], reason="collector failed",
        source_refs=["collection_request:creq-test:FAILED"],
        reason_class="COLLECTION_TERMINAL", dedupe_key="delivery-retry-test",
    )

    class FlakyRuntime:
        def __init__(self):
            self.attempts = 0

        def follow_up(self, _case_id, _instruction):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("sidecar unavailable")

    runtime = FlakyRuntime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: runtime)
    main_module._run_runtime_wakeup_pass()

    first = repo.list_runtime_wakeups(case["case_id"], "tenant-a")[0]
    assert first["wakeup_id"] == wakeup["wakeup_id"]
    assert first["status"] == "PENDING"
    assert first["cycle_id"]
    cycles = repo.list_agent_cycles(case["case_id"], "tenant-a")
    assert len(cycles) == 1

    main_module._run_runtime_wakeup_pass()

    delivered = repo.list_runtime_wakeups(case["case_id"], "tenant-a")[0]
    assert delivered["status"] == "DELIVERED"
    assert delivered["cycle_id"] == first["cycle_id"]
    assert len(repo.list_agent_cycles(case["case_id"], "tenant-a")) == 1
    assert runtime.attempts == 2


def test_runtime_wakeup_inherits_turn_options_and_persists_rotated_binding(client, monkeypatch):
    import server.app.app_factory as main_module
    from server.app.agent_runtime.port import RuntimeBinding

    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    case = _create_case(client)
    case_id = case["case_id"]
    repo.create_context_packet({
        "case_id": case_id,
        "tenant_id": "tenant-a",
        "schema_version": "case-context.v1",
        "purpose": "runtime_turn",
        "iteration_no": 0,
        "payload": {
            "case_id": case_id,
            "diagnostic_strategy_id": "hybrid",
            "runtime_policy": {
                "side_effect_policy": "AUTO_READ_LOW",
                "max_collection_requests": 5,
                "max_collection_duration_sec": 70,
            },
            "runtime_options": {
                "strategy_id": "hybrid",
                "reasoning_effort": "low",
                "prompt_variant": "evidence_strict",
                "max_tokens": 768,
                "fresh_session": True,
                "runtime_support": {"fresh_session": "applied"},
            },
        },
        "projection_stats": {},
        "source_versions": {},
        "content_hash": "runtime-turn-options-test",
        "created_by": "test",
    })
    repo.upsert_agent_runtime_binding(
        case_id,
        "tenant-a",
        runtime_type="pi",
        runtime_version="pi-0.84.2",
        runtime_session_id=case_id,
        runtime_generation=1,
    )
    run = repo.create_investigation_run(case_id=case_id, tenant_id="tenant-a")
    wakeup = repo.create_runtime_wakeup(
        case_id=case_id,
        tenant_id="tenant-a",
        investigation_run_id=run["run_id"],
        reason="new evidence",
        source_refs=["task:follow-up"],
        reason_class="EVIDENCE_COMMITTED",
        dedupe_key="runtime-options-binding-test",
    )

    class RotatingRuntime:
        def __init__(self):
            self.context = None

        def start_or_resume(self, context):
            self.context = context
            return RuntimeBinding(
                case_id=case_id,
                runtime_type="pi",
                runtime_version="pi-0.84.2",
                runtime_session_id=case_id,
                runtime_generation=2,
                status="READY",
                last_event_seq=0,
                lease_owner="test-sidecar",
            )

        def follow_up(self, _case_id, _instruction):
            binding = repo.get_agent_runtime_binding(case_id, "tenant-a")
            assert binding["runtime_generation"] == 2

    runtime = RotatingRuntime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: runtime)

    main_module._run_runtime_wakeup_pass()

    assert runtime.context.runtime_options["reasoning_effort"] == "low"
    assert runtime.context.runtime_options["prompt_variant"] == "evidence_strict"
    assert runtime.context.runtime_options["fresh_session"] is True
    assert runtime.context.runtime_policy["max_collection_requests"] == 5
    delivered = repo.list_runtime_wakeups(case_id, "tenant-a")[0]
    assert delivered["wakeup_id"] == wakeup["wakeup_id"]
    assert delivered["status"] == "DELIVERED"


def test_topology_evidence_wakeup_without_analysis_run_requires_finish_tool(client, monkeypatch):
    """Direct topology materialization must not make Pi invent an analysis ID."""
    import server.app.app_factory as main_module

    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    case = _create_case(client)
    run = repo.create_investigation_run(case_id=case["case_id"], tenant_id="tenant-a")
    repo.create_runtime_wakeup(
        case_id=case["case_id"], tenant_id="tenant-a",
        investigation_run_id=run["run_id"], reason="topology evidence",
        source_refs=["task:topology-seed"], reason_class="EVIDENCE_COMMITTED",
        dedupe_key="topology-no-analysis-run",
    )

    class FakeRuntime:
        def __init__(self):
            self.notes = []

        def follow_up(self, case_id, instruction):
            self.notes.append((case_id, instruction))

    fake = FakeRuntime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: fake)
    main_module._run_runtime_wakeup_pass()

    assert len(fake.notes) == 1
    note = fake.notes[0][1].note
    assert "没有预注册的 EvidenceAnalysisRun" in note
    assert "不要调用 submit_evidence_analysis" in note
    assert "使用 finish_investigation" in note
    assert "analysis_run_id" not in note or "不要编造 analysis_run_id" in note
    assert repo.list_runtime_wakeups(case["case_id"], "tenant-a")[0]["status"] == "DELIVERED"
