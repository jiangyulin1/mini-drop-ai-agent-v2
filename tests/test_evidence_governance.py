"""Human-in-the-loop Evidence governance correctness contracts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server.app.app_factory as app_factory
from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.runtime_services import evidence_analysis_service


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_EVIDENCE_REVIEW_SECRET", "governance-test-secret")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "governance-internal-token")
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


def _case_and_evidence(client: TestClient, evidence_id: str = "ev-governed") -> tuple[dict, str]:
    response = client.post("/api/v1/cases", json={
        "title": "evidence governance",
        "problem_description": "checkout 延迟升高，需要治理调查证据",
        "recovery_goal": "恢复 checkout 延迟",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert response.status_code == 200, response.text
    case = response.json()["data"]
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_id=evidence_id,
        attachment_id=None, task_id="task-governed", artifact_id=1,
        artifact_type="sys_metrics", collector_id="sys_metrics",
        source_type="task_artifact", target_ref="service:checkout/node-1",
        content_hash="content-governed", projection_hash="projection-governed",
    )
    repo.upsert_evidence_projection(
        evidence_id=evidence_id, case_id=case["case_id"], tenant_id="tenant-a",
        projection_kind="SUMMARY", projection_version=1,
        content={"summary": "checkout CPU usage is elevated"},
    )
    return case, evidence_id


def _preview(
    client: TestClient, case_id: str, evidence_id: str, decision: str,
    assessment: dict | None = None,
) -> dict:
    response = client.post(
        f"/api/v1/cases/{case_id}/evidence/{evidence_id}/reviews/preview",
        json={"decision": decision, "assessment": assessment or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _submit(
    client: TestClient, case_id: str, evidence_id: str, decision: str,
    *, preview: dict | None = None, assessment: dict | None = None,
    override_reason: str | None = None,
):
    impact = preview or _preview(client, case_id, evidence_id, decision, assessment)
    payload = {
        "evidence_id": evidence_id,
        "decision": decision,
        "expected_review_revision": impact["current_review_revision"],
        "impact_token": impact["impact_token"],
        "assessment": assessment or {},
        "reason_code": "SCOPE_MISMATCH" if decision == "EXCLUDED" else "USER_VERIFIED",
        "reason": "governance test review",
    }
    if override_reason is not None:
        payload["override_reason"] = override_reason
    return client.post(
        f"/api/v1/cases/{case_id}/evidence/{evidence_id}/reviews",
        json=payload,
    )


def test_review_rejects_stale_revision_and_tampered_impact_token(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    impact = _preview(client, case["case_id"], evidence_id, "LOW_TRUST")
    tampered = dict(impact)
    tampered["impact_token"] = impact["impact_token"][:-1] + "0"
    response = _submit(
        client, case["case_id"], evidence_id, "LOW_TRUST", preview=tampered,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "EVIDENCE_REVIEW_IMPACT_STALE"

    accepted = _submit(
        client, case["case_id"], evidence_id, "LOW_TRUST", preview=impact,
    )
    assert accepted.status_code == 200, accepted.text
    stale = _submit(
        client, case["case_id"], evidence_id, "LOW_TRUST", preview=impact,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "EVIDENCE_REVIEW_VERSION_CONFLICT"


def test_structured_review_override_requires_explanation(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    assessment = {
        "target_identity": "CONFIRMED",
        "time_alignment": "FULL_WINDOW",
        "data_integrity": "COMPLETE",
        "source_reliability": "NATIVE_COLLECTOR",
        "scope_fit": "WRONG_SCOPE",
        "corroboration": "NONE",
        "freshness": "CURRENT_WINDOW",
    }
    impact = _preview(client, case["case_id"], evidence_id, "TRUSTED", assessment)
    assert impact["assessment_result"]["recommended_decision"] == "EXCLUDED"
    missing = _submit(
        client, case["case_id"], evidence_id, "TRUSTED",
        preview=impact, assessment=assessment,
    )
    assert missing.status_code == 409
    assert missing.json()["detail"] == "EVIDENCE_REVIEW_OVERRIDE_REASON_REQUIRED"
    accepted = _submit(
        client, case["case_id"], evidence_id, "TRUSTED",
        preview=impact, assessment=assessment,
        override_reason="现场已通过实例 UID 再次核验",
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["overridden_recommendation"] is True


def test_hidden_is_ui_only_but_excluded_invalidates_analysis(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    trusted_assessment = {
        "target_identity": "CONFIRMED",
        "time_alignment": "FULL_WINDOW",
        "data_integrity": "COMPLETE",
        "source_reliability": "NATIVE_COLLECTOR",
        "scope_fit": "CORRECT",
        "corroboration": "INDEPENDENT_SUPPORT",
        "freshness": "CURRENT_WINDOW",
    }
    trusted = _submit(
        client, case["case_id"], evidence_id, "TRUSTED",
        assessment=trusted_assessment,
    )
    assert trusted.status_code == 200, trusted.text
    run = evidence_analysis_service.create_run(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_ids=[evidence_id],
        mode="SINGLE", explicit_single=True,
    )
    hidden = _submit(client, case["case_id"], evidence_id, "HIDDEN")
    assert hidden.status_code == 200, hidden.text
    after_hidden = evidence_analysis_service.get_run(
        run["analysis_run_id"], case["case_id"], "tenant-a",
    )
    assert after_hidden["input_state"] == "CURRENT"
    stored = repo.get_case_evidence(case["case_id"], "tenant-a", evidence_id)
    assert stored["ui_hidden"] is True
    assert stored["derived_trust_score"] == 100

    excluded = _submit(client, case["case_id"], evidence_id, "EXCLUDED")
    assert excluded.status_code == 200, excluded.text
    after_excluded = evidence_analysis_service.get_run(
        run["analysis_run_id"], case["case_id"], "tenant-a",
    )
    assert after_excluded["input_state"] == "EXCLUDED_INPUT"


def test_exclusion_holds_recovery_plan_restore_resumes_and_dispatches_wakeup(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    run = repo.create_investigation_run(case_id=case["case_id"], tenant_id="tenant-a")
    current = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    created = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans",
        json={
            "action_id": "mini-drop.cleanup-expired-cache",
            "parameters": {"retention_days": 7},
            "value_after_fix": "释放过期缓存并保留隔离副本",
            "verification_method": "验证缓存目录与隔离目录",
            "evidence_refs": [evidence_id],
            "expected_case_version": current["row_version"],
        },
    )
    assert created.status_code == 200, created.text
    plan_id = created.json()["data"]["recovery_plan_id"]

    impact = _preview(client, case["case_id"], evidence_id, "EXCLUDED")
    assert impact["affected"]["recovery_plans"] == 1
    excluded = _submit(
        client, case["case_id"], evidence_id, "EXCLUDED", preview=impact,
    )
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["data"]["held_recovery_plan_ids"] == [plan_id]
    held = repo.get_case_recovery_plan(case["case_id"], "tenant-a", plan_id)
    assert held["status"] == "HELD_FOR_EVIDENCE_REVIEW"

    outbox = [
        item for item in repo.list_domain_outbox(status="PENDING")
        if item["event_type"] == "EVIDENCE_ELIGIBILITY_CHANGED"
    ][-1]
    assert outbox["payload"]["investigation_run_id"] == run["run_id"]
    app_factory._dispatch_domain_outbox_event(outbox)
    wakeup = repo.get_runtime_wakeup_by_outbox(outbox["outbox_id"])
    assert wakeup["reason_class"] == "EVIDENCE_ELIGIBILITY_CHANGED"
    assert wakeup["source_refs"] == [f"evidence:{evidence_id}"]

    restored = _submit(
        client, case["case_id"], evidence_id, "RESTORE_AS_TRUSTED",
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["resumed_recovery_plan_ids"] == [plan_id]
    resumed = repo.get_case_recovery_plan(case["case_id"], "tenant-a", plan_id)
    assert resumed["status"] == "PROPOSED"
    assert resumed["evidence_hold"] == {}


def test_intervention_ack_accepts_governance_outbox_reference(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    run = repo.create_investigation_run(case_id=case["case_id"], tenant_id="tenant-a")
    excluded = _submit(client, case["case_id"], evidence_id, "EXCLUDED")
    assert excluded.status_code == 200, excluded.text
    outbox = [
        item for item in repo.list_domain_outbox(status="PENDING")
        if item["event_type"] == "EVIDENCE_ELIGIBILITY_CHANGED"
    ][-1]
    assert outbox["payload"]["source_refs"] == [f"evidence:{evidence_id}"]
    app_factory._dispatch_domain_outbox_event(outbox)
    wakeup = repo.get_runtime_wakeup_by_outbox(outbox["outbox_id"])
    assert wakeup["reason_class"] == "EVIDENCE_ELIGIBILITY_CHANGED"

    response = client.post(
        "/internal/agent/tools/acknowledge-intervention",
        headers={"X-Internal-Token": "governance-internal-token"},
        json={
            "case_id": case["case_id"],
            "intervention_id": f"intervention-{wakeup['wakeup_id']}",
            "trust_state": "EXCLUDED",
            "evidence_state_rechecked": True,
            "revision_before": 0,
            "revision_after": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["affected_evidence_ids"] == [evidence_id]


def test_review_and_outbox_are_atomic(client: TestClient, monkeypatch):
    case, evidence_id = _case_and_evidence(client)
    impact = repo.preview_evidence_review(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_id=evidence_id,
        decision="LOW_TRUST", assessment={},
    )

    def crash(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(repo, "_enqueue_domain_outbox_in_session", crash)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        repo.apply_evidence_review(
            case_id=case["case_id"], tenant_id="tenant-a", evidence_id=evidence_id,
            decision="LOW_TRUST", assessment={}, reason_code="QUALITY_CONCERN",
            reason="transaction must roll back", override_reason=None,
            expected_review_revision=impact["current_review_revision"],
            impact_token=impact["impact_token"], actor_id="operator-a",
        )
    stored = repo.get_case_evidence(case["case_id"], "tenant-a", evidence_id)
    assert stored["review_revision"] == 0
    assert stored["review_trust_state"] == "UNREVIEWED"
    assert repo.list_evidence_review_revisions(
        case["case_id"], "tenant-a", evidence_id=evidence_id,
    ) == []


def test_recovery_plan_inherits_supporting_evidence_from_conclusion(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    conclusion = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "checkout CPU saturation",
            "evidence_ids": [evidence_id],
        },
        headers={"X-Internal-Token": "governance-internal-token"},
    )
    assert conclusion.status_code == 200, conclusion.text
    current = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    created = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans",
        json={
            "action_id": "mini-drop.cleanup-expired-cache",
            "parameters": {"retention_days": 7},
            "value_after_fix": "释放过期缓存并保留隔离副本",
            "verification_method": "验证缓存目录与隔离目录",
            "expected_case_version": current["row_version"],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["evidence_refs"] == [evidence_id]


def test_restored_evidence_conservatively_revalidates_conclusion(client: TestClient):
    case, evidence_id = _case_and_evidence(client)
    finished = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case["case_id"],
            "summary": "checkout CPU saturation",
            "evidence_ids": [evidence_id],
        },
        headers={"X-Internal-Token": "governance-internal-token"},
    )
    assert finished.status_code == 200, finished.text
    excluded = _submit(client, case["case_id"], evidence_id, "EXCLUDED")
    assert excluded.status_code == 200, excluded.text
    downgraded = repo.get_conclusion(case["case_id"], "tenant-a")
    assert downgraded["state"] == "INSUFFICIENT_EVIDENCE"

    restored = _submit(
        client, case["case_id"], evidence_id, "RESTORE_AS_TRUSTED",
    )
    assert restored.status_code == 200, restored.text
    revalidated = repo.get_conclusion(case["case_id"], "tenant-a")
    assert revalidated["revision"] == downgraded["revision"] + 1
    assert revalidated["state"] == "PARTIALLY_CONFIRMED"
    assert revalidated["claim_evidence_bindings"][0]["verifier_result"] == "VALIDATED"
    assert any("requires_reinvestigation" in item for item in revalidated["limitations"])


def test_recovery_plan_waits_until_all_blocking_evidence_is_restored(client: TestClient):
    case, first_id = _case_and_evidence(client, "ev-first")
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id="tenant-a", evidence_id="ev-second",
        attachment_id=None, task_id="task-second", artifact_id=2,
        artifact_type="sys_metrics", collector_id="sys_metrics",
        source_type="task_artifact", target_ref="service:checkout/node-2",
        content_hash="content-second", projection_hash="projection-second",
    )
    current = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    created = client.post(
        f"/api/v1/cases/{case['case_id']}/recovery-plans",
        json={
            "action_id": "mini-drop.cleanup-expired-cache",
            "parameters": {"retention_days": 7},
            "value_after_fix": "释放过期缓存并保留隔离副本",
            "verification_method": "验证缓存目录与隔离目录",
            "evidence_refs": [first_id, "ev-second"],
            "expected_case_version": current["row_version"],
        },
    )
    assert created.status_code == 200, created.text
    plan_id = created.json()["data"]["recovery_plan_id"]
    assert _submit(client, case["case_id"], first_id, "EXCLUDED").status_code == 200
    assert _submit(client, case["case_id"], "ev-second", "EXCLUDED").status_code == 200
    held = repo.get_case_recovery_plan(case["case_id"], "tenant-a", plan_id)
    assert held["evidence_hold"]["evidence_ids"] == ["ev-first", "ev-second"]

    assert _submit(
        client, case["case_id"], first_id, "RESTORE_AS_TRUSTED",
    ).status_code == 200
    still_held = repo.get_case_recovery_plan(case["case_id"], "tenant-a", plan_id)
    assert still_held["status"] == "HELD_FOR_EVIDENCE_REVIEW"
    assert still_held["evidence_hold"]["evidence_ids"] == ["ev-second"]

    assert _submit(
        client, case["case_id"], "ev-second", "RESTORE_AS_TRUSTED",
    ).status_code == 200
    resumed = repo.get_case_recovery_plan(case["case_id"], "tenant-a", plan_id)
    assert resumed["status"] == "PROPOSED"
