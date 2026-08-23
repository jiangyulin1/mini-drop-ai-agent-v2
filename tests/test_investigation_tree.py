"""Contracts for the durable tree projection and optional graph adapter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.agent_runtime.langgraph_adapter import (
    LangGraphRuntimeContext,
    LangGraphUnavailable,
    langgraph_available,
)


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-tree")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "tree-token")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "tree case",
        "problem_description": "CPU anomaly",
        "recovery_goal": "understand CPU anomaly",
        "run_mode": "COLLABORATE",
        "environment": "test",
        "target_scope": {"service_id": "checkout"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_evidence_invalidation_abandons_all_descendants(client: TestClient):
    case = _case(client)
    case_id = case["case_id"]
    repo.upsert_case_evidence(
        case_id=case_id, tenant_id="tenant-tree", evidence_id="ev-tree",
        attachment_id=None, task_id="task-tree", artifact_id=1,
        artifact_type="sys_metrics", collector_id="sys_metrics",
        source_type="task_artifact", target_ref="service:checkout",
        content_hash="tree-content", projection_hash="tree-projection",
    )
    run = repo.create_investigation_run(case_id=case_id, tenant_id="tenant-tree")
    root = repo.create_investigation_tree_node(
        case_id=case_id, tenant_id="tenant-tree", run_id=run["run_id"],
        node_type="HYPOTHESIS", statement="CPU saturation", node_id="tree-root",
    )
    child = repo.create_investigation_tree_node(
        case_id=case_id, tenant_id="tenant-tree", run_id=run["run_id"],
        node_type="OBLIGATION", statement="check scheduler", parent_node_id=root["node_id"],
        node_id="tree-child",
    )
    repo.create_investigation_tree_node(
        case_id=case_id, tenant_id="tenant-tree", run_id=run["run_id"],
        node_type="CLAIM", statement="scheduler explains CPU", parent_node_id=child["node_id"],
        node_id="tree-grandchild",
    )
    repo.add_investigation_tree_dependency(
        case_id=case_id, tenant_id="tenant-tree", node_id=root["node_id"],
        target_kind="EVIDENCE", target_id="ev-tree",
    )

    result = repo.invalidate_investigation_tree_for_evidence(
        case_id=case_id, tenant_id="tenant-tree", evidence_id="ev-tree",
    )
    assert result["invalidated_nodes"] == ["tree-root"]
    assert result["abandoned_nodes"] == ["tree-child", "tree-grandchild"]
    tree = repo.list_investigation_tree(case_id, "tenant-tree", run_id=run["run_id"])
    statuses = {item["node_id"]: item["status"] for item in tree["nodes"]}
    assert statuses == {
        "tree-root": "INVALIDATED",
        "tree-child": "ABANDONED",
        "tree-grandchild": "ABANDONED",
    }


def test_agent_cycle_has_a_durable_tree_root(client: TestClient):
    case = _case(client)
    run = repo.create_investigation_run(case_id=case["case_id"], tenant_id="tenant-tree")
    cycle = repo.create_agent_cycle(
        case_id=case["case_id"], tenant_id="tenant-tree", run_id=run["run_id"],
        trigger_type="EVIDENCE_COMMITTED", trigger_ref="evidence:ev-cycle",
    )
    tree = repo.list_investigation_tree(case["case_id"], "tenant-tree", run_id=run["run_id"])
    roots = [item for item in tree["nodes"] if item["node_id"] == cycle["tree_root_node_id"]]
    assert len(roots) == 1
    assert roots[0]["node_type"] == "CYCLE"
    assert roots[0]["metadata"]["cycle_id"] == cycle["cycle_id"]


def test_evidence_review_exclusion_invalidates_tree_in_same_write(client: TestClient):
    case = _case(client)
    case_id = case["case_id"]
    repo.upsert_case_evidence(
        case_id=case_id, tenant_id="tenant-tree", evidence_id="ev-review-tree",
        attachment_id=None, task_id="task-review-tree", artifact_id=1,
        artifact_type="sys_metrics", collector_id="sys_metrics",
        source_type="task_artifact", target_ref="service:checkout",
        content_hash="review-content", projection_hash="review-projection",
    )
    run = repo.create_investigation_run(case_id=case_id, tenant_id="tenant-tree")
    root = repo.create_investigation_tree_node(
        case_id=case_id, tenant_id="tenant-tree", run_id=run["run_id"],
        node_type="HYPOTHESIS", statement="reviewable CPU cause", node_id="review-root",
    )
    repo.add_investigation_tree_dependency(
        case_id=case_id, tenant_id="tenant-tree", node_id=root["node_id"],
        target_kind="EVIDENCE", target_id="ev-review-tree",
    )
    preview = repo.preview_evidence_review(
        case_id=case_id, tenant_id="tenant-tree", evidence_id="ev-review-tree",
        decision="EXCLUDED", assessment={},
    )
    applied = repo.apply_evidence_review(
        case_id=case_id, tenant_id="tenant-tree", evidence_id="ev-review-tree",
        decision="EXCLUDED", assessment={}, reason_code="SCOPE_MISMATCH",
        reason="test exclusion", override_reason=None,
        expected_review_revision=preview["current_review_revision"],
        impact_token=preview["impact_token"], actor_id="operator",
    )
    assert applied["tree_propagation"]["invalidated_nodes"] == ["review-root"]
    assert repo.get_investigation_tree_node(case_id, "tenant-tree", "review-root")["status"] == "INVALIDATED"


def test_langgraph_context_is_branch_scoped_and_optional():
    context = LangGraphRuntimeContext(
        case_id="case-1", run_id="run-1", branch_id="branch-2", node_id="node-3",
        visible_evidence_ids=("ev-2", "ev-1", "ev-1"),
    )
    assert context.config()["configurable"]["thread_id"] == "case:case-1:run:run-1:branch:branch-2"
    state = context.initial_state(obligations=[{"id": "obligation-1"}])
    assert state["visible_evidence_ids"] == ["ev-1", "ev-2"]
    if not langgraph_available():
        from server.app.agent_runtime.langgraph_adapter import LangGraphInvestigationAdapter

        with pytest.raises(LangGraphUnavailable):
            LangGraphInvestigationAdapter().compile()


def test_tree_tool_gateway_requires_explicit_node_and_dependency(client: TestClient):
    case = _case(client)
    run = repo.create_investigation_run(case_id=case["case_id"], tenant_id="tenant-tree")
    headers = {"X-Internal-Token": "tree-token"}
    created = client.post(
        "/internal/agent/tools/investigation-tree/node",
        headers=headers,
        json={
            "case_id": case["case_id"], "run_id": run["run_id"],
            "node_type": "HYPOTHESIS", "statement": "CPU saturation",
            "node_id": "ignored-by-gateway",
        },
    )
    assert created.status_code == 200, created.text
    node_id = created.json()["data"]["node"]["node_id"]
    dependency = client.post(
        "/internal/agent/tools/investigation-tree/dependency",
        headers=headers,
        json={
            "case_id": case["case_id"], "node_id": node_id,
            "target_kind": "HYPOTHESIS", "target_id": "hyp-cpu",
        },
    )
    assert dependency.status_code == 200, dependency.text
    read = client.post(
        "/internal/agent/tools/investigation-tree",
        headers=headers,
        json={"case_id": case["case_id"], "run_id": run["run_id"]},
    )
    assert read.status_code == 200, read.text
    assert read.json()["data"]["tree"]["nodes"][0]["statement"] == "CPU saturation"
