"""P7 生产治理测试：Red Button、影子模式、Capability Key 轮换。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis.autonomous_agent import (
    AgentCallbacks,
    AutonomousIncidentAgent,
)
from server.app.diagnosis.case_supervisor import CaseSupervisor
from server.app.diagnosis.governance import (
    CAPABILITY_EPOCH,
    RED_BUTTON,
    current_capability_epoch,
    issue_capability_key,
)
from server.app.main import app, repo
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


def _create_case(**overrides) -> str:
    payload = {
        "tenant_id": "tenant-a",
        "created_by": "test-user",
        "title": "checkout 延迟事故",
        "problem_description": "checkout 延迟",
        "recovery_goal": "p95 恢复",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    }
    payload.update(overrides)
    return repo.create_incident_case(payload)["case_id"]


class _FakeAgent:
    def __init__(self):
        self.steps = 0

    def step(self, case_id, tenant_id):
        self.steps += 1
        return {"outcome": "DIAGNOSING", "loop": {}}


def test_red_button_stops_advance():
    case_id = _create_case()
    agent = _FakeAgent()
    supervisor = CaseSupervisor(repo, agent, None, lease_ttl_seconds=60)
    repo.set_system_control(RED_BUTTON, enabled=True)
    outcomes = supervisor.scan_and_advance("tenant-a")
    assert any(item["outcome"] == "RED_BUTTON_ACTIVE" for item in outcomes)
    assert agent.steps == 0, "Red Button 激活时不得推进自治循环"
    repo.set_system_control(RED_BUTTON, enabled=False)
    supervisor.scan_and_advance("tenant-a")
    assert agent.steps == 1


def test_capability_epoch_rotation():
    assert current_capability_epoch(repo) == 0
    repo.set_system_control(CAPABILITY_EPOCH, enabled=True, value={"epoch": 3})
    assert current_capability_epoch(repo) == 3
    key = issue_capability_key(repo, principal_id="ops", source_ids=["prometheus-metrics"])
    assert key["capability_epoch"] == 3


def test_case_detail_includes_agent_progress(client):
    case_id = _create_case()
    resp = client.get(f"/api/v1/cases/{case_id}").json()["data"]
    progress = resp.get("agent_progress") or {}
    assert "phase" in progress
    assert "verification_progress" in progress
    assert "phase_label" in progress
    assert progress["verification_progress"] == 0.0


def test_controls_api_list_and_set(client):
    created = client.get("/api/v1/controls").json()["data"]
    assert isinstance(created, list)
    resp = client.post("/api/v1/controls/red_button", json={"enabled": True}).json()["data"]
    assert resp["enabled"] is True
    controls = client.get("/api/v1/controls").json()["data"]
    assert any(item["control_name"] == "red_button" and item["enabled"] for item in controls)


def test_shadow_mode_skips_execution():
    case = _create_case(target_scope={
        "service_id": "checkout",
        "autonomy_policy": {
            "allowed_action_ids": ["swarm.restart-stateless-service"],
            "max_auto_impact": "I2",
            "stable_verification_count": 2,
            "max_iterations": 4,
            "max_actions": 1,
            "shadow": True,
        },
        "orchestration": {"swarm_service": "shop_checkout", "replicas": 1},
    })

    class Repo:
        def __init__(self):
            self.case = repo.get_incident_case(case, "tenant-a")
            self.case["diagnosis_session_id"] = "diag-1"
            self.events = []

        def get_incident_case(self, *_):
            return self.case

        def update_case_agent_loop(self, *_args, loop, event_type, detail, **_kwargs):
            self.case["recovery"] = {"agent_loop": loop.copy()}
            self.events.append(event_type)
            return self.case

        def transition_incident_case(self, *_args, **_kwargs):
            return self.case

    r = Repo()

    class Gateway:
        def dry_run(self, action_id, parameters):
            return {"attempt_id": "dry", "dry_run": {}}

        def execute(self, action_id, attempt_id, environment):
            return {"attempt_id": attempt_id, "stage": "COMPLETED"}

    class Orchestrator:
        def get(self, diagnosis_id, advance=True):
            return {
                "diagnosis_id": diagnosis_id,
                "status": "COMPLETED",
                "latest_conclusion": {
                    "cluster_assessment": {"classification": "runtime_lock_contention"},
                    "evidence_review": {"quality_gate_passed": True, "conflicts": []},
                },
            }

    agent = AutonomousIncidentAgent(r, Orchestrator(), Gateway(), AgentCallbacks(
        start_diagnosis=lambda case: {"diagnosis": {"diagnosis_id": "diag-1"}},
        verify_recovery=lambda case, did: {"status": "recovered"},
    ))
    result = agent.step(case, "tenant-a")
    assert result["outcome"] == "SHADOW_SKIPPED_EXECUTION"
