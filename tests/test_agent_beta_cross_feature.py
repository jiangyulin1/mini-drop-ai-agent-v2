"""Complex cross-feature scenarios for the Agent Beta surface.

Each test intentionally crosses several G-stage boundaries (G1-G6) so a
regression in one contract fails a realistic longitudinal scenario instead of
only an isolated unit.
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
from server.app.main import PLAN_DRIVER, app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus

TOKEN = "cross-feature-token"


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


def _create_case(client: TestClient, *, cluster: bool = False) -> dict:
    target_scope: dict = {"service_id": "checkout"}
    if cluster:
        target_scope = {"cluster_id": "prod-a", "service_id": "checkout"}
    created = client.post("/api/v1/cases", json={
        "title": "cross-feature-case",
        "problem_description": "checkout 服务延迟升高，请定位根因",
        "recovery_goal": "恢复延迟到正常",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": target_scope,
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _register_agent(agent_id: str = "agent-x") -> None:
    repo.register_agent(
        agent_id, f"node-{agent_id}", "192.168.60.10", version="0.3.0",
        capabilities=["sys_metrics", "process_scan", "service:checkout", "fault_domain:zone-a"],
    )


def _done_task(collector: str = "sys_metrics", *, agent_id: str = "agent-x") -> str:
    _register_agent(agent_id)
    task = repo.create_task(CreateTaskRequest(
        name=f"cross-{collector}",
        agent_id=agent_id,
        target_pid=1,
        collector_type=collector,
        sample_rate=11,
        duration_sec=10,
        options={"source": "cross-feature"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": collector,
        "metadata": {"samples": 20, "cpu_percent": 83},
    }])
    repo.transition_task(task.id, TaskStatus.RUNNING, "start", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "upload", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyze", Actor.WEB)
    repo.transition_task(task.id, TaskStatus.DONE, "done", Actor.AGENT)
    return task.id


# ── 1. 数据驱动入口 → Evidence → 解释不误采集 → 排除/恢复 → finish ──────

def test_data_driven_evidence_lifecycle_answer_only_and_restore(client: TestClient):
    task_id = _done_task()
    case = client.post("/api/v1/cases", json={
        "title": "evidence-lifecycle",
        "problem_description": "基于已有数据定位根因",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
        "initial_tasks": [task_id],
    })
    assert case.status_code == 200, case.text
    case = case.json()["data"]

    # 第一页入口已物化为 canonical Evidence
    evidence = client.get(f"/api/v1/cases/{case['case_id']}/evidence").json()["data"]
    assert evidence["total"] == 1
    evidence_id = evidence["items"][0]["evidence_id"]

    # 解释型追问不得创建 Plan/Task/Fanout
    turn = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/turn",
        json={"message": "为什么判断是 checkout？"},
    )
    assert turn.status_code == 200, turn.text
    data = turn.json()["data"]
    assert data["intent"] == "explain"
    assert data["side_effect_delta"] == {
        "plan_revision": 0,
        "plan_step_count": 0,
        "case_task_count": 0,
        "fanout_run_count": 0,
    }

    # 重复 @ 同一 Task 必须去重
    dup = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}], "purpose": "repeat"},
    )
    assert dup.json()["data"]["items"][0]["result"] == "DUPLICATE_SKIPPED"

    # 排除后 finish 拒绝引用；RESTORED 后再次接受
    excluded = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "EXCLUDED", "reason": "outlier"},
    )
    assert excluded.status_code == 200, excluded.text
    rejected = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "x", "evidence_ids": [evidence_id]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert rejected.status_code == 400
    restored = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "RESTORED", "reason": "误排除"},
    )
    assert restored.status_code == 200, restored.text
    accepted = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "CPU 饱和", "evidence_ids": [evidence_id]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert accepted.status_code == 200, accepted.text
    updated = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert updated["summary"]["current_finding"]["status"] == "concluded"


# ── 2. PI Turn → 内部 Query Tool → 原生 Task → Evidence → FollowUp → Stop ──

class PiLoopHandler(BaseHTTPRequestHandler):
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
                "runtime_session_id": "pi-loop-session",
                "runtime_generation": body["context"].get("runtime_generation") or 1,
                "status": "READY",
                "last_event_seq": 0,
                "last_context_snapshot_id": None,
                "lease_owner": "pi-loop",
            }})
        elif self.path.endswith("/turn"):
            self._respond({"ok": True, "data": {
                "turn_id": "turn-loop", "accepted": True, "mode": "pi", "detail": "ok",
            }})
        elif self.path.endswith("/follow-up"):
            self._respond({"ok": True, "data": {"accepted": True}})
        else:
            self._respond({"ok": True, "data": {"accepted": True}})


@contextmanager
def _pi_sidecar(monkeypatch):
    PiLoopHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), PiLoopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    reset_runtime()
    try:
        yield
    finally:
        server.shutdown()
        monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
        reset_runtime()


def test_pi_runtime_longitudinal_chain_with_follow_up_and_stop(client: TestClient, monkeypatch):
    import server.app.main as main_module

    _register_agent("agent-x")
    case = _create_case(client)
    with _pi_sidecar(monkeypatch):
        # 1. User turn goes through AgentRuntimePort and persists binding/turn.
        turn = client.post(
            f"/api/v1/cases/{case['case_id']}/agent/turn",
            json={"message": "请自行定位"},
        )
        assert turn.status_code == 200, turn.text
        assert turn.json()["data"]["status"] == "runtime_turn_accepted"
        assert repo.get_agent_runtime_binding(case["case_id"], "tenant-a") is not None

        # 2. Simulated Pi tool call through internal gateway creates a native task.
        query = client.post(
            "/internal/agent/tools/query",
            json={"case_id": case["case_id"], "operation": "process.list", "parameters": {}},
            headers={"X-Internal-Token": TOKEN},
        )
        assert query.status_code == 200, query.text
        task_id = query.json()["data"]["task"]["id"]

    # 3. Worker produces artifact and completes task.
    repo.add_artifacts(task_id, [{
        "artifact_type": "process_scan",
        "metadata": {"processes": [{"pid": 1, "comm": "init"}]},
    }])
    repo.transition_task(task_id, TaskStatus.RUNNING, "start", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "upload", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyze", Actor.WEB)
    repo.transition_task(task_id, TaskStatus.DONE, "done", Actor.AGENT)

    # 4. Wake path materializes canonical Evidence and calls Runtime.followUp.
    with _pi_sidecar(monkeypatch):
        main_module._wake_case_from_task(task_id, "DONE")
        followups = [item for item in PiLoopHandler.received if item[0].endswith("/follow-up")]
        assert followups, "task wake must follow-up the Pi runtime"
        evidence_ids = followups[-1][2]["evidence_ids"]
        assert evidence_ids

    evidence = client.get(f"/api/v1/cases/{case['case_id']}/evidence").json()["data"]
    assert {item["evidence_id"] for item in evidence["items"]} == set(evidence_ids)

    # 5. Create another active query then stop Case; stop cancels native Task.
    with _pi_sidecar(monkeypatch):
        query2 = client.post(
            "/internal/agent/tools/query",
            json={"case_id": case["case_id"], "operation": "system.metrics", "parameters": {}},
            headers={"X-Internal-Token": TOKEN},
        )
        assert query2.status_code == 200, query2.text
        task2_id = query2.json()["data"]["task"]["id"]
        case_row = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
        stopped = client.post(
            f"/api/v1/cases/{case['case_id']}/stop",
            json={"reason": "user stop", "expected_row_version": case_row["row_version"]},
        )
        assert stopped.status_code == 200, stopped.text
        assert str(repo.tasks[task2_id].status) == "CANCELLED"


# ── 3. Campaign → PlanDriver → Fanout → Cancel Step → stale revision ──

def test_campaign_fanout_cancel_and_stale_revision_isolation(client: TestClient):
    _register_agent("agent-a")
    repo.register_agent(
        "agent-b", "node-agent-b", "192.168.60.11", version="0.3.0",
        capabilities=["sys_metrics", "service:checkout", "fault_domain:zone-b"],
    )
    case = _create_case(client, cluster=True)
    campaign = client.post(
        f"/api/v1/cases/{case['case_id']}/campaigns",
        json={
            "goal": "异构集群调查",
            "expected_case_row_version": case["row_version"],
            "expected_scope_revision": case["scope_revision"],
            "expected_plan_revision": 0,
            "common_baseline": {
                "role": "all",
                "collector_id": "sys_metrics",
                "target_refs": ["cluster:prod-a"],
                "selection_strategy": "ALL_IN_SCOPE",
                "purpose": "共同基线",
                "priority": 90,
            },
            "assignments": [{
                "role": "gateway",
                "collector_id": "connection_probe",
                "target_refs": ["service:gateway"],
                "purpose": "网关探测",
                "priority": 60,
            }],
        },
    )
    assert campaign.status_code == 200, campaign.text
    plan = campaign.json()["data"]["plan"]
    baseline_step = next(item for item in plan["steps"] if item["collector_id"] == "sys_metrics")

    dispatched = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
    ).json()["data"]
    assert dispatched["outcome"] == "DISPATCHED"
    cluster_runs = [
        item for item in dispatched["dispatched"]
        if item["kind"] == "cluster"
    ]
    assert cluster_runs

    # 取消 cluster step 会同步取消其 Fanout 子任务
    cancelled = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{baseline_step['step_id']}/cancel",
        json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    run = repo.get_fanout_run(
        case["case_id"], "tenant-a", cluster_runs[0]["run_id"],
    )
    for task_id in run["task_ids"]:
        assert str(repo.tasks[task_id].status) == "CANCELLED"

    # 旧 plan revision 不能覆盖当前 plan
    stale = client.put(
        f"/api/v1/cases/{case['case_id']}/plans",
        json={
            "goal": "stale rewrite",
            "expected_case_row_version": case["row_version"],
            "expected_scope_revision": case["scope_revision"],
            "expected_plan_revision": 0,
            "steps": [],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("STALE_PLAN")


# ── 4. Runtime 事件清洗与去重 ──

def test_runtime_event_ingestion_drops_thinking_and_deduplicates(client: TestClient):
    case = _create_case(client)
    repo.upsert_agent_runtime_binding(
        case["case_id"],
        "tenant-a",
        runtime_type="pi",
        runtime_version="pi-0.83.0",
        runtime_session_id="session-events",
        runtime_generation=1,
        status="READY",
    )
    headers = {"X-Internal-Token": TOKEN}
    payload = {
        "runtime_generation": 1,
        "events": [
            {
                "event_id": "evt-thinking",
                "event_seq": 1,
                "event_type": "thinking.private",
                "payload": {"secret": "must-not-persist"},
                "idempotency_key": "think-1",
            },
            {
                "event_id": "evt-final",
                "event_seq": 2,
                "event_type": "turn_end",
                "payload": {"message": "{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"ok\"}]}"},
                "idempotency_key": "final-1",
            },
        ],
    }
    first = client.post(
        f"/internal/runtime/v1/cases/{case['case_id']}/events",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        f"/internal/runtime/v1/cases/{case['case_id']}/events",
        json=payload,
        headers=headers,
    )
    assert replay.status_code == 200, replay.text

    events = repo.list_agent_runtime_events(case["case_id"], "tenant-a")
    assert [item["event_type"] for item in events] == ["turn_end"]
    assert "thinking" not in json.dumps(events, default=str)
    binding = repo.get_agent_runtime_binding(case["case_id"], "tenant-a")
    assert binding["last_event_seq"] == 2


# ── 5. Query idempotency + 危险参数拒绝 ──

def test_query_idempotency_and_dangerous_parameter_rejection(client: TestClient):
    _register_agent()
    case = _create_case(client)
    first = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={
            "operation": "process.list",
            "parameters": {},
            "idempotency_key": "same-query",
        },
    )
    assert first.status_code == 200, first.text
    first_id = first.json()["data"]["task"]["id"]
    second = client.post(
        f"/api/v1/cases/{case['case_id']}/queries",
        json={
            "operation": "process.list",
            "parameters": {},
            "idempotency_key": "same-query",
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["task"]["id"] == first_id

    for bad in (
        {"operation": "system.metrics", "parameters": {"shell": "/bin/sh"}},
        {"operation": "system.metrics", "parameters": {"executable": "bash"}},
        {"operation": "system.metrics", "parameters": {"unknown": 1}},
    ):
        resp = client.post(
            f"/api/v1/cases/{case['case_id']}/queries",
            json=bad,
        )
        assert resp.status_code == 400, resp.text
