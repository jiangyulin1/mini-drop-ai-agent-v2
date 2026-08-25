"""Evaluator-controlled hidden-fact test for the evidence-native loop.

The decisive lock fact is withheld from the first Agent projection.  The test
then opens the collector capability, completes a real native Task, and checks
that the resulting canonical Evidence wakes the runtime and can be used to
revise the investigation state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.agent_runtime.dispatcher import reset_runtime
from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.state_machine import Actor, TaskStatus


TOKEN = "blind-gap-token"


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


def _headers() -> dict[str, str]:
    return {"X-Internal-Token": TOKEN}


def _create_case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "hidden-lock-contention",
        "problem_description": "checkout 延迟升高，请定位根因",
        "recovery_goal": "确认并缓解具体阻塞机制",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _seed_distractor(case_id: str) -> str:
    """Seed only a non-decisive CPU observation; lock data stays withheld."""
    evidence_id = "ev-distractor-cpu"
    projection = repo.upsert_evidence_projection(
        evidence_id=evidence_id,
        case_id=case_id,
        tenant_id="tenant-a",
        projection_kind="metric_summary",
        content={"summary": "CPU is elevated", "signals": {"cpu_percent": 82}},
    )
    repo.upsert_case_evidence(
        case_id=case_id,
        tenant_id="tenant-a",
        evidence_id=evidence_id,
        attachment_id=None,
        task_id=None,
        artifact_id=None,
        artifact_type="sys_metrics",
        collector_id="sys_metrics",
        source_type="test_fixture",
        source_id="distractor-cpu",
        target_ref="service:checkout",
        content_hash=projection["projection_hash"],
        projection_hash=projection["projection_hash"],
        quality="COMPLETE",
        freshness="FRESH",
    )
    return evidence_id


def test_hidden_fact_gap_collection_wakeup_and_revision_update(client, monkeypatch):
    """Exercise the evaluator-controlled P07 chain without Oracle leakage."""
    import server.app.app_factory as main_module

    monkeypatch.setenv("MINI_DROP_WAKEUP_QUIET_SEC", "0")
    case = _create_case(client)
    case_id = case["case_id"]
    distractor_id = _seed_distractor(case_id)
    envelope = {
        "case_id": case_id,
        "expected_scope_revision": case["scope_revision"],
        "expected_control_revision": case["control_revision"],
    }

    # Round 1: the model can only see the distractor.  The decisive lock fact
    # is absent from both the Case Evidence list and its projection store.
    visible = client.post(
        "/internal/agent/tools/list-case-evidence",
        json={"case_id": case_id},
        headers=_headers(),
    )
    assert visible.status_code == 200, visible.text
    visible_ids = {item["evidence_id"] for item in visible.json()["data"]["items"]}
    assert visible_ids == {distractor_id}
    assert not any(
        token in str(item).lower()
        for item in visible.json()["data"]["items"]
        for token in ("orders_mutex", "holder_pid", "waiter_pid")
    )

    hypothesis = client.post(
        "/internal/agent/tools/hypotheses",
        json={
            **envelope,
            "hypotheses": [{
                "hypothesis_id": "cpu-or-lock-contention",
                "statement": "CPU saturation or lock contention causes checkout latency",
                "status": "ACTIVE",
                "supporting_evidence_refs": [distractor_id],
                "missing_evidence": ["runtime lock snapshot identifies holder and waiter"],
            }],
        },
        headers=_headers(),
    )
    assert hypothesis.status_code == 200, hypothesis.text

    gap = client.post(
        "/internal/agent/tools/evidence-gaps",
        json={
            **envelope,
            "gaps": [{
                "gap_id": "gap-lock-snapshot",
                "required_fact": "runtime lock snapshot identifies the blocked waiter and lock holder",
                "blocked_claim": "a specific lock owner is the primary root cause",
                "reason_code": "DECISIVE_RUNTIME_FACT_WITHHELD",
                "retryable": True,
                "next_best_action": "collect a bounded process/runtime lock snapshot",
                "observed_evidence": [distractor_id],
                "status": "BLOCKING",
            }],
        },
        headers=_headers(),
    )
    assert gap.status_code == 200, gap.text
    assert gap.json()["data"]["items"][0]["status"] == "BLOCKING"

    # A premature confirmation is downgraded by the verifier; it cannot enter
    # the durable state as CONFIRMED while the blocking Gap remains open.
    premature = client.post(
        "/internal/agent/tools/finish",
        json={
            **envelope,
            "summary": "CPU may explain the latency, but the decisive mechanism is unknown",
            "state": "CONFIRMED",
            "evidence_ids": [distractor_id],
            "root_location": {"type": "service", "service": "checkout", "evidence_refs": [distractor_id]},
            "mechanism": {
                "statement": "CPU saturation or lock contention",
                "supporting_evidence": [distractor_id],
                "confidence": 0.9,
            },
            "confidence_reason": "blocking runtime fact is missing",
            "evidence_gaps": ["gap-lock-snapshot"],
            "abstention_reason": "cannot distinguish CPU from lock contention",
        },
        headers=_headers(),
    )
    assert premature.status_code == 200, premature.text
    assert premature.json()["data"]["state"] == "PARTIALLY_CONFIRMED"

    # Evaluator opens the previously withheld collector capability only now.
    repo.register_agent(
        "agent-lock", "node-lock", "192.168.77.30", version="0.3.0",
        capabilities=["runtime_snapshot"],
    )
    proposal = client.post(
        "/internal/agent/tools/collection-proposal",
        json={
            **envelope,
            "collector_id": "runtime_snapshot",
            "target_selector": {"agent_id": "agent-lock", "target_pid": 4321},
            "parameters": {"target_pid": 4321},
            "information_goal": "锁等待、futex、park 与运行时停顿",
            "reason_summary": "resolve gap-lock-snapshot",
            "runtime_policy": {"side_effect_policy": "AUTO_READ_LOW"},
        },
        headers=_headers(),
    )
    assert proposal.status_code == 200, proposal.text
    task_id = proposal.json()["data"]["task"]["id"]

    # The fixture represents the Collector result; parser/materializer remain
    # the production path and generate a new canonical Evidence ID.
    repo.add_artifacts(task_id, [{
        "artifact_type": "runtime_snapshot",
        "metadata": {
            "processes": [{"pid": 4321, "comm": "checkout", "cpu_percent": 82}],
            "locks": [{"waiter_pid": 4321, "holder_pid": 4170, "lock": "orders_mutex"}],
            "target_ref": "service:checkout",
        },
    }])
    for status in (TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING, TaskStatus.DONE):
        repo.transition_task(task_id, status, "hidden-fact evaluator collector", Actor.AGENT)

    # Enable the Pi wakeup path before the terminal Task event is processed.
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    main_module._wake_case_from_task(task_id, TaskStatus.DONE.value)
    new_evidence = [
        item for item in repo.list_case_evidence(case_id, "tenant-a")
        if item.get("evidence_id") != distractor_id
    ]
    assert len(new_evidence) == 1
    new_evidence_id = new_evidence[0]["evidence_id"]
    assert new_evidence[0]["task_id"] == task_id

    class FakeRuntime:
        def __init__(self):
            self.instructions = []

        def follow_up(self, received_case_id, instruction):
            self.instructions.append((received_case_id, instruction))

    runtime = FakeRuntime()
    reset_runtime()
    monkeypatch.setattr(main_module, "get_runtime", lambda: runtime)
    main_module._run_runtime_wakeup_pass()
    assert len(runtime.instructions) == 1
    wake_instruction = runtime.instructions[0][1]
    assert new_evidence_id in wake_instruction.evidence_ids
    assert distractor_id not in wake_instruction.evidence_ids

    # Round 2: the Agent can now revise its hypothesis and causal graph using
    # the newly materialized Evidence, while the old Gap is resolved.
    current = client.get(f"/api/v1/cases/{case_id}").json()["data"]
    revised_gap = client.post(
        "/internal/agent/tools/evidence-gaps",
        json={
            "case_id": case_id,
            "expected_scope_revision": current["scope_revision"],
            "expected_control_revision": current["control_revision"],
            "gaps": [{
                "gap_id": "gap-lock-snapshot",
                "required_fact": "runtime lock snapshot identifies the blocked waiter and lock holder",
                "status": "RESOLVED",
                "observed_evidence": [new_evidence_id],
            }],
        },
        headers=_headers(),
    )
    assert revised_gap.status_code == 200, revised_gap.text
    assert revised_gap.json()["data"]["items"][0]["status"] == "RESOLVED"

    revised_hypothesis = client.post(
        "/internal/agent/tools/hypotheses",
        json={
            "case_id": case_id,
            "expected_scope_revision": current["scope_revision"],
            "expected_control_revision": current["control_revision"],
            "hypotheses": [{
                "hypothesis_id": "lock-contention",
                "statement": "orders_mutex contention blocks checkout",
                "status": "CONFIRMED",
                "supporting_evidence_refs": [new_evidence_id],
            }],
        },
        headers=_headers(),
    )
    assert revised_hypothesis.status_code == 200, revised_hypothesis.text

    causal = client.post(
        "/internal/agent/tools/causal-graph",
        json={
            "case_id": case_id,
            "expected_scope_revision": current["scope_revision"],
            "expected_control_revision": current["control_revision"],
            "expected_evidence_watermark": 2,
            "nodes": [
                {
                    "node_id": "lock",
                    "entity_ref": "process:4170",
                    "mechanism": "orders_mutex held by checkout worker",
                    "role": "PRIMARY_ROOT_CAUSE",
                    "supporting_evidence_refs": [new_evidence_id],
                },
                {
                    "node_id": "latency",
                    "entity_ref": "service:checkout",
                    "mechanism": "requests wait on orders_mutex",
                    "role": "SYMPTOM",
                    "supporting_evidence_refs": [new_evidence_id],
                },
            ],
            "edges": [{
                "edge_id": "lock-latency",
                "source_node_id": "lock",
                "target_node_id": "latency",
                "relation": "CAUSES",
                "supporting_evidence_refs": [new_evidence_id],
            }],
        },
        headers=_headers(),
    )
    assert causal.status_code == 200, causal.text
    assert causal.json()["data"]["graph"]["edges"][0]["verification_state"] == "SUPPORTED"

    final = client.post(
        "/internal/agent/tools/finish",
        json={
            "case_id": case_id,
            "expected_scope_revision": current["scope_revision"],
            "expected_control_revision": current["control_revision"],
            "summary": "orders_mutex contention blocks checkout workers",
            "state": "CONFIRMED",
            "evidence_ids": [new_evidence_id],
            "root_location": {"type": "process", "pid": 4170, "evidence_refs": [new_evidence_id]},
            "mechanism": {
                "statement": "checkout waits on orders_mutex held by pid 4170",
                "supporting_evidence": [new_evidence_id],
                "confidence": 0.92,
            },
            "confidence_reason": "collector resolved the previously blocking lock snapshot gap",
        },
        headers=_headers(),
    )
    assert final.status_code == 200, final.text
    # The lock mechanism is confirmed, but the verifier may retain a partial
    # state because this synthetic case has no direct request/endpoint symptom
    # Evidence.  Crucially, the resolved Gap is no longer attached.
    assert final.json()["data"]["state"] in {"CONFIRMED", "PARTIALLY_CONFIRMED"}
    assert repo.get_conclusion(case_id, "tenant-a")["evidence_gap_ids"] == []

    # A late write carrying the pre-collection revision is fenced by Mini-Drop.
    stale = client.post(
        "/internal/agent/tools/evidence-gaps",
        json={
            "case_id": case_id,
            "expected_scope_revision": case["scope_revision"] - 1,
            "expected_control_revision": case["control_revision"],
            "gaps": [{"gap_id": "late", "required_fact": "late stale write"}],
        },
        headers=_headers(),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "STALE_SCOPE"