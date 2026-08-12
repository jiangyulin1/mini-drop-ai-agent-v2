"""AI 集群诊断会话、探针审批、预算和证据链测试。"""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server.app import storage
from server.app.database import init_db, reset_engine
from server.app.diagnosis import orchestrator as orchestrator_module
from server.app.diagnosis.orchestrator import _pressure_flags
from server.app.diagnosis.actions import collect_action
from server.app.diagnosis.domain_analyzers import analyze_observations, assess_cluster, cluster_finding
from server.app.diagnosis.report_verifier import evidence_integrity_hash, verify_report
from server.app.diagnosis.schemas import ApprovalRequest
from server.app.main import app, repo
from server.app.main import diagnosis_orchestrator
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


def test_historical_request_never_creates_current_task(client: TestClient):
    payload = _payload("请分析 2020 年的 service-a 故障")
    payload["context"]["time_range"] = {
        "start": "2020-01-01T00:00:00Z",
        "end": "2020-01-01T00:30:00Z",
        "source": "user_expression",
    }
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["normalized_intent"]["diagnosis_mode"] == "HISTORICAL"
    assert data["status"] == "INSUFFICIENT_EVIDENCE"
    assert data["child_task_ids"] == []
    assert data["probes"] == []


def test_live_effective_window_includes_bounded_collection_period(client: TestClient):
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    requested_end = datetime.fromisoformat(data["requested_time_range"]["end"].replace("Z", "+00:00"))
    effective_end = datetime.fromisoformat(data["effective_time_range"]["end"].replace("Z", "+00:00"))
    assert data["effective_time_range"]["source"] == "live_collection_window"
    assert effective_end > requested_end


def test_audit_bundle_persists_runtime_decisions_and_keeps_oracle_optional(
    client: TestClient,
):
    payload = _payload()
    payload["evaluation_oracle"] = {
        "case_id": "hidden-cpu-case",
        "expected_location_type": "self",
        "expected_domain_type": "cpu",
    }
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = created["diagnosis_id"]

    public = client.get(
        f"/api/v1/diagnoses/{diagnosis_id}/audit-bundle"
    ).json()["data"]
    private = client.get(
        f"/api/v1/diagnoses/{diagnosis_id}/audit-bundle?include_oracle=true"
    ).json()["data"]

    assert public["trace_verification"]["status"] == "passed"
    assert public["trace_verification"]["runtime_step_count"] >= 4
    assert {item["stage"] for item in public["trace"]} >= {
        "intent", "scope", "hypothesis", "probe_plan",
    }
    assert "evaluation_oracle" not in public
    assert private["evaluation_oracle"]["case_id"] == "hidden-cpu-case"


def test_non_blocking_model_note_does_not_stop_resolved_scope(
    client: TestClient,
    monkeypatch,
):
    from server.app.diagnosis.intent import _fallback_intent

    def parsed_with_note(request):
        intent = _fallback_intent(request)
        intent.ambiguities = ["未提供时间范围，已使用服务器默认窗口"]
        return intent

    monkeypatch.setattr(orchestrator_module, "parse_diagnosis_intent", parsed_with_note)
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]

    assert data["target_scope"]["scope_completeness"] == "complete"
    assert data["status"] == "COLLECTING"
    assert data["child_task_ids"]


def test_zero_model_budget_forces_deterministic_intent(client: TestClient, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "is_feature_enabled", lambda feature: feature == "nlp")

    def fail_if_model_is_used(*args, **kwargs):
        raise AssertionError("model-backed intent parser must not run with zero model budget")

    monkeypatch.setattr(orchestrator_module, "parse_diagnosis_intent", fail_if_model_is_used)
    payload = _payload()
    payload["budget"] = {"max_model_calls": 0}

    response = client.post("/api/v1/diagnoses", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["budget_used"]["model_calls"] == 0


def test_case_path_disables_global_feedback_priors_by_default(
    client: TestClient,
    monkeypatch,
):
    monkeypatch.delenv("MINI_DROP_CASE_FEEDBACK_PRIORS_ENABLED", raising=False)

    def fail_if_global_priors_are_read():
        raise AssertionError("Case diagnosis must not consume global feedback priors by default")

    monkeypatch.setattr(repo, "get_feedback_priors", fail_if_global_priors_are_read)

    response = client.post("/api/v1/diagnoses", json=_payload())

    assert response.status_code == 200


def test_missing_target_anchor_does_not_expand_to_other_service(client: TestClient):
    payload = _payload()
    payload["context"]["instances"][0]["service_id"] = "service-b"
    payload["context"]["instances"][0]["instance_id"] = "service-b-1"
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"
    assert data["target_scope"]["scope_completeness"] == "unresolved"
    assert data["child_task_ids"] == []


def test_conflicting_instance_identity_abstains_instead_of_409(client: TestClient):
    """同一 instance_id 声明多个进程身份是冲突证据：应拒绝作答而非 409。

    ai_ops_v2 的 OB-ROBUST-CONFLICT-001 依赖这个行为拿到 abstention 命中；
    此前 _build_target_scope 直接 raise ValueError 把请求拒成 409，评测轮次
    永远无法创建诊断。
    """
    payload = _payload()
    payload["context"]["instances"] = [
        {
            "service_id": "service-a",
            "instance_id": "conflict-target",
            "host_id": "host-1",
            "agent_id": "a1",
            "pid": 1234,
            "environment": "production",
        },
        {
            "service_id": "frontend",
            "instance_id": "conflict-target",
            "host_id": "host-1",
            "agent_id": "a1",
            "pid": 5678,
            "environment": "production",
        },
    ]
    response = client.post("/api/v1/diagnoses", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"
    assert data["target_scope"]["scope_completeness"] == "unresolved"
    assert data["target_scope"]["excluded_targets"]
    assert data["child_task_ids"] == []


def test_zero_probe_budget_ends_explicitly(client: TestClient):
    payload = _payload()
    payload["budget"] = {"max_total_probe_cpu_seconds": 0}
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["status"] == "BUDGET_EXHAUSTED"
    assert data["child_task_ids"] == []


def test_structured_evidence_download_contains_observation(client: TestClient):
    created = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    for task_id in created["child_task_ids"]:
        _finish_sys_metrics_task(task_id, _normal_summary())

    detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    evidence = next(
        item for item in detail["evidence"]
        if item["source_type"] == "derived_artifact"
    )

    download = client.get(
        f"/api/v1/diagnoses/{diagnosis_id}/evidence/{evidence['evidence_id']}/download"
    )

    assert download.status_code == 200
    assert len(download.content) > 100
    assert download.json()["evidence_id"] == evidence["evidence_id"]
    assert download.json()["observed_value"]["summary"]["avg_cpu_user_pct"] == 18.0
    assert "attachment;" in download.headers["content-disposition"]


def test_evidence_bundle_contains_manifest_and_available_artifact(
    client: TestClient,
    monkeypatch,
):
    created = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    for task_id in created["child_task_ids"]:
        _finish_sys_metrics_task(task_id, _normal_summary())
    detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    evidence = next(
        item for item in detail["evidence"]
        if item["source_type"] == "derived_artifact"
    )
    assert evidence["artifact_links"]
    raw_content = b'{"sample_count":10,"summary":{"avg_cpu_user_pct":18.0}}'
    monkeypatch.setattr(storage, "object_size", lambda bucket, key: len(raw_content))
    monkeypatch.setattr(storage, "read_object_bytes", lambda bucket, key: raw_content)

    download = client.get(
        f"/api/v1/diagnoses/{diagnosis_id}/evidence/{evidence['evidence_id']}/bundle"
    )

    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        assert "evidence.json" in names
        assert "manifest.json" in names
        assert any(name.startswith("artifacts/") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["included_artifact_count"] == 1
        assert manifest["artifacts"][0]["availability"] == "available"


def test_pure_ebpf_latency_produces_io_finding():
    findings = analyze_observations([{
        "task_id": "t1",
        "target": {"instance_id": "i1"},
        "facts": {},
        "pressure": {"block_latency_high": True},
        "evidence_refs": ["ev1"],
    }])
    assert "io_wait_high" in {item["finding_type"] for item in findings}


def test_process_cpu_delta_detects_hot_process_on_non_saturated_host():
    pressure = _pressure_flags({
        "avg_cpu_user_pct": 25,
        "avg_cpu_sys_pct": 5,
        "process_cpu_core_usage": 1.2,
    }, {})
    assert pressure["cpu"] is True
    observations = [{
        "task_id": "t-target",
        "target": {"service_id": "service-a", "instance_id": "service-a-1"},
        "facts": {"avg_cpu_user_pct": 10, "process_cpu_core_usage": 0.1},
        "top_function": {"name": "", "percent": 0},
        "pressure": {name: False for name in pressure},
        "evidence_refs": ["ev-target"],
    }, {
        "task_id": "t-process",
        "target": {"service_id": "service-b", "instance_id": "service-b-1"},
        "facts": {"avg_cpu_user_pct": 25, "process_cpu_core_usage": 1.2},
        "top_function": {"name": "", "percent": 0},
        "pressure": pressure,
        "evidence_refs": ["ev-process"],
    }]
    findings = analyze_observations(observations)
    assert {item["finding_type"] for item in findings} == {"process_cpu_pressure"}
    assessment = assess_cluster({
        "target_service": "service-a",
        "downstream_service_ids": ["service-b"],
        "same_host_instance_ids": [],
    }, observations)
    assert assessment["domain_cause"]["subtype"] == "process_cpu_pressure"


def test_log_enospc_is_a_disk_pressure_signal_for_log_collector():
    flags = _pressure_flags({}, {
        "log_scan": {
            "log_files": [{
                "level_counts": {},
                "patterns": {"enospc": 1},
                "error_lines": [{"text": "No space left on device"}],
            }],
        },
    })

    assert flags["disk_full"] is True


def test_verifier_rejects_downstream_claim_without_evidence():
    result = verify_report({
        "cluster_assessment": {
            "classification": "downstream_dependency",
            "evidence_refs": [],
        },
        "actions": [],
    }, [], {"instances": []})
    assert result["status"] == "failed"
    assert any("缺少 Evidence" in issue for issue in result["issues"])


def test_root_location_and_domain_cause_are_independent():
    scope = {"target_service": "a", "downstream_service_ids": ["b"], "same_host_instance_ids": []}
    observations = [
        {"target": {"service_id": "a", "instance_id": "a1"}, "facts": {},
         "pressure": {}, "evidence_refs": ["ev-a"]},
        {"target": {"service_id": "b", "instance_id": "b1"},
         "facts": {"packet_loss_pct": 3}, "pressure": {"network": True}, "evidence_refs": ["ev-b"]},
    ]
    result = assess_cluster(scope, observations)
    assert result["root_location"]["type"] == "downstream"
    assert result["domain_cause"]["type"] == "network"
    assert "linux.cpu.process_pressure" not in cluster_finding(result)["knowledge_ids"]


def test_verifier_detects_rendered_command_tampering():
    action = collect_action(
        action_id="a", title="collect", collector_type="sys_metrics",
        target={"agent_id": "a1", "pid": 1234}, duration_sec=15, sample_rate=11,
        comment="test", risk_level="R1", evidence_refs=[], confidence_level="中",
    )
    action["rendered_command"] += " --duration 99"
    result = verify_report({"actions": [action]}, [], {"instances": [{"agent_id": "a1", "pid": 1234}]})
    assert result["status"] == "failed"
    assert any("preview" in issue for issue in result["issues"])


def test_verifier_recomputes_full_evidence_hash():
    evidence = {
        "evidence_id": "ev1", "source_type": "derived_artifact", "source_system": "agent",
        "evidence_role": "incident", "target": {"agent_id": "a1", "pid": 1234},
        "event_time_range": {}, "ingestion_time": datetime.now(timezone.utc),
        "query_or_probe": "sys_metrics", "raw_artifact_ref": None, "derived_artifact_ref": "x",
        "derivation_version": "v2", "observed_value": {"cpu": 1}, "baseline_value": {},
        "anomaly_score": {}, "data_quality": {"domains": ["host"]}, "claim_links": [],
    }
    evidence["integrity_hash"] = evidence_integrity_hash(evidence)
    evidence["observed_value"]["cpu"] = 2
    result = verify_report(
        {"root_location": {"type": "self", "target_ref": "i1", "evidence_refs": ["ev1"]}, "actions": []},
        [evidence], {"instances": [{"agent_id": "a1", "pid": 1234}]},
    )
    assert any("Hash" in issue for issue in result["issues"])


def test_five_instances_are_covered_in_bounded_batches(client: TestClient):
    payload = _payload("service-a 延迟升高，覆盖全部实例")
    payload["budget"] = {"max_parallel_probes": 2, "max_medium_risk_probes": 0}
    for index in range(2, 6):
        agent_id = f"a{index}"
        host_id = f"host-{index}"
        repo.register_agent(agent_id, host_id, f"10.0.0.{index}", capabilities=["sys_metrics"])
        payload["context"]["instances"].append({
            "service_id": "service-a", "instance_id": f"service-a-{index}",
            "host_id": host_id, "agent_id": agent_id, "pid": 1200 + index,
            "environment": "production",
        })
    detail = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert len(detail["coverage"]) == 5
    observed_active = []
    while detail["status"] not in {"COMPLETED", "PARTIAL_COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED"}:
        active = [
            task for task in repo.tasks.values()
            if task.request_params.get("options", {}).get("diagnosis_id") == detail["diagnosis_id"]
            and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING}
        ]
        observed_active.append(len(active))
        assert len(active) <= 2
        for task in active:
            _finish_sys_metrics_task(task.id, _normal_summary())
        detail = client.get(f"/api/v1/diagnoses/{detail['diagnosis_id']}").json()["data"]
    assert max(observed_active) == 2
    assert {item["status"] for item in detail["coverage"]} == {"COMPLETED"}


def test_concurrent_approval_creates_one_task(client: TestClient):
    detail = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    task_id = detail["child_task_ids"][0]
    repo.transition_task(task_id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    repo.transition_task(task_id, TaskStatus.DONE, "no artifact", Actor.ANALYZER)
    waiting = client.get(f"/api/v1/diagnoses/{detail['diagnosis_id']}").json()["data"]
    r2 = next(item for item in waiting["probes"] if item["risk_level"] == "R2")
    request = ApprovalRequest(step_id=r2["step_id"], decision="approve", approver_id="operator")

    def approve_once():
        try:
            return diagnosis_orchestrator.approve(detail["diagnosis_id"], request)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: approve_once(), range(2)))
    matching = [
        task for task in repo.tasks.values()
        if task.request_params.get("options", {}).get("diagnosis_step_id") == r2["step_id"]
    ]
    assert len(matching) == 1


def test_concurrent_advance_writes_one_conclusion(client: TestClient):
    detail = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    _finish_sys_metrics_task(detail["child_task_ids"][0], _normal_summary())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: diagnosis_orchestrator.advance(detail["diagnosis_id"]), range(2)))
    stored = diagnosis_orchestrator.store.get_session(detail["diagnosis_id"])
    assert stored is not None
    assert len(stored["conclusion_versions"]) == 1


def test_remote_artifact_falls_back_to_object_storage_when_agent_path_is_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        orchestrator_module.storage,
        "read_object_bytes",
        lambda bucket, key: b'{"avg_cpu_user_pct": 88.0}',
    )

    value = diagnosis_orchestrator._read_artifact_json({
        "artifact_type": "sys_metrics",
        "bucket": "mini-drop",
        "object_key": "tasks/task-remote/sys_metrics.json",
        "local_path": str(tmp_path / "worker-only" / "sys_metrics.json"),
    })

    assert value == {"avg_cpu_user_pct": 88.0}


def test_existing_structured_evidence_uses_legal_transition_and_completes(client: TestClient):
    task_id = client.post("/api/tasks", json={
        "name": "reusable-sys-metrics",
        "agent_id": "a1",
        "target_pid": 1234,
        "collector_type": "sys_metrics",
        "duration_sec": 5,
    }).json()["data"]["task_id"]
    summary = _normal_summary()
    summary["avg_cpu_user_pct"] = 92.0
    _finish_sys_metrics_task(task_id, summary)

    response = client.post("/api/v1/diagnoses", json=_payload())

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["status"] == "COMPLETED"
    assert len(detail["coverage"]) == 1
    assert detail["coverage"][0]["target"] == "service-a-1"
    assert detail["coverage"][0]["status"] == "COMPLETED"
    assert detail["coverage"][0]["task_id"] == task_id
    transitions = [(event["from_status"], event["to_status"]) for event in detail["events"]]
    assert ("ANALYZING_EXISTING_DATA", "ANALYZING") in transitions
    assert ("ANALYZING", "CONCLUDING") in transitions


def test_reusable_evidence_must_be_fresh_and_cover_every_target(client: TestClient):
    task_id = client.post("/api/tasks", json={
        "name": "reusable-sys-metrics",
        "agent_id": "a1",
        "target_pid": 1234,
        "collector_type": "sys_metrics",
        "duration_sec": 5,
    }).json()["data"]["task_id"]
    _finish_sys_metrics_task(task_id, _normal_summary())
    now = datetime.now(timezone.utc)

    complete_scope = {
        "instances": [{"agent_id": "a1", "pid": 1234}],
    }
    assert diagnosis_orchestrator._find_reusable_tasks(
        complete_scope, now - timedelta(minutes=30), now,
    ) == [task_id]
    assert diagnosis_orchestrator._find_reusable_tasks(
        complete_scope, now - timedelta(minutes=30), now + timedelta(minutes=5),
    ) == []

    partial_scope = {
        "instances": [
            {"agent_id": "a1", "pid": 1234},
            {"agent_id": "a2", "pid": 5678},
        ],
    }
    assert diagnosis_orchestrator._find_reusable_tasks(
        partial_scope, now - timedelta(minutes=30), now,
    ) == []


def test_diagnosis_waits_for_all_active_target_tasks(client: TestClient):
    repo.register_agent(
        "a2", "host-2", "10.0.0.2",
        capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
    )
    payload = _payload()
    payload["budget"] = {"max_medium_risk_probes": 0}
    payload["context"]["instances"].append({
        "service_id": "service-b",
        "instance_id": "service-b-1",
        "host_id": "host-2",
        "agent_id": "a2",
        "pid": 5678,
        "environment": "production",
    })
    payload["context"]["dependencies"] = [{
        "source_service": "service-a",
        "target_service": "service-b",
        "relation": "CALLS",
    }]
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    task_ids = [
        item["task_id"]
        for item in created["probes"]
        if item["probe_id"] == "host_process_metrics"
    ]
    assert len(task_ids) == 2

    _finish_sys_metrics_task(task_ids[0], _normal_summary())
    collecting = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    assert collecting["status"] == "COLLECTING"
    run_node = next(item for item in collecting["pipeline_nodes"] if item["node_name"] == "run_probes")
    assert run_node["status"] == "RUNNING"
    assert run_node["metrics"]["terminal_task_count"] == 1

    _finish_sys_metrics_task(task_ids[1], _normal_summary())
    completed = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    assert completed["status"] == "INSUFFICIENT_EVIDENCE"
    assert len(completed["evidence"]) == 4


def _finish_sys_metrics_task(task_id: str, summary: dict):
    repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    repo.add_artifacts(task_id, [{
        "artifact_type": "sys_metrics",
        "object_key": f"tasks/{task_id}/sys_metrics.json",
        "metadata": {
            "data": {
                "sample_count": 10,
                "summary": summary,
            },
        },
    }])
    repo.transition_task(task_id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)


def _fail_artifact_upload_task(task_id: str):
    repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
    repo.transition_task(
        task_id,
        TaskStatus.FAILED,
        (
            "artifact upload failed: HTTPConnectionPool(host='10.0.0.10', port=9000): "
            "Failed to establish a new connection: [Errno 111] Connection refused"
        ),
        Actor.AGENT,
    )


def _normal_summary() -> dict:
    return {
        "avg_cpu_user_pct": 18.0,
        "avg_cpu_sys_pct": 4.0,
        "avg_cpu_iowait_pct": 1.0,
        "load1m": 0.8,
        "thread_count": 20,
        "thread_trend": "stable",
        "fd_count": 20,
        "fd_trend": "stable",
        "fd_max": 25,
        "vmrss_mb": 200,
        "vmrss_mb_max": 220,
        "ctx_nonvoluntary_rate": 10,
        "net_rx_kbps": 10,
        "net_tx_kbps": 10,
    }


def test_stable_rss_and_fd_near_observed_max_are_not_pressure():
    summary = _normal_summary()
    summary.update({
        "vmrss_mb": 200,
        "vmrss_mb_max": 201,
        "fd_count": 20,
        "fd_max": 20,
    })
    flags = orchestrator_module._pressure_flags(summary, {})
    assert flags["memory"] is False
    assert flags["fd"] is False


def test_cluster_assessment_uses_healthy_peer_to_localize_storage_path_failure():
    scope = {
        "target_service": "service-a",
        "instances": [
            {"service_id": "service-a", "instance_id": "service-a-1"},
            {"service_id": "service-a", "instance_id": "service-a-2"},
        ],
        "same_host_instance_ids": [],
        "downstream_service_ids": [],
    }
    observations = [
        {
            "target": {
                "service_id": "service-a", "instance_id": "service-a-1",
                "host_id": "host-1", "agent_id": "a1", "pid": 1,
            },
            "collector_type": "sys_metrics",
            "collection_status": "DONE",
            "failure_kind": None,
            "facts": {},
            "pressure": {},
            "evidence_refs": ["ev-healthy"],
        },
        {
            "target": {
                "service_id": "service-a", "instance_id": "service-a-2",
                "host_id": "host-2", "agent_id": "a2", "pid": 1,
            },
            "collector_type": "sys_metrics",
            "collection_status": "FAILED",
            "failure_kind": "artifact_upload_failed",
            "facts": {},
            "pressure": {},
            "evidence_refs": ["ev-failed"],
        },
    ]

    assessment = assess_cluster(scope, observations)

    assert assessment["classification"] == "single_instance_storage_path_failure"
    assert assessment["confidence_level"] == "高"
    assert assessment["root_location"] == {
        "type": "self",
        "target_ref": "service-a-2",
        "evidence_refs": ["ev-failed"],
    }
    assert assessment["domain_cause"]["type"] == "network"
    assert assessment["domain_cause"]["subtype"] == "agent_to_object_storage_connectivity"
    assert {item["instance_id"] for item in assessment["compared_targets"]} == {
        "service-a-1", "service-a-2",
    }


def _sys_metric_probe_by_instance(data: dict) -> dict:
    return {
        item["target"]["instance_id"]: item
        for item in data["probes"]
        if item["probe_id"] == "host_process_metrics"
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
        assert data["latest_conclusion"]["diagnostic_commands"]
        assert all(cmd["auto_execute"] is False for cmd in data["latest_conclusion"]["diagnostic_commands"])

    def test_create_schedules_only_registered_low_risk_probe(self, client: TestClient):
        response = client.post("/api/v1/diagnoses", json=_payload())
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "COLLECTING"
        assert len(data["child_task_ids"]) == 1
        probes = {item["probe_id"]: item for item in data["probes"]}
        assert probes["host_process_metrics"]["status"] in {"SCHEDULED", "RUNNING"}
        assert "process_cpu_profile" not in probes
        assert data["coverage"][0]["status"] in {"SCHEDULED", "RUNNING"}

        task = repo.tasks[data["child_task_ids"][0]]
        assert task.collector_type == "sys_metrics"
        assert task.request_params["options"]["registered_probe"] is True
        assert task.request_params["options"]["diagnosis_step_id"].startswith("step_")

    def test_r2_probe_requires_explicit_single_execution_approval(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.transition_task(task_id, TaskStatus.DONE, "no structured output", Actor.ANALYZER)
        waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        r2 = next(item for item in waiting["probes"] if item["risk_level"] == "R2")
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
        payload = _payload()
        payload["evaluation_oracle"] = {
            "case_id": "cpu-hotspot-001",
            "expected_instance_id": "service-a-1",
            "expected_location_type": "self",
            "expected_domain_type": "cpu",
            "expected_classification": "self_code_or_process_pressure",
        }
        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        assert data["evaluation_oracle"]["case_id"] == "cpu-hotspot-001"
        assert "evaluation_oracle" not in data["normalized_intent"]
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
        assert detail["latest_conclusion"]["cluster_assessment"]["evidence_refs"]
        assert detail["latest_conclusion"]["diagnostic_commands"]
        assert all(cmd["auto_execute"] is False for cmd in detail["latest_conclusion"]["diagnostic_commands"])
        candidate = detail["latest_conclusion"]["root_cause_candidates"][0]
        assert candidate["confidence_level"] in {"低", "中", "高"}
        assert candidate["evidence_refs"]
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(candidate["evidence_refs"]).issubset(evidence_ids)
        assert all(item["integrity_hash"].startswith("sha256:") for item in detail["evidence"])
        assert all(item["status"] != "WAITING_APPROVAL" for item in detail["probes"])
        assert len(detail["pipeline_nodes"]) == 12
        assert detail["latest_conclusion"]["verification"]["status"] == "passed"
        assert detail["latest_conclusion"]["findings"]
        assert detail["latest_conclusion"]["knowledge_refs"]
        evaluation = detail["latest_conclusion"]["evaluation"]
        assert evaluation["case_id"] == "cpu-hotspot-001"
        assert evaluation["oracle_isolated"] is True
        assert evaluation["exact_match"] is True
        assert evaluation["score_pct"] == 100.0
        assert evaluation["matched_count"] == evaluation["specified_count"] == 4
        actions = detail["latest_conclusion"]["actions"]
        assert actions
        assert all(action["action_type"] in {"inspect", "collect", "manual_remediation"} for action in actions)
        assert all(action["rendered_command"] == action["command"] for action in actions)
        assert all(action["auto_execute"] is False for action in actions)
        recommendations = detail["latest_conclusion"]["recommendations"]
        assert {item["category"] for item in recommendations} == {
            "mitigation", "optimization", "validation",
        }
        assert all(item["detail"] for item in recommendations)
        assert all(set(item["evidence_refs"]).issubset(evidence_ids) for item in recommendations)
        graph = detail["hypothesis_graph"]
        assert graph["updated_at"]
        assert graph["edges"]
        assert any(item["status"] == "SUPPORTED" for item in graph["hypotheses"])
        assert all(len(item["history"]) >= 2 for item in graph["hypotheses"])
        assert all("evidence_score" in item for item in graph["hypotheses"])

    def test_analysis_strategy_is_persisted_and_changes_probe_plan(self, client: TestClient):
        decision_tree = _payload()
        decision_tree["analysis_strategy"] = "DECISION_TREE"
        tree_detail = client.post("/api/v1/diagnoses", json=decision_tree).json()["data"]
        assert tree_detail["normalized_intent"]["analysis_strategy"] == "DECISION_TREE"
        assert tree_detail["planner_version"].endswith(":decision_tree")
        assert any(
            item["risk_level"] == "R2" and item["status"] == "WAITING_APPROVAL"
            for item in tree_detail["probes"]
        )

        exploratory = _payload()
        exploratory["analysis_strategy"] = "EXPLORATORY"
        exploratory_detail = client.post("/api/v1/diagnoses", json=exploratory).json()["data"]
        assert exploratory_detail["normalized_intent"]["analysis_strategy"] == "EXPLORATORY"
        assert {
            item["probe_id"] for item in exploratory_detail["probes"]
        } >= {"host_process_metrics", "process_memory_map"}

    def test_rejected_deep_probe_can_end_as_insufficient_evidence(self, client: TestClient):
        data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
        task_id = data["child_task_ids"][0]
        repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
        repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
        repo.transition_task(task_id, TaskStatus.DONE, "no structured output", Actor.ANALYZER)
        waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assert waiting["status"] == "WAITING_APPROVAL"
        repeated = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}")
        assert repeated.status_code == 200
        assert repeated.json()["data"]["status"] == "WAITING_APPROVAL"
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

    def test_dual_worker_storage_path_failure_is_localized_by_peer_comparison(
        self,
        client: TestClient,
    ):
        repo.register_agent(
            "a2", "host-2", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload(
            "同一服务的两个 Worker 协同运行，但部分节点采集结果缺失，请做跨节点对比",
        )
        payload["context"]["instances"].append({
            "service_id": "service-a",
            "instance_id": "service-a-2",
            "host_id": "host-2",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = _sys_metric_probe_by_instance(data)
        assert set(probes) == {"service-a-1", "service-a-2"}

        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], _normal_summary())
        _fail_artifact_upload_task(probes["service-a-2"]["task_id"])

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        conclusion = detail["latest_conclusion"]
        assessment = conclusion["cluster_assessment"]

        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "single_instance_storage_path_failure"
        assert assessment["confidence_level"] == "高"
        assert assessment["root_location"]["target_ref"] == "service-a-2"
        assert assessment["domain_cause"] == {
            "type": "network",
            "subtype": "agent_to_object_storage_connectivity",
            "evidence_refs": assessment["root_location"]["evidence_refs"],
        }
        assert len(assessment["compared_targets"]) == 2
        top_candidate = conclusion["root_cause_candidates"][0]
        assert top_candidate["candidate_id"] == "artifact_storage_unreachable"
        assert top_candidate["confidence_level"] == "高"
        assert len(top_candidate["evidence_refs"]) == 3
        assert "部分目标采集失败" in conclusion["limitations"]
        assert conclusion["verification"]["status"] == "passed"

    def test_same_host_noisy_neighbor_assessment_uses_multiple_agents(self, client: TestClient):
        repo.register_agent(
            "a2", "host-1", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload("service-a 变慢，判断是不是被同宿主其他服务影响")
        payload["context"]["instances"].append({
            "service_id": "service-b",
            "instance_id": "service-b-1",
            "host_id": "host-1",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = {
            item["target"]["instance_id"]: item
            for item in data["probes"]
            if item["probe_id"] == "host_process_metrics"
        }
        assert set(probes) == {"service-a-1", "service-b-1"}

        noisy_summary = _normal_summary()
        noisy_summary.update({
            "avg_cpu_user_pct": 86.0,
            "avg_cpu_sys_pct": 9.0,
            "avg_cpu_iowait_pct": 24.0,
            "load1m": 9.0,
        })
        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], _normal_summary())
        _finish_sys_metrics_task(probes["service-b-1"]["task_id"], noisy_summary)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assessment = detail["latest_conclusion"]["cluster_assessment"]
        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "same_host_noisy_neighbor"
        assert assessment["confidence_level"] in {"中", "高"}
        assert len(assessment["compared_targets"]) == 2
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(assessment["evidence_refs"]).issubset(evidence_ids)
        commands = detail["latest_conclusion"]["diagnostic_commands"]
        assert any(cmd["risk_level"] == "R2" and cmd["requires_approval"] for cmd in commands)
        assert all(cmd["execution_policy"] == "human_review_required" for cmd in commands)
        assert all(cmd["approval_policy"] == "single_execution" for cmd in commands if cmd["risk_level"] == "R2")

    def test_shared_io_wait_prefers_host_contention_over_generic_neighbor(self, client: TestClient):
        repo.register_agent(
            "a2", "host-1", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload("service-a 变慢，检查同宿主 I/O 争抢")
        payload["context"]["instances"].append({
            "service_id": "service-b",
            "instance_id": "service-b-1",
            "host_id": "host-1",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = _sys_metric_probe_by_instance(data)
        target_io = _normal_summary()
        target_io.update({"avg_cpu_iowait_pct": 28.0, "load1m": 6.0})
        neighbor_io = _normal_summary()
        neighbor_io.update({"avg_cpu_iowait_pct": 34.0, "load1m": 7.0})
        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], target_io)
        _finish_sys_metrics_task(probes["service-b-1"]["task_id"], neighbor_io)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assessment = detail["latest_conclusion"]["cluster_assessment"]
        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "host_resource_contention"
        assert any(target["pressure"]["io_wait"] for target in assessment["compared_targets"])
        assert any(cmd["command_id"] == "cmd_io_latency" for cmd in detail["latest_conclusion"]["diagnostic_commands"])

    def test_downstream_pressure_is_reported_as_root_cause_node_not_first_alert(self, client: TestClient):
        repo.register_agent(
            "a2", "host-2", "10.0.0.2",
            capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"],
        )
        payload = _payload("service-a 延迟升高，逐层检查调用链真正根因")
        payload["context"]["instances"].append({
            "service_id": "service-b",
            "instance_id": "service-b-1",
            "host_id": "host-2",
            "agent_id": "a2",
            "pid": 4321,
            "environment": "production",
        })
        payload["context"]["dependencies"] = [{
            "source_service": "service-a",
            "target_service": "service-b",
            "relation": "CALLS",
            "confidence": "high",
            "source": "test_topology",
        }]

        data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
        probes = _sys_metric_probe_by_instance(data)
        downstream_hot = _normal_summary()
        downstream_hot.update({"avg_cpu_user_pct": 91.0, "avg_cpu_sys_pct": 6.0, "load1m": 12.0})
        _finish_sys_metrics_task(probes["service-a-1"]["task_id"], _normal_summary())
        _finish_sys_metrics_task(probes["service-b-1"]["task_id"], downstream_hot)

        detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
        assessment = detail["latest_conclusion"]["cluster_assessment"]
        assert detail["status"] == "COMPLETED"
        assert assessment["classification"] == "downstream_dependency"
        assert "service-b" in detail["target_scope"]["downstream_service_ids"]
        assert any(
            target["service_id"] == "service-b" and target["pressure"]["cpu"]
            for target in assessment["compared_targets"]
        )
        assert any(item["hypothesis"] == "same_host_noisy_neighbor" for item in assessment["ruled_out"])
        evidence_ids = {item["evidence_id"] for item in detail["evidence"]}
        assert set(assessment["evidence_refs"]).issubset(evidence_ids)


# ── 状态机收敛修复回归测试（2026-08-05 审计） ────────────────


def _force_deadline(diagnosis_id: str, *, past: bool = True) -> None:
    """直接改写会话 deadline_at，模拟 deadline 已过（store.update_session 不允许改此字段）。"""
    from server.app.database import new_session
    from server.app.models import DiagnosisSessionModel

    session = new_session()
    try:
        model = session.get(DiagnosisSessionModel, diagnosis_id)
        assert model is not None
        model.deadline_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
            if past
            else datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        session.commit()
    finally:
        session.close()


def test_scope_confirmation_converges_after_deadline(client: TestClient):
    """NEEDS_SCOPE_CONFIRMATION 在 deadline 后必须收敛，而不是每轮扫描抛非法迁移。"""
    payload = _payload()
    payload["context"]["instances"][0]["service_id"] = "service-b"
    payload["context"]["instances"][0]["instance_id"] = "service-b-1"
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"

    _force_deadline(data["diagnosis_id"], past=True)
    detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
    assert detail["status"] == "INSUFFICIENT_EVIDENCE"

    # 收敛后重复 GET 稳定，不再抛异常
    again = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}")
    assert again.status_code == 200
    assert again.json()["data"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_waiting_approval_converges_after_deadline(client: TestClient):
    """WAITING_APPROVAL 超 deadline 必须收敛（此前会抛非法迁移并永久卡死）。"""
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    task_id = data["child_task_ids"][0]
    repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    repo.transition_task(task_id, TaskStatus.DONE, "no structured output", Actor.ANALYZER)
    waiting = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
    assert waiting["status"] == "WAITING_APPROVAL"

    _force_deadline(data["diagnosis_id"], past=True)
    detail = client.get(f"/api/v1/diagnoses/{data['diagnosis_id']}").json()["data"]
    assert detail["status"] == "INSUFFICIENT_EVIDENCE"
    assert all(
        probe["status"] in {"TIMED_OUT", "COMPLETED", "FAILED", "REJECTED", "SKIPPED", "UNAVAILABLE"}
        for probe in detail["probes"]
    )


def test_cancel_diagnosis_session(client: TestClient):
    """取消 API: 非终态收敛到 USER_CANCELED，且取消活跃子任务。"""
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    assert data["status"] == "COLLECTING"
    task_id = data["child_task_ids"][0]

    resp = client.post(
        f"/api/v1/diagnoses/{data['diagnosis_id']}/cancel",
        json={"reason": "operator abort"},
    )
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["status"] == "USER_CANCELED"

    from server.app.common_utils import status_value
    task = repo.tasks.get(task_id)
    assert task is not None
    assert status_value(task.status) == "CANCELLED"

    # 终态幂等：重复取消不报错、状态不变
    again = client.post(f"/api/v1/diagnoses/{data['diagnosis_id']}/cancel", json={})
    assert again.status_code == 200
    assert again.json()["data"]["status"] == "USER_CANCELED"


def test_cancel_scope_confirmation_session(client: TestClient):
    """NEEDS_SCOPE_CONFIRMATION 会话可被用户取消（此前无任何出口）。"""
    payload = _payload()
    payload["context"]["instances"][0]["service_id"] = "service-b"
    data = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    assert data["status"] == "NEEDS_SCOPE_CONFIRMATION"

    resp = client.post(f"/api/v1/diagnoses/{data['diagnosis_id']}/cancel", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "USER_CANCELED"


def test_concluding_session_converges_after_interrupted_submit(client: TestClient):
    """CONCLUDING->COMPLETED 双事务提交中断后，重试 GET 幂等补提交终态。"""
    data = client.post("/api/v1/diagnoses", json=_payload()).json()["data"]
    diag_id = data["diagnosis_id"]
    conclusion = {
        "version": 1,
        "generated_at": "2026-08-05T00:00:00+00:00",
        "summary": "测试结论",
        "confidence_level": "高",
        "cluster_assessment": {
            "classification": "cpu_hotspot", "confidence": 0.9, "confidence_level": "高",
            "summary": "", "evidence_refs": [], "compared_targets": [], "ruled_out": [],
        },
        "root_location": {"type": "unknown", "target_ref": None, "evidence_refs": []},
        "domain_cause": {"type": "unknown", "subtype": "unknown", "evidence_refs": []},
        "findings": [],
        "root_cause_candidates": [],
        "ruled_out": [],
        "knowledge_refs": [],
        "actions": [],
        "diagnostic_commands": [],
        "recommendations": [],
        "limitations": [],
        "coverage": {"task_count": 0, "evidence_count": 0},
    }
    diagnosis_orchestrator.store.update_session(
        diag_id, status="CONCLUDING", conclusion_versions=[conclusion],
    )

    detail = client.get(f"/api/v1/diagnoses/{diag_id}").json()["data"]
    assert detail["status"] == "COMPLETED"
    # 不重复追加结论版本
    assert len(detail["conclusion_versions"]) == 1
