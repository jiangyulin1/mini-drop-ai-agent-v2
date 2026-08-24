"""Supervised investigation state is evidence-bound and visible in Workspace."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base


TOKEN = "investigation-state-token"


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


def _headers() -> dict[str, str]:
    return {"X-Internal-Token": TOKEN}


def _case_and_evidence(client: TestClient) -> tuple[dict, str]:
    response = client.post("/api/v1/cases", json={
        "title": "supervised-investigation",
        "problem_description": "checkout latency increased",
        "recovery_goal": "identify and verify the causal mechanism",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert response.status_code == 200, response.text
    case = response.json()["data"]
    evidence_id = "ev-investigation-state"
    content = {"summary": "CPU saturated", "signals": {"cpu_percent": 96}}
    projection = repo.upsert_evidence_projection(
        evidence_id=evidence_id, case_id=case["case_id"], tenant_id="tenant-a",
        projection_kind="metric_summary", content=content,
    )
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_id=evidence_id,
        attachment_id=None, task_id=None, artifact_id=None, artifact_type="sys_metrics",
        collector_id="sys_metrics", source_type="test", source_id="test-source",
        target_ref="service:checkout", content_hash=projection["projection_hash"],
        projection_hash=projection["projection_hash"], quality="COMPLETE", freshness="FRESH",
    )
    return case, evidence_id


def test_hypothesis_gap_causal_and_conclusion_loop(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    envelope = {
        "expected_scope_revision": case["scope_revision"],
        "expected_control_revision": case["control_revision"],
    }
    hypotheses = client.post(
        "/internal/agent/tools/hypotheses",
        json={
            "case_id": case["case_id"], **envelope,
            "hypotheses": [{
                "hypothesis_id": "cpu-saturation", "statement": "CPU saturation increases latency",
                "status": "ACTIVE", "supporting_evidence_refs": [evidence_id],
                "missing_evidence": ["profile identifies the hot path"],
            }],
        }, headers=_headers(),
    )
    assert hypotheses.status_code == 200, hypotheses.text
    assert len(hypotheses.json()["data"]["graph"]["hypotheses"]) == 2

    gaps = client.post(
        "/internal/agent/tools/evidence-gaps",
        json={
            "case_id": case["case_id"], **envelope,
            "gaps": [{
                "required_fact": "profile identifies the hot path", "status": "BLOCKING",
                "blocked_claim": "specific function is the primary cause",
                "reason_code": "PROFILE_MISSING", "retryable": True,
                "observed_evidence": [evidence_id],
            }],
        }, headers=_headers(),
    )
    assert gaps.status_code == 200, gaps.text
    gap_id = gaps.json()["data"]["items"][0]["gap_id"]

    graph = client.post(
        "/internal/agent/tools/causal-graph",
        json={
            "case_id": case["case_id"], **envelope, "expected_evidence_watermark": 1,
            "nodes": [
                {"node_id": "cpu", "entity_ref": "service:checkout", "mechanism": "CPU saturation", "role": "PRIMARY_ROOT_CAUSE", "supporting_evidence_refs": [evidence_id]},
                {"node_id": "latency", "entity_ref": "service:checkout", "mechanism": "request latency", "role": "SYMPTOM", "supporting_evidence_refs": [evidence_id]},
            ],
            "edges": [{"edge_id": "cpu-latency", "source_node_id": "cpu", "target_node_id": "latency", "relation": "CAUSES", "supporting_evidence_refs": [evidence_id]}],
        }, headers=_headers(),
    )
    assert graph.status_code == 200, graph.text
    assert graph.json()["data"]["graph"]["edges"][0]["verification_state"] == "SUPPORTED"

    conclusion = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"], "summary": "CPU saturation explains the latency",
            "state": "CONFIRMED", "evidence_ids": [evidence_id],
            "primary_root_causes": [{"summary": "CPU saturation"}],
        }, headers=_headers(),
    )
    assert conclusion.status_code == 200, conclusion.text
    assert conclusion.json()["data"]["state"] == "PARTIALLY_CONFIRMED"
    stored = repo.get_conclusion(case["case_id"], "tenant-a")
    assert stored["evidence_gap_ids"] == [gap_id]
    assert stored["primary_root_causes"][0]["summary"] == "CPU saturation"

    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    data = workspace.json()["data"]
    assert data["hypothesis_graph"]["hypotheses"]
    assert data["evidence_gaps"][0]["gap_id"] == gap_id
    assert data["causal_graph"]["graph_id"]
    assert data["conclusion"]["conclusion_id"] == stored["conclusion_id"]


def test_agent_aliases_are_normalized_and_causal_ids_are_graph_scoped(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    envelope = {
        "expected_scope_revision": case["scope_revision"],
        "expected_control_revision": case["control_revision"],
    }
    hypotheses = client.post(
        "/internal/agent/tools/hypotheses",
        json={
            "case_id": case["case_id"], **envelope,
            "hypotheses": [{
                "id": "H1", "title": "CPU busy loop", "status": "PARTIALLY_RULED_OUT",
                "opposing_evidence_refs": [evidence_id], "alternative_to": ["OTHER_UNKNOWN"],
            }],
        }, headers=_headers(),
    )
    assert hypotheses.status_code == 200, hypotheses.text
    stored_hypothesis = next(
        item for item in hypotheses.json()["data"]["graph"]["hypotheses"]
        if item["hypothesis_id"] == "H1"
    )
    assert stored_hypothesis["statement"] == "CPU busy loop"
    assert stored_hypothesis["status"] == "WEAKENED"
    assert stored_hypothesis["contradicting_evidence_refs"] == [evidence_id]

    gaps = client.post(
        "/internal/agent/tools/evidence-gaps",
        json={
            "case_id": case["case_id"], **envelope,
            "gaps": [{
                "id": "GAP-1", "missing_fact": "hot function", "status": "OPEN",
                "resolution_plan": "collect a profile", "related_hypothesis": "H1",
            }],
        }, headers=_headers(),
    )
    assert gaps.status_code == 200, gaps.text
    stored_gap = gaps.json()["data"]["items"][0]
    assert stored_gap["required_fact"] == "hot function"
    assert stored_gap["next_best_action"] == "collect a profile"
    assert stored_gap["blocked_claim"] == "H1"

    graph_payload = {
        "case_id": case["case_id"], **envelope, "expected_evidence_watermark": 1,
        "nodes": [{
            "node_id": "n1", "entity_ref": "service:checkout",
            "mechanism": "CPU busy loop", "role": "PRIMARY_CAUSE",
            "supporting_evidence_refs": [evidence_id],
        }],
        "edges": [],
    }
    first = client.post("/internal/agent/tools/causal-graph", json=graph_payload, headers=_headers())
    second = client.post("/internal/agent/tools/causal-graph", json=graph_payload, headers=_headers())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"]["graph"]["graph_revision"] == 2


def test_dependency_only_unknown_node_is_not_persisted_as_causal_graph(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    response = client.post(
        "/internal/agent/tools/causal-graph",
        json={
            "case_id": case["case_id"],
            "expected_scope_revision": case["scope_revision"],
            "expected_control_revision": case["control_revision"],
            "expected_evidence_watermark": 1,
            "nodes": [{
                "node_id": "dependency-only",
                "entity_ref": "service:checkout",
                "mechanism": "observed TCP communication; not a causal root cause",
                "role": "UNKNOWN",
                "supporting_evidence_refs": [evidence_id],
            }],
            "edges": [],
        },
        headers=_headers(),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "DEPENDENCY_ONLY_NOT_CAUSAL_GRAPH"
    assert repo.get_causal_graph(case["case_id"], "tenant-a") is None


def test_dependency_projection_cannot_be_relabelled_as_root_cause(client: TestClient):
    case, _ = _case_and_evidence(client)
    evidence_id = "ev-dependency-only"
    content = {
        "summary": "client communicates with server",
        "graph_semantics": "dependency_only_not_causal",
        "graph": {"nodes": [], "edges": [{"source": "client", "target": "server"}]},
    }
    projection = repo.upsert_evidence_projection(
        evidence_id=evidence_id,
        case_id=case["case_id"],
        tenant_id="tenant-a",
        projection_kind="DEPENDENCY_GRAPH",
        content=content,
    )
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_id=evidence_id,
        attachment_id=None, task_id=None, artifact_id=None, artifact_type="network_discovery",
        collector_id="network_discovery", source_type="test", source_id="dependency-source",
        target_ref="service:checkout", content_hash=projection["projection_hash"],
        projection_hash=projection["projection_hash"], quality="COMPLETE", freshness="FRESH",
    )

    response = client.post(
        "/internal/agent/tools/causal-graph",
        json={
            "case_id": case["case_id"],
            "expected_scope_revision": case["scope_revision"],
            "expected_control_revision": case["control_revision"],
            "expected_evidence_watermark": 2,
            "nodes": [{
                "node_id": "server-root-cause",
                "entity_ref": "service:server",
                "mechanism": "observed TCP dependency",
                "role": "PRIMARY_ROOT_CAUSE",
                "supporting_evidence_refs": [evidence_id],
            }],
            "edges": [],
        },
        headers=_headers(),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "DEPENDENCY_ONLY_NOT_CAUSAL_GRAPH"
    assert repo.get_causal_graph(case["case_id"], "tenant-a") is None


def test_explicit_abstention_can_finish_without_evidence(client: TestClient):
    case, _ = _case_and_evidence(client)
    response = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"], "summary": "Available data cannot distinguish causes",
            "state": "INSUFFICIENT_EVIDENCE", "evidence_ids": [],
            "abstention_reason": "No observation covers the incident window",
        }, headers=_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "INSUFFICIENT_EVIDENCE"


def test_structured_abstention_requires_unknown_root_and_gap(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    response = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "The causal chain is not closed",
            "state": "INSUFFICIENT_EVIDENCE",
            "evidence_ids": [evidence_id],
            "root_location": {"type": "self", "evidence_refs": [evidence_id]},
            "mechanism": {
                "statement": "unverified",
                "supporting_evidence": [evidence_id],
                "confidence": 0.2,
            },
            "confidence_reason": "Only a correlation is available",
            "abstention_reason": "No mechanism-level observation",
        },
        headers=_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "INSUFFICIENT_EVIDENCE_ROOT_MUST_BE_UNKNOWN"


def test_confirmed_structured_conclusion_cannot_use_unknown_root(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    response = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "The mechanism is confirmed",
            "state": "CONFIRMED",
            "evidence_ids": [evidence_id],
            "root_location": {"type": "unknown", "evidence_refs": [evidence_id]},
            "mechanism": {
                "statement": "confirmed mechanism",
                "supporting_evidence": [evidence_id],
                "confidence": 0.9,
            },
            "confidence_reason": "Direct observation",
        },
        headers=_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "CONFIRMED_ROOT_CANNOT_BE_UNKNOWN"


def test_conclusion_history_keeps_superseded_revision_visible(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    first = repo.submit_conclusion_revision(
        case_id=case["case_id"], tenant_id="tenant-a", investigation_run_id="run-1",
        state="PARTIALLY_CONFIRMED", claims=[{
            "claim_id": "claim-old", "evidence_id": evidence_id,
            "projection_hash": repo.list_evidence_projections(case["case_id"], "tenant-a")[0]["projection_hash"],
        }], report_text="旧结论：CPU 可能相关",
    )
    second = repo.submit_conclusion_revision(
        case_id=case["case_id"], tenant_id="tenant-a", investigation_run_id="run-2",
        state="INSUFFICIENT_EVIDENCE", claims=[], report_text="新证据仍不足以闭合因果链",
    )

    response = client.get(f"/api/v1/cases/{case['case_id']}/conclusions")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["conclusion"]["conclusion_id"] == second["conclusion_id"]
    assert data["conclusion"]["revision_status"] == "CURRENT"
    history_by_id = {item["conclusion_id"]: item for item in data["history"]}
    assert history_by_id[first["conclusion_id"]]["revision_status"] == "SUPERSEDED"
    assert history_by_id[first["conclusion_id"]]["claim_evidence_bindings"]

    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    workspace_history = workspace.json()["data"]["conclusion_history"]
    assert [item["conclusion_id"] for item in workspace_history] == [
        second["conclusion_id"], first["conclusion_id"],
    ]


def test_finish_persists_evidence_bound_recommendation_in_workspace(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    response = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "CPU saturation is the supported recovery target",
            "state": "PARTIALLY_CONFIRMED",
            "evidence_ids": [evidence_id],
            "recommendations": [{
                "cause_or_edge_ref": "cpu-saturation",
                "target": "service:checkout",
                "concrete_action": "raise CPU capacity before traffic restoration",
                "evidence_refs": [evidence_id],
                "confidence": 0.82,
                "verification_operations": ["sys_metrics"],
                "success_criteria": ["cpu_percent < 80"],
            }],
        },
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    stored = repo.get_conclusion(case["case_id"], "tenant-a")
    assert len(stored["recommendation_ids"]) == 1

    workspace = client.get(f"/api/v1/cases/{case['case_id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    recommendations = workspace.json()["data"]["recommendations"]
    assert recommendations[0]["recommendation_id"] == stored["recommendation_ids"][0]
    assert recommendations[0]["evidence_refs"] == [evidence_id]
    assert recommendations[0]["conclusion_id"] == stored["conclusion_id"]


def test_finish_retry_reuses_conclusion_after_publish_failure(client: TestClient, monkeypatch):
    from server.app.sql_repository import SqlRepository

    case, evidence_id = _case_and_evidence(client)
    turn_id = "turn-retry-after-partial-finish"
    repo.record_agent_runtime_turn(
        turn_id=turn_id,
        case_id=case["case_id"],
        tenant_id="tenant-a",
        runtime_session_id=case["case_id"],
        runtime_generation=1,
        user_message="finish with a recovery recommendation",
        requested_mode=None,
        status="ACCEPTED",
        accepted_mode="pi",
    )
    payload = {
        "case_id": case["case_id"],
        "summary": "CPU saturation is verified",
        "state": "PARTIALLY_CONFIRMED",
        "evidence_ids": [evidence_id],
        "trigger_turn_id": turn_id,
        "recommendations": [{
            "recommendation_id": "rec-model-friendly-id",
            "cause_or_edge_ref": "cpu-saturation",
            "target": "service:checkout",
            "concrete_action": "raise CPU capacity before traffic restoration",
            "evidence_refs": [evidence_id],
            "confidence": 0.82,
        }],
    }
    original_finalize = SqlRepository.finalize_investigation_result
    attempts = 0

    def fail_once(self, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated publish failure")
        return original_finalize(self, **kwargs)

    monkeypatch.setattr(SqlRepository, "finalize_investigation_result", fail_once)
    with pytest.raises(RuntimeError, match="simulated publish failure"):
        client.post(
            "/internal/agent/tools/finish", json=payload, headers=_headers(),
        )
    second = client.post(
        "/internal/agent/tools/finish", json=payload, headers=_headers(),
    )

    assert second.status_code == 200, second.text
    conclusion = repo.get_conclusion(case["case_id"], "tenant-a")
    assert conclusion["revision"] == 1
    assert second.json()["data"]["conclusion_id"] == conclusion["conclusion_id"]
    assert len(repo.list_repair_recommendations(case["case_id"], "tenant-a")) == 1
    assert len(repo.list_assistant_messages(case["case_id"], "tenant-a")) == 1
    assert repo.get_agent_runtime_turn(turn_id, "tenant-a")["status"] == "COMPLETED"

    repo.submit_conclusion_revision(
        case_id=case["case_id"],
        tenant_id="tenant-a",
        investigation_run_id="",
        state="INSUFFICIENT_EVIDENCE",
        claims=[],
        report_text="orphan draft from a failed retry",
    )
    assert repo.get_conclusion(case["case_id"], "tenant-a")["conclusion_id"] == conclusion["conclusion_id"]


def test_business_latency_cannot_be_confirmed_from_cpu_metrics_alone(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    graph = client.post(
        "/internal/agent/tools/causal-graph",
        json={
            "case_id": case["case_id"],
            "expected_scope_revision": case["scope_revision"],
            "expected_control_revision": case["control_revision"],
            "expected_evidence_watermark": 1,
            "nodes": [
                {"node_id": "cpu", "entity_ref": "service:checkout", "mechanism": "CPU saturation", "role": "PRIMARY_ROOT_CAUSE", "supporting_evidence_refs": [evidence_id]},
                {"node_id": "latency", "entity_ref": "service:checkout", "mechanism": "request latency", "role": "SYMPTOM", "supporting_evidence_refs": [evidence_id]},
            ],
            "edges": [{"edge_id": "cpu-latency", "source_node_id": "cpu", "target_node_id": "latency", "relation": "CAUSES", "supporting_evidence_refs": [evidence_id]}],
        },
        headers=_headers(),
    )
    assert graph.status_code == 200, graph.text
    response = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"], "summary": "CPU explains checkout latency",
            "state": "CONFIRMED", "evidence_ids": [evidence_id],
            "primary_root_causes": [{"mechanism": "CPU saturation"}],
        },
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == "PARTIALLY_CONFIRMED"
    stored = repo.get_conclusion(case["case_id"], "tenant-a")
    assert any("业务症状" in item for item in stored["limitations"])


@pytest.mark.parametrize(
    ("recommendation", "error"),
    [
        ({"target": "service:checkout", "cause_or_edge_ref": "cpu"}, "INVALID_RECOMMENDATION_FIELDS"),
        ({
            "target": "service:checkout", "cause_or_edge_ref": "cpu",
            "concrete_action": "scale", "evidence_refs": ["unknown-evidence"],
        }, "INVALID_RECOMMENDATION_EVIDENCE"),
        ({
            "target": "service:checkout", "cause_or_edge_ref": "cpu",
            "concrete_action": "scale", "confidence": 1.5,
        }, "INVALID_RECOMMENDATION_CONFIDENCE"),
    ],
)
def test_finish_rejects_invalid_recommendation_before_conclusion(
    client: TestClient, recommendation: dict, error: str,
):
    case, evidence_id = _case_and_evidence(client)
    response = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"], "summary": "candidate conclusion",
            "evidence_ids": [evidence_id], "recommendations": [recommendation],
        },
        headers=_headers(),
    )
    assert response.status_code == 400, response.text
    assert error in response.json()["detail"]
    assert repo.get_conclusion(case["case_id"], "tenant-a") is None


def test_gap_without_id_is_idempotent_and_invalid_hypothesis_edge_is_rejected(client: TestClient):
    case, _ = _case_and_evidence(client)
    envelope = {
        "case_id": case["case_id"],
        "expected_scope_revision": case["scope_revision"],
        "expected_control_revision": case["control_revision"],
    }
    gap_payload = {
        **envelope,
        "gaps": [{"required_fact": "profile identifies hot path", "target": "service:checkout"}],
    }
    first = client.post("/internal/agent/tools/evidence-gaps", json=gap_payload, headers=_headers())
    second = client.post("/internal/agent/tools/evidence-gaps", json=gap_payload, headers=_headers())
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["items"][0]["gap_id"] == second.json()["data"]["items"][0]["gap_id"]
    assert len(repo.list_evidence_gaps(case["case_id"], "tenant-a")) == 1

    invalid_edge = client.post(
        "/internal/agent/tools/hypotheses",
        json={
            **envelope,
            "hypotheses": [{"hypothesis_id": "cpu", "statement": "CPU saturation"}],
            "edges": [{"source": "cpu", "target": "missing", "relation": "ALTERNATIVE_TO"}],
        },
        headers=_headers(),
    )
    assert invalid_edge.status_code == 409
    assert "INVALID_HYPOTHESIS_EDGE" in invalid_edge.json()["detail"]
