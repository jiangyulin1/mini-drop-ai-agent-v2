"""G3: Task artifacts are materialized as canonical Case Evidence."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.runtime_services import case_evidence_service, evidence_analysis_service
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus

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


def _create_case(client: TestClient) -> dict:
    created = client.post("/api/v1/cases", json={
        "title": "case-evidence-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-a"},
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _done_task_with_artifact() -> str:
    repo.register_agent(
        "agent-ev", "node-ev", "192.168.40.10", version="0.3.0",
        capabilities=["sys_metrics"],
    )
    task = repo.create_task(CreateTaskRequest(
        name="evidence-task",
        agent_id="agent-ev",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=11,
        duration_sec=15,
        options={"source": "manual"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "metadata": {"samples": 100, "window_sec": 15, "cpu_percent": 80},
    }])
    repo.transition_task(task.id, TaskStatus.RUNNING, "start", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "upload", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyze", Actor.WEB)
    repo.transition_task(task.id, TaskStatus.DONE, "done", Actor.AGENT)
    return task.id


def test_attachment_materializes_task_artifacts_as_case_evidence(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    resp = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}], "purpose": "已有采集"},
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["data"]["items"][0]
    assert item["result"] == "ACCEPTED"
    assert item["evidence_ids"], "task artifacts must produce evidence IDs"

    evidence = repo.list_case_evidence(case["case_id"], "tenant-a")
    assert len(evidence) == 1
    assert evidence[0]["task_id"] == task_id
    assert evidence[0]["artifact_type"] == "sys_metrics"
    assert evidence[0]["projection_hash"]
    assert evidence[0]["source_id"] == "sys_metrics"
    assert evidence[0]["schema_version"] == "1"
    assert evidence[0]["completeness"] == "COMPLETE"
    assert evidence[0]["trust_level"] == "INTERNAL"
    assert evidence[0]["sha256"]
    assert evidence[0]["lineage"]["task_id"] == task_id
    attachment = repo.list_case_attachments(case["case_id"], "tenant-a")[0]
    assert attachment["evidence_ids"] == item["evidence_ids"]
    listed = client.get(f"/api/v1/cases/{case['case_id']}/evidence")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["evidence_id"] == item["evidence_ids"][0]


def test_source_envelope_materializes_as_canonical_evidence(client: TestClient):
    case = _create_case(client)
    evidence_id = case_evidence_service.materialize_source_envelope(
        case["case_id"], "tenant-a", envelope={
            "schema_version": "evidence-envelope.v1",
            "evidence_id": "ev-source-1", "source_id": "mcp-k8s-control-plane",
            "source_version": "1", "principal_id": "operator-a", "tenant_id": "tenant-a",
            "case_id": case["case_id"], "resource_scope": {"cluster": "prod-a"},
            "operation": "events.list", "query_fingerprint": "query-hash",
            "observed_at": "2026-08-20T01:00:00+00:00", "valid_time": {},
            "data_class": "INTERNAL", "content_hash": "content-hash",
            "projection_hash": "envelope-projection-hash",
            "content_projection": {"events": [{"reason": "BackOff"}]},
            "redactions": {"projected_bytes": 64}, "policy": {"decision": "ALLOW"},
        }, actor_id="operator-a",
    )
    assert evidence_id == "ev-source-1"
    evidence = repo.get_case_evidence(case["case_id"], "tenant-a", evidence_id)
    assert evidence["source_channel"] == "MCP"
    assert evidence["trust_level"] == "AUTHORIZED_SOURCE"
    assert evidence["lineage"]["query_fingerprint"] == "query-hash"
    projection = repo.list_evidence_projections(case["case_id"], "tenant-a", evidence_id)[0]
    assert projection["projection_kind"] == "source_projection"
    assert projection["content"]["events"][0]["reason"] == "BackOff"


def test_evaluation_projection_import_is_explicit_and_reusable(client: TestClient, monkeypatch):
    case = _create_case(client)
    payload = {
        "evidence_id": f"eval:{case['case_id']}:pr_core",
        "pack_kind": "pr_core",
        "source_id": "github:grafana/grafana#123359",
        "source_ref": "github://grafana/grafana/pull/123359",
        "projection": {
            "records": [{
                "evidence_id": "ghpr:grafana-123359:pr_core:abc",
                "field_path": "github.title",
                "projection_hash": "a" * 64,
                "value": "Fix queue retention",
                "synthetic": False,
            }],
        },
        "source_bytes": 512,
        "synthetic": False,
    }
    disabled = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/import",
        json=payload,
        headers={"X-Evaluation-Import-Token": "eval-token"},
    )
    assert disabled.status_code == 404

    monkeypatch.setenv("MINI_DROP_EVAL_IMPORT_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_EVAL_IMPORT_TOKEN", "eval-token")
    imported = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/import",
        json=payload,
        headers={"X-Evaluation-Import-Token": "eval-token"},
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()["data"]
    assert result["evidence_id"] == payload["evidence_id"]
    assert result["synthetic"] is False
    assert result["projected_bytes"] > 0

    stored = repo.get_case_evidence(case["case_id"], "tenant-a", payload["evidence_id"])
    assert stored["source_channel"] == "EVALUATION"
    assert stored["data_origin"] == "REPLAY"
    assert stored["trust_level"] == "DEVELOPMENT_EVAL"
    projection = repo.list_evidence_projections(
        case["case_id"], "tenant-a", evidence_id=payload["evidence_id"],
    )[0]
    assert projection["projection_kind"] == "evaluation_projection"
    assert projection["content"]["records"][0]["field_path"] == "github.title"


def test_evaluation_projection_import_rejects_hash_mismatch(client: TestClient, monkeypatch):
    case = _create_case(client)
    monkeypatch.setenv("MINI_DROP_EVAL_IMPORT_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_EVAL_IMPORT_TOKEN", "eval-token")
    response = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/import",
        json={
            "evidence_id": f"eval:{case['case_id']}:runtime",
            "pack_kind": "simulated_runtime",
            "source_id": "synthetic:runtime",
            "source_ref": "synthetic://runtime",
            "projection": {"signals": []},
            "projection_hash": "b" * 64,
            "synthetic": True,
        },
        headers={"X-Evaluation-Import-Token": "eval-token"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "EVALUATION_PROJECTION_HASH_MISMATCH"


def test_finish_accepts_canonical_evidence_and_persists_conclusion(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_ids = attached["evidence_ids"]
    resp = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "根因是 CPU 饱和", "evidence_ids": evidence_ids},
        headers={"X-Internal-Token": TOKEN},
    )
    assert resp.status_code == 200, resp.text
    updated = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert updated["summary"]["current_finding"]["status"] == "concluded"
    assert updated["summary"]["current_finding"]["evidence_refs"] == evidence_ids


def test_excluded_case_evidence_is_not_consumed_by_finish(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    repo.exclude_case_evidence(case["case_id"], "tenant-a", evidence_id)
    resp = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "x", "evidence_ids": [evidence_id]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("INVALID_EVIDENCE_REFS")


def test_evidence_review_excluded_updates_canonical_store(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "EXCLUDED", "reason": "outlier"},
    )
    assert review.status_code == 200, review.text
    stored = repo.get_case_evidence(case["case_id"], "tenant-a", evidence_id)
    assert stored["status"] == "EXCLUDED"
    resp = client.post(
        "/internal/agent/tools/finish",
        json={"case_id": case["case_id"], "summary": "x", "evidence_ids": [evidence_id]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert resp.status_code == 400


def test_excluding_supporting_evidence_appends_downgraded_conclusion(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    finished = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "CPU 证据支持当前结论",
            "evidence_ids": [evidence_id],
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert finished.status_code == 200, finished.text
    original = repo.get_conclusion(case["case_id"], "tenant-a")
    assert original["revision"] == 1

    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "EXCLUDED", "reason": "outlier"},
    )
    assert review.status_code == 200, review.text
    downgraded = repo.get_conclusion(case["case_id"], "tenant-a")
    assert downgraded["revision"] == 2
    assert downgraded["state"] == "INSUFFICIENT_EVIDENCE"
    assert downgraded["verifier_version"] == "causal-report-verifier.v2-revalidation"
    assert downgraded["claim_evidence_bindings"][0]["verifier_result"] == "EVIDENCE_EXCLUDED"
    published_message = repo.list_assistant_messages(case["case_id"], "tenant-a")[-1]
    assert published_message["conclusion_revision_id"] == original["conclusion_id"]
    assert repo.get_conclusion(
        case["case_id"], "tenant-a", original["conclusion_id"],
    )["revision"] == 1


def test_case_evidence_detail_preview_and_download_contract(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]

    detail = client.get(f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}")
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()["data"]
    assert detail_data["collector_spec"]["collector_id"] == "sys_metrics"
    assert detail_data["projections"][0]["projection_hash"]
    assert detail_data["analyses"] == []

    preview = client.get(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/preview",
        params={"max_bytes": 65536},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["projection_hash"]
    assert preview.json()["data"]["content"] is not None

    raw = client.get(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/download",
        params={"format": "raw"},
    )
    assert raw.status_code == 200, raw.text
    assert raw.content
    bundle = client.get(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/download",
        params={"format": "bundle"},
    )
    assert bundle.status_code == 200, bundle.text
    assert bundle.headers["content-type"].startswith("application/zip")


def test_evidence_analysis_requires_valid_field_citation_and_review_marks_stale(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    attached = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]
    evidence_id = attached["evidence_ids"][0]
    run = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", explicit_single=True,
    )
    projection = repo.list_evidence_projections(case["case_id"], "tenant-a", evidence_id)[-1]

    invalid = client.post(
        "/internal/agent/tools/evidence-analysis",
        json={
            "case_id": case["case_id"], "analysis_run_id": run["analysis_run_id"],
            "facts": [{
                "claim": "CPU 使用率较高",
                "citations": [{
                    "evidence_id": evidence_id,
                    "projection_hash": projection["projection_hash"],
                    "field_path": "does.not.exist",
                }],
            }],
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert invalid.status_code == 409
    assert "FIELD_PATH_NOT_FOUND" in invalid.json()["detail"]

    completed = client.post(
        "/internal/agent/tools/evidence-analysis",
        json={
            "case_id": case["case_id"], "analysis_run_id": run["analysis_run_id"],
            "facts": [{
                "claim": "该证据已成功形成确定性摘要",
                "citations": [{
                    "evidence_id": evidence_id,
                    "projection_hash": projection["projection_hash"],
                    "field_path": "summary",
                }],
            }],
            "limitations": ["单一时间窗"],
            "runtime_policy": {"side_effect_policy": "PROPOSE_ONLY"},
        },
        headers={"X-Internal-Token": TOKEN},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "COMPLETED"

    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "LOW_TRUST", "reason": "sample window too short"},
    )
    assert review.status_code == 200, review.text
    analyses = client.get(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/analyses",
    ).json()["data"]["items"]
    assert analyses[0]["input_state"] == "STALE_INPUT"


def test_evidence_analysis_reuses_identical_pinned_input(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    evidence_id = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]["evidence_ids"][0]

    first = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", model_config_id="model-a", explicit_single=True,
    )
    duplicate = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", model_config_id="model-a", explicit_single=True,
    )

    assert first["reused"] is False
    assert duplicate["reused"] is True
    assert duplicate["analysis_run_id"] == first["analysis_run_id"]
    assert duplicate["input_fingerprint"] == first["input_fingerprint"]
    assert len(repo.list_evidence_analysis_runs(case["case_id"], "tenant-a")) == 1


def test_evidence_analysis_rejects_completion_after_review_revision(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    evidence_id = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]["evidence_ids"][0]
    run = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", explicit_single=True,
    )
    projection = repo.list_evidence_projections(case["case_id"], "tenant-a", evidence_id)[-1]

    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "TRUSTED", "reason": "human verified"},
    )
    assert review.status_code == 200, review.text

    with pytest.raises(ValueError, match="ANALYSIS_INPUT_STALE"):
        evidence_analysis_service.complete_run(
            analysis_run_id=run["analysis_run_id"], case_id=case["case_id"],
            tenant_id="tenant-a", facts=[{
                "claim": "old model result",
                "citations": [{
                    "evidence_id": evidence_id,
                    "projection_hash": projection["projection_hash"],
                    "field_path": "summary",
                }],
            }],
        )
    stored = evidence_analysis_service.get_run(
        run["analysis_run_id"], case["case_id"], "tenant-a",
    )
    assert stored["status"] == "QUEUED"
    assert stored["input_state"] == "STALE_INPUT"


def test_evidence_analysis_rejects_changed_projection_and_accepts_array_paths(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    evidence_id = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]["evidence_ids"][0]
    projection = repo.upsert_evidence_projection(
        evidence_id=evidence_id, case_id=case["case_id"], tenant_id="tenant-a",
        projection_kind="ARRAY_TEST", projection_version=2,
        content={"items": [{"value": "cpu-hot"}]},
    )
    run = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", explicit_single=True,
    )
    completed = evidence_analysis_service.complete_run(
        analysis_run_id=run["analysis_run_id"], case_id=case["case_id"],
        tenant_id="tenant-a", facts=[{
            "claim": "top item is CPU hot",
            "citations": [{
                "evidence_id": evidence_id,
                "projection_hash": projection["projection_hash"],
                "field_path": "projection.items[0].value",
                "quote": "cpu-hot", "start": 0, "end": 7,
            }],
        }],
    )
    assert completed["status"] == "COMPLETED"

    next_run = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", prompt_version="evidence-analysis.v2", explicit_single=True,
    )
    repo.upsert_evidence_projection(
        evidence_id=evidence_id, case_id=case["case_id"], tenant_id="tenant-a",
        projection_kind="ARRAY_TEST", projection_version=2,
        content={"items": [{"value": "io-hot"}]},
    )
    with pytest.raises(ValueError, match="ANALYSIS_INPUT_STALE"):
        evidence_analysis_service.complete_run(
            analysis_run_id=next_run["analysis_run_id"], case_id=case["case_id"],
            tenant_id="tenant-a", facts=[{
                "claim": "stale CPU result",
                "citations": [{
                    "evidence_id": evidence_id,
                    "projection_hash": projection["projection_hash"],
                    "field_path": "items[0].value",
                }],
            }],
        )


def test_explicit_single_analysis_can_explain_excluded_evidence(client: TestClient):
    task_id = _done_task_with_artifact()
    case = _create_case(client)
    evidence_id = client.post(
        f"/api/v1/cases/{case['case_id']}/attachments",
        json={"references": [{"type": "task", "id": task_id}]},
    ).json()["data"]["items"][0]["evidence_ids"][0]
    review = client.post(
        f"/api/v1/cases/{case['case_id']}/evidence/{evidence_id}/reviews",
        json={"evidence_id": evidence_id, "decision": "EXCLUDED", "reason": "known outlier"},
    )
    assert review.status_code == 200, review.text
    run = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", explicit_single=True,
    )
    projection = repo.list_evidence_projections(case["case_id"], "tenant-a", evidence_id)[-1]
    result = evidence_analysis_service.complete_run(
        analysis_run_id=run["analysis_run_id"], case_id=case["case_id"],
        tenant_id="tenant-a", facts=[{
            "claim": "excluded evidence remains independently explainable",
            "citations": [{
                "evidence_id": evidence_id,
                "projection_hash": projection["projection_hash"],
                "field_path": "summary",
            }],
        }],
    )
    assert run["input_state"] == "EXCLUDED_INPUT"
    assert result["status"] == "COMPLETED"
