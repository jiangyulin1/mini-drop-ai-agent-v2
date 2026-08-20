"""E3: internal Tool Gateway — token gate, read-only projections, STALE_PLAN."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base

TOKEN = "test-internal-token"


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
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


def _headers() -> dict:
    return {"X-Internal-Token": TOKEN}


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "tool-gateway-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def test_internal_tool_requires_token(client: TestClient):
    resp = client.post("/internal/agent/tools/case-snapshot", json={"case_id": "x"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "INTERNAL_TOKEN_REQUIRED"


def test_internal_catalog_is_authenticated_and_canonical(client: TestClient):
    denied = client.get("/internal/agent/tools/catalog")
    assert denied.status_code == 401
    response = client.get("/internal/agent/tools/catalog", headers=_headers())
    assert response.status_code == 200
    catalog = response.json()["data"]
    assert catalog["schema_version"] == "tool-catalog.v1"
    names = {item["name"] for item in catalog["tools"]}
    assert len(names) == 12
    assert {"get_case_snapshot", "propose_collection", "submit_evidence_analysis", "finish_investigation"} <= names
    assert {"evaluate_hypotheses", "rca_candidate_analysis", "request_operation", "propose_plan_revision"}.isdisjoint(names)


def test_public_runtime_config_exposes_safe_strategy_and_schema_summaries(client: TestClient):
    response = client.get("/api/v1/agent-runtime/config")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "deterministic"
    assert data["ready"] is True
    assert data["ai_ready"] is False
    assert data["ai_status"] == "NOT_CONFIGURED"
    assert {item["strategy_id"] for item in data["available_strategies"]} == {"hybrid"}
    assert len(data["tool_catalog"]["tools"]) == 12
    assert all("internal_path" not in item for item in data["tool_catalog"]["tools"])
    assert data["runtime_policy_schema"]["title"] == "RuntimePolicy"
    assert data["runtime_options_schema"]["title"] == "RuntimeOptions"


def test_runtime_policy_can_remove_proposal_tools_at_gateway(client: TestClient):
    case = _create_case(client)
    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-a", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "runtime_policy": {"side_effect_policy": "READ_ONLY"},
        },
        headers=_headers(),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "TURN_READ_ONLY"


def test_internal_case_snapshot_returns_projection(client: TestClient):
    case = _create_case(client)
    resp = client.post(
        "/internal/agent/tools/case-snapshot",
        json={"case_id": case["case_id"]},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["case_id"] == case["case_id"]
    assert "plan" in data and "attachments" in data
    assert isinstance(data.get("evidence"), list)
    assert "process.list" in data.get("query_operations", [])


def test_collection_proposal_requires_scope_fence(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-collector", "node-a", "192.168.9.10", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    ok = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-collector", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
        },
        headers=_headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["proposal"]["status"] == "ACCEPTED"
    assert ok.json()["data"]["collection_request"]["status"] == "DISPATCHED"
    assert ok.json()["data"]["task"]["collector_type"] == "sys_metrics"

    stale = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-collector", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"] + 1,
        },
        headers=_headers(),
    )
    assert stale.status_code == 409
    assert "STALE_SCOPE_REVISION" in stale.json()["detail"]


def test_duplicate_collection_proposal_reuses_request_without_budget(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-dedupe", "node-a", "192.168.9.12", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    payload = {
        "case_id": case["case_id"],
        "collector_id": "sys_metrics",
        "target_selector": {"agent_id": "agent-dedupe", "target_pid": 1},
        "parameters": {"duration_sec": 15},
        "information_goal": "主机和目标进程资源饱和度",
    }
    first = client.post(
        "/internal/agent/tools/collection-proposal", json=payload, headers=_headers(),
    )
    duplicate = client.post(
        "/internal/agent/tools/collection-proposal", json=payload, headers=_headers(),
    )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    first_data = first.json()["data"]
    duplicate_data = duplicate.json()["data"]
    assert duplicate_data["collection_request"]["collection_request_id"] == first_data["collection_request"]["collection_request_id"]
    assert duplicate_data["task"]["id"] == first_data["task"]["id"]
    validation = duplicate_data["proposal"]["validation_result"]
    assert validation["duplicate"] is True
    assert validation["budget_consumed"] is False
    assert len(repo.list_collection_requests(case["case_id"], "tenant-a")) == 1


def test_collection_request_count_budget_is_a_hard_limit(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-count-budget", "node-a", "192.168.9.13", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    for index in range(2):
        response = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"],
                "collector_id": "sys_metrics",
                "target_selector": {"agent_id": "agent-count-budget", "target_pid": 1},
                "parameters": {"duration_sec": 15, "sample_rate": index + 1},
                "information_goal": "主机和目标进程资源饱和度",
                "runtime_policy": {"max_collection_requests": 1},
            },
            headers=_headers(),
        )
        if index == 0:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 409
            assert "COLLECTION_REQUEST_COUNT_BUDGET_EXHAUSTED" in response.json()["detail"]
    assert len(repo.list_collection_requests(case["case_id"], "tenant-a")) == 1


def test_collection_duration_budget_is_a_hard_limit(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-duration-budget", "node-a", "192.168.9.14", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    for sample_rate in (1, 2):
        response = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"],
                "collector_id": "sys_metrics",
                "target_selector": {"agent_id": "agent-duration-budget", "target_pid": 1},
                "parameters": {"duration_sec": 15, "sample_rate": sample_rate},
                "information_goal": "主机和目标进程资源饱和度",
                "runtime_policy": {"max_collection_duration_sec": 20},
            },
            headers=_headers(),
        )
        if sample_rate == 1:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 409
            assert "COLLECTION_REQUEST_DURATION_BUDGET_EXHAUSTED" in response.json()["detail"]
    assert len(repo.list_collection_requests(case["case_id"], "tenant-a")) == 1


def test_runtime_policy_cannot_expand_collection_budget():
    from pydantic import ValidationError

    from server.app.agent_runtime.policy import RuntimePolicy

    with pytest.raises(ValidationError):
        RuntimePolicy(max_collection_requests=9)
    with pytest.raises(ValidationError):
        RuntimePolicy(max_collection_duration_sec=241)


def test_propose_only_collection_can_be_approved_without_regenerating_proposal(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-human-approval", "node-a", "192.168.9.15", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    proposed = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"], "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-human-approval", "target_pid": 1},
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
            "idempotency_key": "human-approved-collection",
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers=_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()["data"]["proposal"]
    assert proposal["status"] == "PROPOSED"
    assert proposal["validation_result"]["awaiting_execution_authority"] is True
    assert proposed.json()["data"]["task"] is None

    approved = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{proposal['proposal_id']}/decision",
        json={
            "decision": "APPROVE", "reason": "read-only collection approved",
            "expected_control_revision": case["control_revision"],
            "expected_scope_revision": case["scope_revision"],
        },
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert data["proposal"]["status"] == "ACCEPTED"
    assert data["proposal"]["validation_result"]["approval_decision"] == "APPROVE"
    assert data["collection_request"]["status"] == "DISPATCHED"
    assert data["task"]["collector_type"] == "sys_metrics"
    assert len(repo.list_collection_proposals(case["case_id"], "tenant-a")) == 1


def test_pending_collection_reject_and_revision_fence(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-human-reject", "node-a", "192.168.9.16", version="0.3.0",
        capabilities=["sys_metrics"],
    )

    def propose(key: str) -> dict:
        response = client.post(
            "/internal/agent/tools/collection-proposal",
            json={
                "case_id": case["case_id"], "collector_id": "sys_metrics",
                "target_selector": {"agent_id": "agent-human-reject", "target_pid": 1},
                "parameters": {"duration_sec": 15},
                "information_goal": "主机和目标进程资源饱和度",
                "idempotency_key": key,
                "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
            },
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]["proposal"]

    rejected_proposal = propose("human-rejected-collection")
    rejected = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{rejected_proposal['proposal_id']}/decision",
        json={"decision": "REJECT", "reason": "not needed"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["proposal"]["status"] == "REJECTED"
    assert rejected.json()["data"]["collection_request"] is None

    fenced_proposal = propose("revision-fenced-collection")
    fenced = client.post(
        f"/api/v1/cases/{case['case_id']}/collection-proposals/{fenced_proposal['proposal_id']}/decision",
        json={
            "decision": "APPROVE",
            "expected_control_revision": case["control_revision"] + 1,
            "expected_scope_revision": case["scope_revision"],
        },
    )
    assert fenced.status_code == 409
    assert fenced.json()["detail"] == "APPROVAL_CONTROL_REVISION_MISMATCH"
    assert repo.get_collection_proposal(
        fenced_proposal["proposal_id"], case["case_id"], "tenant-a",
    )["status"] == "PROPOSED"

    current_state_proposal = propose("current-state-fenced-collection")
    original_get_case = repo.get_incident_case

    def changed_scope(case_id: str, tenant_id: str):
        current = original_get_case(case_id, tenant_id)
        return {**current, "scope_revision": int(current["scope_revision"]) + 1}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(repo, "get_incident_case", changed_scope)
    try:
        stale_current = client.post(
            f"/api/v1/cases/{case['case_id']}/collection-proposals/{current_state_proposal['proposal_id']}/decision",
            json={
                "decision": "APPROVE",
                "expected_control_revision": case["control_revision"],
                "expected_scope_revision": case["scope_revision"],
            },
        )
    finally:
        monkeypatch.undo()
    assert stale_current.status_code == 409
    assert "COLLECTION_PROPOSAL_FENCED:STALE_SCOPE_REVISION" == stale_current.json()["detail"]
    assert repo.get_collection_proposal(
        current_state_proposal["proposal_id"], case["case_id"], "tenant-a",
    )["status"] == "REJECTED"


def test_collection_proposal_is_not_accepted_when_task_dispatch_fails(
    client: TestClient, monkeypatch,
):
    case = _create_case(client)
    repo.register_agent(
        "agent-dispatch-fail", "node-a", "192.168.9.17", version="0.3.0",
        capabilities=["sys_metrics"],
    )

    def fail_create_task(*args, **kwargs):
        raise RuntimeError("simulated dispatcher failure")

    monkeypatch.setattr(repo, "create_task", fail_create_task)
    response = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"], "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-dispatch-fail", "target_pid": 1},
            "parameters": {"duration_sec": 15},
            "information_goal": "主机和目标进程资源饱和度",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "COLLECTION_TASK_DISPATCH_FAILED"
    proposal = repo.list_collection_proposals(case["case_id"], "tenant-a")[-1]
    collection_request = repo.list_collection_requests(case["case_id"], "tenant-a")[-1]
    assert proposal["status"] == "FAILED"
    assert collection_request["status"] == "DISPATCH_FAILED"
    assert collection_request["task_id"] is None


def test_internal_finish_requires_evidence_refs(client: TestClient):
    case = _create_case(client)
    missing = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "evidence_ids": []},
        headers=_headers(),
    )
    assert missing.status_code == 400
    assert missing.json()["detail"] == "NO_EVIDENCE_REFS"
    unknown = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "evidence_ids": ["ev-1"]},
        headers=_headers(),
    )
    assert unknown.status_code == 400
    assert unknown.json()["detail"].startswith("INVALID_EVIDENCE_REFS")


def test_internal_finish_accepts_known_evidence_refs(client: TestClient):
    case = _create_case(client)
    repo.upsert_case_attachment(
        case["case_id"],
        "tenant-a",
        {
            "attachment_id": "attach-valid",
            "resource_type": "task",
            "resource_id": "task-valid",
            "label": "valid task",
            "source": "user_mention",
            "status": "ACCEPTED",
            "evidence_ids": ["ev-valid"],
        },
    )
    ok = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "根因是 CPU 饱和",
            "evidence_ids": ["ev-valid"],
        },
        headers=_headers(),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["accepted"] is True
    events = client.get(f"/api/v1/cases/{case['case_id']}/events").json()["data"]["items"]
    assert events[-1]["event_type"] == "agent_finish_investigation"
    updated = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert updated["summary"]["current_finding"]["status"] == "concluded"
    assert updated["summary"]["current_finding"]["evidence_refs"] == ["ev-valid"]


def test_tool_policy_error_enforces_needs_approval(monkeypatch):
    from server.app.agent_runtime.catalog import ToolSpec
    from server.app.agent_runtime.policy import RuntimePolicy
    from server.app.diagnosis import v6_policy

    fake_spec = ToolSpec(
        name="propose_collection",
        description="sensitive operation request",
        parameters={"type": "object"},
        internal_path="/internal/agent/tools/collection-proposal",
        policy="PROPOSE_ONLY",
        needs_approval=True,
    )
    monkeypatch.setattr(v6_policy, "get_tool_spec", lambda name: fake_spec if name == "propose_collection" else None)

    policy = RuntimePolicy(side_effect_policy="PROPOSE_ONLY")
    assert v6_policy.tool_policy_error("propose_collection", policy) == "TOOL_REQUIRES_APPROVAL"

    auto = RuntimePolicy(side_effect_policy="PROPOSE_ONLY", auto_approve=True)
    assert v6_policy.tool_policy_error("propose_collection", auto) is None


def test_operation_risk_in_require_approval_for_is_rejected_at_gateway(client: TestClient):
    case = _create_case(client)
    repo.register_agent(
        "agent-approval", "node-a", "192.168.9.11", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    resp = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            "case_id": case["case_id"],
            "collector_id": "sys_metrics",
            "target_selector": {"agent_id": "agent-approval", "target_pid": 1},
            "parameters": {},
            "information_goal": "主机和目标进程资源饱和度",
            "runtime_policy": {
                "side_effect_policy": "AUTO_READ_LOW",
                "allowed_risk_levels": ["R1"],
                "require_approval_for": ["R1"],
                "auto_approve": False,
            },
        },
        headers=_headers(),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "COLLECTOR_REQUIRES_APPROVAL"


def test_finish_autofills_single_projection_hash_when_claim_omits_it(client: TestClient):
    case = _create_case(client)
    repo.upsert_case_evidence(
        case_id=case["case_id"],
        tenant_id="tenant-a",
        evidence_id="ev-auto-proj",
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="sys_metrics",
        collector_id="sys_metrics",
        source_type="task_artifact",
        target_ref="task:auto",
        content_hash="content-hash",
        projection_hash="will-be-replaced",
        time_window={},
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-auto-proj",
        case_id=case["case_id"],
        tenant_id="tenant-a",
        projection_kind="TOP_ITEMS",
        content={"summary": "cpu 100%"},
    )
    resp = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "结论：CPU 热点",
            "evidence_ids": ["ev-auto-proj"],
            "claims": [{"evidence_id": "ev-auto-proj"}],
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accepted"] is True


def test_finish_accepts_model_friendly_claim_shapes(client: TestClient):
    case = _create_case(client)
    repo.upsert_case_evidence(
        case_id=case["case_id"],
        tenant_id="tenant-a",
        evidence_id="ev-friendly",
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="sys_metrics",
        collector_id="sys_metrics",
        source_type="task_artifact",
        target_ref="task:friendly",
        content_hash="content-hash",
        projection_hash="will-be-replaced",
        time_window={},
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-friendly",
        case_id=case["case_id"],
        tenant_id="tenant-a",
        projection_kind="TOP_ITEMS",
        content={"summary": "cpu 100%"},
    )
    resp = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "结论：CPU 热点",
            "evidence_ids": ["ev-friendly"],
            "claims": [
                {"claim": "CPU hotspot", "confidence": 0.9, "supporting_evidence": ["ev-friendly"]},
                {"evidence_ids": ["ev-friendly"], "text": "user-mode spin"},
                {"evidence": "ev-friendly"},
            ],
        },
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["accepted"] is True
