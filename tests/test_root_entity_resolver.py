"""RootEntityResolver 测试：Payment/Redis 下游实体解析为稳定服务 ID。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.state_machine import Actor, TaskStatus

from server.app.diagnosis.root_entity_resolver import resolve_root_entity


@pytest.fixture
def client():
    return TestClient(app)


def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.delenv("MINI_DROP_ALLOWED_SERVICES", raising=False)
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    repo.register_agent(
        "a1", "host-1", "10.0.0.1",
        capabilities=["sys_metrics", "log_scan"],
    )


def _finish(task_id: str, probe_id: str, *, connection_refused: int = 0):
    repo.transition_task(task_id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    if probe_id == "process_log_scan":
        artifact_type = "log_scan"
        summary = {
            "schema_version": "log_scan.v1",
            "log_files": [{
                "path": "/app/logs/service.log",
                "size_bytes": 1024,
                "tail_bytes": 1024,
                "level_counts": {"INFO": 5, "ERROR": connection_refused},
                "patterns": {"connection_refused": connection_refused, "timeout": 0},
                "error_lines": [
                    {"line": index, "ts": "2026-08-11T00:00:00Z",
                     "text": f"connection refused to paymentservice: {index}"}
                    for index in range(connection_refused)
                ],
            }],
        }
    else:
        artifact_type = "sys_metrics"
        summary = {
            "avg_cpu_user_pct": 18.0, "avg_cpu_sys_pct": 4.0, "avg_cpu_iowait_pct": 1.0,
            "load1m": 0.8, "thread_count": 20, "fd_count": 20, "vmrss_mb": 200,
            "process_cpu_core_usage": 0.4, "tcp_retransmit_pct": 0.1, "tcp_timeout_delta": 0,
            "container_memory_usage_ratio": 0.3, "target_fs_used_pct": 40.0, "root_fs_used_pct": 50.0,
        }
    data = {"sample_count": 10, "summary": summary}
    if artifact_type == "log_scan":
        # log_scan.v1 顶层直接携带 log_files 列表；_log_summary 依赖该结构。
        data = summary
    repo.add_artifacts(task_id, [{
        "artifact_type": artifact_type,
        "object_key": f"tasks/{task_id}/{artifact_type}.json",
        "metadata": {"data": data},
    }])
    repo.transition_task(task_id, TaskStatus.DONE, "done", Actor.ANALYZER)


def _drive_to_conclusion(client: TestClient, created: dict) -> dict:
    diagnosis_id = created["diagnosis_id"]
    detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    guard = 0
    while detail["status"] not in {
        "COMPLETED", "PARTIAL_COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED",
    }:
        guard += 1
        assert guard < 15, f"未收敛: {detail['status']}"
        for probe in detail["probes"]:
            task_id = probe.get("task_id")
            if not task_id:
                continue
            task = repo.tasks.get(task_id)
            if task is None or task.status in {TaskStatus.DONE, TaskStatus.FAILED}:
                continue
            target = (probe.get("target") or {}).get("service_id")
            refused = 6 if probe["probe_id"] == "process_log_scan" and target == "checkoutservice" else 0
            _finish(task_id, probe["probe_id"], connection_refused=refused)
        detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    return detail


def test_orchestrator_populates_root_entity_for_payment(client, monkeypatch):
    """端到端：下游依赖归因的结论必须带稳定 root_entity=paymentservice。"""
    _reset_repo(monkeypatch)
    payload = {
        "query": "checkout 结算超时，怀疑下游支付服务不可用",
        "context": {
            "service_id": "checkoutservice",
            "environment": "production",
            "instances": [
                {"service_id": "checkoutservice", "instance_id": "checkout-1",
                 "host_id": "host-1", "agent_id": "a1", "pid": 1001, "environment": "production"},
                {"service_id": "paymentservice", "instance_id": "payment-1",
                 "host_id": "host-1", "agent_id": "a1", "pid": 1002, "environment": "production"},
            ],
            "dependencies": [
                {"source_service": "checkoutservice", "target_service": "paymentservice", "relation": "CALLS"},
            ],
        },
        "budget_profile": "production_safe",
        "budget": {"max_medium_risk_probes": 0},
    }
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    detail = _drive_to_conclusion(client, created)
    assessment = (detail.get("latest_conclusion") or {}).get("cluster_assessment") or {}
    assert assessment.get("classification") == "downstream_dependency"
    assert assessment.get("root_entity") == "paymentservice", (
        f"root_entity 未解析为稳定服务 ID，实际: {assessment.get('root_entity')}"
    )


def _scope(target_service: str, dependencies: list[dict], downstream: list[str]) -> dict:
    return {
        "target_service": target_service,
        "dependencies": dependencies,
        "downstream_service_ids": downstream,
        "instances": [],
    }


def _observation(service: str, instance_id: str, facts: dict | None = None) -> dict:
    return {
        "task_id": f"task-{instance_id}",
        "target": {"service_id": service, "instance_id": instance_id},
        "facts": facts or {},
        "log": None,
        "evidence_refs": [f"ev_{instance_id}"],
    }


def test_payment_downstream_resolves_to_paymentservice():
    assessment = {
        "classification": "downstream_dependency",
        "root_location": {"type": "downstream"},
    }
    scope = _scope("checkoutservice", [
        {"source_service": "checkoutservice", "target_service": "paymentservice", "relation": "CALLS"},
    ], ["paymentservice"])
    observations = [
        _observation("checkoutservice", "checkout-1"),
        _observation("paymentservice", "paymentservice-1"),
    ]
    assert resolve_root_entity(assessment, scope, observations) == "paymentservice"


def test_redis_downstream_resolves_to_redis_cart():
    assessment = {
        "classification": "downstream_dependency",
        "root_location": {"type": "downstream"},
    }
    scope = _scope("cartservice", [
        {"source_service": "cartservice", "target_service": "redis-cart", "relation": "READS_FROM"},
    ], ["redis-cart"])
    observations = [
        _observation("cartservice", "cart-1"),
        _observation("redis-cart", "redis-cart-1"),
    ]
    assert resolve_root_entity(assessment, scope, observations) == "redis-cart"


def test_downstream_prefers_evidence_pointed_service():
    assessment = {
        "classification": "downstream_dependency",
        "root_location": {"type": "downstream"},
    }
    scope = _scope("checkoutservice", [
        {"source_service": "checkoutservice", "target_service": "paymentservice", "relation": "CALLS"},
        {"source_service": "checkoutservice", "target_service": "shippingservice", "relation": "CALLS"},
    ], ["paymentservice", "shippingservice"])
    # 观测只覆盖 paymentservice → 应解析为 paymentservice，而不是多下游时返回 None。
    observations = [_observation("paymentservice", "paymentservice-1")]
    assert resolve_root_entity(assessment, scope, observations) == "paymentservice"


def test_ambiguous_downstream_without_evidence_returns_none():
    assessment = {
        "classification": "downstream_dependency",
        "root_location": {"type": "downstream"},
    }
    scope = _scope("checkoutservice", [
        {"source_service": "checkoutservice", "target_service": "paymentservice", "relation": "CALLS"},
        {"source_service": "checkoutservice", "target_service": "shippingservice", "relation": "CALLS"},
    ], ["paymentservice", "shippingservice"])
    observations = [_observation("checkoutservice", "checkout-1")]
    assert resolve_root_entity(assessment, scope, observations) is None


def test_self_case_resolves_to_target_service():
    assessment = {
        "classification": "self_code_or_process_pressure",
        "root_location": {"type": "self"},
    }
    scope = _scope("productcatalogservice", [], [])
    assert resolve_root_entity(assessment, scope, []) == "productcatalogservice"


def test_same_host_noise_resolves_to_host():
    assessment = {
        "classification": "same_host_noisy_neighbor",
        "root_location": {"type": "same_host"},
    }
    scope = {
        "target_service": "productcatalogservice",
        "dependencies": [],
        "downstream_service_ids": [],
        "instances": [{
            "service_id": "productcatalogservice",
            "instance_id": "productcatalogservice-worker1-1",
            "host_id": "worker1",
        }],
    }
    assert resolve_root_entity(assessment, scope, []) == "worker1"


def test_endpoint_probe_facts_point_to_downstream():
    assessment = {
        "classification": "downstream_dependency",
        "root_location": {"type": "downstream"},
    }
    scope = _scope("checkoutservice", [
        {"source_service": "checkoutservice", "target_service": "paymentservice", "relation": "CALLS"},
        {"source_service": "checkoutservice", "target_service": "shippingservice", "relation": "CALLS"},
    ], ["paymentservice", "shippingservice"])
    observations = [{
        "task_id": "task-conn",
        "target": {"service_id": "checkoutservice", "instance_id": "checkout-1"},
        "facts": {
            "endpoint.reachable": False,
            "endpoint.downstream_service": "paymentservice",
        },
        "log": None,
        "evidence_refs": ["ev_conn"],
    }]
    assert resolve_root_entity(assessment, scope, observations) == "paymentservice"
