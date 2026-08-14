"""E3: Shadow Plan generation and pairwise comparison."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app.agent_runtime.shadow import (
    build_deterministic_plan,
    compare_plans,
    plan_signature,
)
from server.app.diagnosis.investigation_plan import PlanStepInput, PlanUpdateInput
from server.app.database import init_db, reset_engine
from server.app.main import app
from server.app.models import Base

import pytest


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
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


def test_deterministic_plan_builds_read_low_steps():
    plan = build_deterministic_plan(
        {"problem_description": "支付接口超时", "row_version": 0, "scope_revision": 1},
        active_hypotheses=[{"hypothesis_id": "hyp_cpu"}],
        probe_candidates=[
            {"collector_id": "sys_metrics", "rationale": "基础指标", "risk": "READ_LOW", "priority": 60},
            {"collector_id": "perf_cpu", "rationale": "CPU 热点", "risk": "READ_LOW", "priority": 80},
        ],
    )
    assert plan.source == "deterministic"
    assert len(plan.steps) == 2
    assert all(step.risk == "READ_LOW" for step in plan.steps)


def test_compare_plans_reports_collector_deltas():
    deterministic = PlanUpdateInput(
        goal="定位", source="deterministic",
        steps=[PlanStepInput(collector_id="sys_metrics", risk="READ_LOW", status="QUEUED")],
    )
    shadow = PlanUpdateInput(
        goal="定位", source="pi_shadow",
        steps=[
            PlanStepInput(collector_id="sys_metrics", risk="READ_LOW", status="QUEUED"),
            PlanStepInput(collector_id="log_scan", risk="READ_LOW", status="QUEUED"),
        ],
    )
    comparison = compare_plans(deterministic, shadow)
    assert comparison["shadow_available"] is True
    assert comparison["collectors_identical"] is False
    assert comparison["shadow_only_collectors"] == ["log_scan"]
    assert comparison["deterministic_only_collectors"] == []


def test_compare_plans_without_shadow_reports_unavailable():
    deterministic = build_deterministic_plan(
        {"problem_description": "x", "row_version": 0, "scope_revision": 1},
        active_hypotheses=[], probe_candidates=[{"collector_id": "sys_metrics"}],
    )
    comparison = compare_plans(deterministic, None)
    assert comparison["shadow_available"] is False


def test_shadow_plan_endpoint_runs_in_deterministic_mode(client: TestClient):
    created = client.post("/api/v1/cases", json={
        "title": "shadow-plan-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    case_id = created.json()["data"]["case_id"]
    resp = client.post(f"/api/v1/cases/{case_id}/agent/shadow-plan", json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["shadow_available"] is False  # deterministic 模式无 Shadow
    assert data["deterministic"]["step_count"] >= 1
