"""G7: deterministic Skill selection and Knowledge projection contracts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis.investigation_directive import build_directive
from server.app.diagnosis.knowledge import retrieve_knowledge
from server.app.diagnosis.skill_registry import SKILL_REGISTRY
from server.app.main import app
from server.app.models import Base


def test_skill_registry_selects_cpu_skill_for_cpu_goal():
    skills = SKILL_REGISTRY.select_skills(
        goal="checkout 服务 CPU 飙高，火焰图热点集中在序列化",
        target_scope={"service_id": "checkout"},
    )
    ids = [item["skill_id"] for item in skills]
    assert ids[0] == "answer_stability"
    assert "linux_cpu_diagnosis" in ids
    assert "linux_memory_diagnosis" not in ids


def test_skill_registry_negative_triggers_do_not_fire():
    skills = SKILL_REGISTRY.select_skills(
        goal="JVM 内存泄漏导致 RSS 持续增长",
        target_scope={"service_id": "jvm-svc"},
    )
    ids = [item["skill_id"] for item in skills]
    assert "linux_memory_diagnosis" in ids
    assert "linux_cpu_diagnosis" not in ids


def test_knowledge_retrieval_returns_documents_not_evidence():
    items = retrieve_knowledge("CPU 用户态高，如何判断？", [])
    assert items
    assert all(item["document"] for item in items)
    assert all("required_evidence" in item for item in items)


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


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


def test_runtime_case_context_excludes_legacy_skill_and_knowledge_rules(client: TestClient):
    created = client.post("/api/v1/cases", json={
        "title": "skill-knowledge-context",
        "problem_description": "checkout 服务 CPU 飙高，火焰图显示热点集中在序列化",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert created.status_code == 200, created.text
    case = created.json()["data"]
    from server.app.main import _build_runtime_case_context
    snapshot = _build_runtime_case_context(case, "tenant-a")
    assert snapshot.hypotheses == []
    assert snapshot.skill_context == []
    assert snapshot.knowledge_context == []
    assert snapshot.investigation_directive["kind"] == "EVIDENCE_NATIVE_COLLECTOR_AGENT"
    assert "no_new_evidence_after_two_cycles" in snapshot.investigation_directive["stop_conditions"]
    assert snapshot.budget["max_collection_requests"] == 8
    assert snapshot.budget["max_collection_duration_sec"] == 240


def test_directive_is_stable_across_time_windows_for_same_unresolved_issue():
    skills = SKILL_REGISTRY.select_skills(
        goal="checkout 服务延迟升高", target_scope={"service_id": "checkout"},
    )
    d1 = build_directive(
        goal="2026-08-01 10:00 checkout 服务延迟升高",
        target_scope={"service_id": "checkout"},
        skill_context=skills,
    )
    d2 = build_directive(
        goal="2026-08-09 22:30 checkout 服务延迟升高",
        target_scope={"service_id": "checkout"},
        skill_context=skills,
    )
    assert d1.directive_key == d2.directive_key
    assert d1.next_action is None and d2.next_action is None
    assert d1.answer_policy == d2.answer_policy == "evidence_driven_free_within_policy"
    assert d1.evidence_order == d2.evidence_order == []


def test_directive_changes_only_with_evidence_or_scope():
    skills = SKILL_REGISTRY.select_skills(
        goal="checkout 服务延迟升高", target_scope={"service_id": "checkout"},
    )
    no_evidence = build_directive(
        goal="checkout 服务延迟升高", target_scope={"service_id": "checkout"},
        skill_context=skills,
    )
    with_evidence = build_directive(
        goal="checkout 服务延迟升高", target_scope={"service_id": "checkout"},
        evidence_summary=[{"artifact_type": "sys_metrics"}],
        skill_context=skills,
    )
    assert no_evidence.next_action is None
    assert with_evidence.next_action is None
    assert no_evidence.collected_evidence_types == []
    assert with_evidence.collected_evidence_types == ["sys_metrics"]
