"""AI 集群诊断会话、探针审批、预算和证据链测试。"""

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.delenv("MINI_DROP_ALLOWED_SERVICES", raising=False)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    repo.register_agent(
        "a1", "host-1", "10.0.0.1",
        capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
    )
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _payload(query: str = "服务 service-a CPU 飙高，请定位原因") -> dict:
    return {
        "query": query,
        "context": {
            "service_id": "service-a",
            "environment": "production",
            "instances": [{
                "service_id": "service-a",
                "instance_id": "service-a-1",
                "host_id": "host-1",
                "agent_id": "a1",
                "pid": 1234,
                "environment": "production",
            }],
        },
        "budget_profile": "production_safe",
    }


class TestDiagnosisSessionAPI:
    def test_missing_instance_mapping_requires_scope_confirmation(self, client: TestClient):
        response = client.post("/api/v1/diagnoses", json={
            "query": "服务 service-a 为什么变慢",
            "context": {"service_id": "service-a", "environment": "production"},
        })
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"
        assert data["child_task_ids"] == []
        assert data["normalized_intent"]["ambiguities"] == ["service_instance_mapping"]

    def test_create_schedules_only_registered_low_risk_probe(self, client: TestClient):
        response = client.post("/api/v1/diagnoses", json=_payload())
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "COLLECTING"
        assert len(data["child_task_ids"]) == 1
        probes = {item["probe_id"]: item for item in data["probes"]}
        assert probes["host_process_metrics"]["status"] in {"SCHEDULED", "RUNNING"}
        assert probes["process_cpu_profile"]["status"] == "WAITING_APPROVAL"

        task = repo.tasks[data["child_task_ids"][0]]
        assert task.collector_type == "sys_metrics"
        assert task.request_params["options"]["registered_probe"] is True
        assert task.request_params["options"]["diagnosis_step_id"].startswith("step_")

    def test_r2_probe_requires_explicit_single_execution_approval(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        r2 = next(item for item in data["probes"] if item["risk_level"] == "R2")
        approved = client.post(
            f"/api/v1/diagnoses/{data['diagnosis_id']}/approvals",
            json={
                "step_id": r2["step_id"],
                "decision": "approve",
                "scope": "single_execution",
                "approver_id": "operator-1",
            },
        )
        assert approved.status_code == 200
        detail = approved.json()["data"]
        approved_probe = next(item for item in detail["probes"] if item["step_id"] == r2["step_id"])
        assert approved_probe["approved_by"] == "operator-1"
        assert approved_probe["task_id"]
        assert detail["budget_used"]["medium_risk_probes"] == 1
        assert repo.tasks[approved_probe["task_id"]].collector_type == "perf_cpu"

    def test_completed_probe_produces_evidence_linked_candidate(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.add_artifacts(task_id, [{
            "artifact_type": "sys_metrics",
            "object_key": f"tasks/{task_id}/sys_metrics.json",
            "metadata": {
                "data": {
                    "sample_count": 10,
                    "summary": {
                        "avg_cpu_user_pct": 92.0,
                        "avg_cpu_sys_pct": 5.0,
                        "avg_cpu_iowait_pct": 1.0,
                        "load1m": 8.0,
                        "thread_count": 20,
                        "thread_trend": "stable",
                        "fd_count": 20,
                        "fd_trend": "stable",
                        "fd_max": 25,
                        "vmrss_mb": 200,
                        "vmrss_mb_max": 210,
                        "ctx_nonvoluntary_rate": 10,
                        "net_rx_kbps": 10,
                        "net_tx_kbps": 10,
                    },
                },
            },
        }])
        repo.transition_task(task_id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assert detail["status"] == "COMPLETED"
        assert detail["latest_conclusion"]["root_cause_candidates"]
        candidate = detail["latest_conclusion"]["root_cause_candidates"][0]
        assert candidate["confidence_level"] in {"低", "中", "高"}
        assert candidate["evidence_refs"]
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(candidate["evidence_refs"]).issubset(evidence_ids)
        assert all(item["integrity_hash"].startswith("sha256:") for item in detail["evidence"])
        assert all(item["status"] != "WAITING_APPROVAL" for item in detail["probes"])

    def test_rejected_deep_probe_can_end_as_insufficient_evidence(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.transition_task(task_id, TaskStatus.DONE, "no structured output", Actor.ANALYZER)
        waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assert waiting["status"] == "WAITING_APPROVAL"
        r2 = next(item for item in waiting["probes"] if item["risk_level"] == "R2")

        rejected = client.post(
            f"/api/v1/diagnoses/{data['diagnosis_id']}/approvals",
            json={"step_id": r2["step_id"], "decision": "reject", "approver_id": "operator-1"},
        )
        assert rejected.status_code == 200
        detail = rejected.json()["data"]
        assert detail["status"] == "INSUFFICIENT_EVIDENCE"
        assert detail["latest_conclusion"]["confidence_level"] == "不可判断"

    def test_unknown_fields_are_rejected(self, client: TestClient):
        payload = _payload()
        payload["context"]["shell"] = "rm -rf /"
        response = client.post("/api/v1/diagnoses", json=payload)
        assert response.status_code == 422

    def test_service_allowlist_is_enforced(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_ALLOWED_SERVICES", "service-b")
        response = client.post("/api/v1/diagnoses", json=_payload())
        assert response.status_code == 403

    def test_requested_budget_cannot_exceed_policy_profile(self, client: TestClient):
        payload = _payload()
        payload["budget"] = {
            "max_hosts": 20,
            "max_service_instances": 100,
            "max_topology_hops": 3,
            "max_duration_minutes": 60,
            "max_parallel_probes": 10,
            "max_artifact_size_mb": 4096,
            "max_model_calls": 30,
            "max_medium_risk_probes": 5,
            "max_total_probe_cpu_seconds": 3600,
        }
        detail = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        assert detail["resource_budget"]["max_hosts"] == 5
        assert detail["resource_budget"]["max_parallel_probes"] == 3
        assert detail["resource_budget"]["max_medium_risk_probes"] == 1

    def test_probe_registry_exposes_no_shell_command(self, client: TestClient):
        probes = client.get("/api/v1/probes").json()["data"]
        assert probes
        assert all("command" not in probe for probe in probes)
        assert {probe["risk_level"] for probe in probes}.issubset({"R0", "R1", "R2", "R3"})
