"""命中率修复回归：运行时锁/停顿分类、多目标复合下游、多实体复合。

这些是 ai_ops_v2 90 轮评测中失分最集中的两类模式：
- OB-SINGLE-GO-LOCK/RUNTIME-STALL/LATENCY 共 9 次 → insufficient_evidence（缺 runtime_snapshot）；
- OB-COMPOUND-PAYMENT-REDIS / CROSS-WORKER 共 9 次 → 只识别单下游（复合检测只看不同域）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.diagnosis.domain_analyzers import assess_cluster
from server.app.main import app, repo
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture
def client():
    return TestClient(app)


def _reset(monkeypatch, agents):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.delenv("MINI_DROP_ALLOWED_SERVICES", raising=False)
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    for agent_id, host_id, caps in agents:
        repo.register_agent(agent_id, host_id, f"10.0.0.{host_id[-1]}", capabilities=caps)


def _finish(task_id: str, probe_id: str, *, runtime: dict | None = None,
            refused: int = 0, sys_overrides: dict | None = None):
    repo.transition_task(task_id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    if probe_id == "runtime_thread_snapshot":
        artifact_type = "runtime_metrics"
        data = {"summary": runtime or {
            "runtime_type": "go", "thread_count_max": 30,
            "lock_waiter_count_max": 28, "blocked_thread_ratio_max": 0.96,
            "uninterruptible_thread_count_max": 0, "cpu_tick_delta": 2,
        }}
    elif probe_id == "process_log_scan":
        artifact_type = "log_scan"
        data = {"schema_version": "log_scan.v1", "log_files": [{
            "path": "/app/logs/svc.log", "level_counts": {"ERROR": refused},
            "patterns": {"connection_refused": refused, "timeout": 0},
            "error_lines": [
                {"line": i, "ts": "2026-08-11T00:00:00Z", "text": f"downstream refused {i}"}
                for i in range(refused)
            ],
        }]}
    else:
        artifact_type = "sys_metrics"
        summary = {
            "avg_cpu_user_pct": 15.0, "avg_cpu_sys_pct": 5.0, "avg_cpu_iowait_pct": 1.0,
            "load1m": 0.6, "thread_count": 30, "fd_count": 30, "vmrss_mb": 300,
            "process_cpu_core_usage": 0.4, "tcp_retransmit_pct": 0.1, "tcp_timeout_delta": 0,
            "container_memory_usage_ratio": 0.3, "target_fs_used_pct": 40.0, "root_fs_used_pct": 50.0,
        }
        summary.update(sys_overrides or {})
        data = {"summary": summary}
    repo.add_artifacts(task_id, [{
        "artifact_type": artifact_type,
        "object_key": f"tasks/{task_id}/{artifact_type}.json",
        "metadata": {"data": data},
    }])
    repo.transition_task(task_id, TaskStatus.DONE, "done", Actor.ANALYZER)


def _drive(client: TestClient, created: dict, *, finish_kwargs=None) -> dict:
    diagnosis_id = created["diagnosis_id"]
    detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    guard = 0
    while detail["status"] not in {
        "COMPLETED", "PARTIAL_COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED",
    }:
        guard += 1
        assert guard < 12, f"未收敛: {detail['status']}"
        for probe in detail["probes"]:
            task_id = probe.get("task_id")
            if not task_id:
                continue
            task = repo.tasks.get(task_id)
            if task is None or task.status in {TaskStatus.DONE, TaskStatus.FAILED}:
                continue
            kwargs = (finish_kwargs or {}).get(probe["probe_id"], {})
            _finish(task_id, probe["probe_id"], **kwargs)
        detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    return detail


def _payload(query: str, service: str, instances: list[dict], dependencies: list[dict] | None = None) -> dict:
    return {
        "query": query,
        "context": {
            "service_id": service,
            "environment": "production",
            "instances": instances,
            "dependencies": dependencies or [],
        },
        "budget_profile": "production_safe",
        "budget": {"max_medium_risk_probes": 0},
    }


def _inst(service: str, instance_id: str, host_id: str, agent_id: str, pid: int) -> dict:
    return {
        "service_id": service, "instance_id": instance_id, "host_id": host_id,
        "agent_id": agent_id, "pid": pid, "environment": "production",
    }


def test_go_lock_query_classifies_runtime_lock_contention(client, monkeypatch):
    """GO-LOCK（unknown_performance_issue）必须触发运行时契约并采 runtime_snapshot，
    锁信号存在时结案 runtime_lock_contention。"""
    _reset(monkeypatch, [("a1", "host-1", ["sys_metrics", "runtime_snapshot"])])
    payload = _payload(
        "Go 服务卡住，业务请求完全停止",
        "productcatalog",
        [_inst("productcatalog", "pc-1", "host-1", "a1", 1001)],
    )
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    first_round = {p["probe_id"] for p in created["probes"]}
    # 首轮广度扫描只采 host 指标；运行时契约在第二轮补证阶段触发。
    assert "host_process_metrics" in first_round
    assert "runtime_thread_snapshot" not in first_round

    detail = _drive(client, created)
    all_planned = {p["probe_id"] for p in detail["probes"]}
    assert "runtime_thread_snapshot" in all_planned, (
        f"GO-LOCK 补证轮未采运行时探针: {all_planned}"
    )
    assessment = (detail.get("latest_conclusion") or {}).get("cluster_assessment") or {}
    assert assessment.get("classification") == "runtime_lock_contention", (
        f"GO-LOCK 应归因运行时锁，实际: {assessment.get('classification')}"
    )
    assert assessment.get("root_location", {}).get("type") == "self"


def test_multi_target_downstream_is_compound(client, monkeypatch):
    """两个目标各自独立下游失败（cart 与 checkout 同域不同实体）→ compound_incident，
    不能被压成单个 downstream_dependency。"""
    _reset(monkeypatch, [
        ("a1", "host-1", ["sys_metrics", "log_scan"]),
        ("a2", "host-2", ["sys_metrics", "log_scan"]),
    ])
    payload = _payload(
        "购物车和结算同时失败，首页无法打开，请定位故障",
        "frontend",
        [
            _inst("frontend", "front-1", "host-1", "a1", 1001),
            _inst("cartservice", "cart-1", "host-1", "a1", 2001),
            _inst("checkoutservice", "checkout-1", "host-2", "a2", 3001),
        ],
        [
            {"source_service": "frontend", "target_service": "cartservice", "relation": "CALLS"},
            {"source_service": "frontend", "target_service": "checkoutservice", "relation": "CALLS"},
        ],
    )
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]

    def refused_for(probe_id):
        return {"refused": 6} if probe_id == "process_log_scan" else {}

    detail = _drive(client, created, finish_kwargs={p["probe_id"]: refused_for(p["probe_id"]) for p in created["probes"]})
    assessment = (detail.get("latest_conclusion") or {}).get("cluster_assessment") or {}
    causes = assessment.get("contributing_causes") or []
    assert len(causes) >= 2
    assert len({c.get("target_ref") for c in causes}) >= 2
    assert assessment.get("classification") == "compound_incident", (
        f"多目标下游失败应归因复合，实际: {assessment.get('classification')}"
    )


def _obs(svc: str, inst: str, pressure: dict, facts: dict | None = None,
         hotspot: bool = False) -> dict:
    return {
        "target": {"service_id": svc, "instance_id": inst, "host_id": "host-1",
                   "agent_id": "a1", "pid": 1001},
        "facts": {
            "process_cpu_core_usage": 0.3, "vmrss_mb": 200,
            "vmrss_slope_bytes_per_second": 0, "container_memory_usage_ratio": 0.5,
            "avg_cpu_sys_pct": 30.0, **(facts or {}),
        },
        "pressure": pressure,
        "evidence_weight": 0.85,
        "evidence_refs": [f"ev_{inst}"],
        "top_function": {"name": "fib", "percent": 60} if hotspot else {"name": "", "percent": 0},
        "log": None,
        "collector_type": "sys_metrics",
    }


def _scope(target_service: str, same_host: list[str], downstream: list[str]) -> dict:
    return {
        "target_service": target_service,
        "same_host_instance_ids": same_host,
        "downstream_service_ids": downstream,
        "instances": [],
    }


def test_noisy_cpu_neighbor_classifies_noisy_neighbor():
    """同宿主 CPU 噪声邻居：目标正常、邻居吃 CPU → same_host_noisy_neighbor，
    而不是被宿主 system CPU 高误判为 host_resource_contention。"""
    scope = _scope("checkoutservice", ["noise-1"], [])
    observations = [
        _obs("checkoutservice", "checkout-1", {}),
        _obs("noise-generator", "noise-1", {"cpu": True, "load": True},
             facts={"process_cpu_core_usage": 3.8}),
    ]
    assert assess_cluster(scope, observations)["classification"] == "same_host_noisy_neighbor"


def test_host_memory_contention_classifies_shared_resource():
    """宿主内存耗尽（目标与邻居同时受影响、目标非来源）→ host_resource_contention，
    而不是被误判为 same_host_noisy_neighbor。"""
    scope = _scope("checkoutservice", ["memgen-1"], [])
    observations = [
        _obs("checkoutservice", "checkout-1", {"memory": True}),
        _obs("memory-generator", "memgen-1", {"memory": True},
             facts={"vmrss_mb": 2048, "vmrss_slope_bytes_per_second": 5 * 1024 * 1024}),
    ]
    assert assess_cluster(scope, observations)["classification"] == "host_resource_contention"


def test_compound_memory_lock_collects_runtime_in_second_round(client, monkeypatch):
    """复合内存+锁：第一轮 host+log 检到内存泄漏（self_code），
    第二轮补证必须采 runtime_snapshot 检到锁，最终归因 compound_incident。"""
    _reset(monkeypatch, [("a1", "host-1", ["sys_metrics", "log_scan", "runtime_snapshot"])])
    payload = _payload(
        "python 服务内存持续增长且请求卡住，怀疑内存泄漏加锁等待",
        "pyservice",
        [_inst("pyservice", "py-1", "host-1", "a1", 1001)],
    )
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = created["diagnosis_id"]

    finish_kwargs = {
        "host_process_metrics": {"sys_overrides": {
            "vmrss_slope_bytes_per_second": 3 * 1024 * 1024,
            "vmrss_trend": "increasing",
            "vmrss_mb": 900,
            "container_memory_usage_ratio": 0.7,
        }},
    }
    detail = _drive(client, created, finish_kwargs=finish_kwargs)
    all_planned = {p["probe_id"] for p in detail["probes"]}
    assert "runtime_thread_snapshot" in all_planned, (
        f"复合案例第二轮应采运行时探针: {all_planned}"
    )
    assessment = (detail.get("latest_conclusion") or {}).get("cluster_assessment") or {}
    causes = assessment.get("contributing_causes") or []
    cause_classes = {c.get("classification") for c in causes}
    assert {"self_code_or_process_pressure", "runtime_lock_contention"}.issubset(cause_classes), (
        f"应同时检出内存与锁两个原因: {cause_classes}"
    )
    assert assessment.get("classification") == "compound_incident", (
        f"内存+锁应归因复合，实际: {assessment.get('classification')}"
    )


def test_healthy_runtime_low_lock_signal_is_not_lock_contention():
    """Go 服务正常 futex 停放（0.89/8）不能判成锁竞争（NEG/ROBUST 拒答回归守卫）。"""
    from server.app.diagnosis.domain_analyzers import analyze_observations
    obs = {
        "task_id": "task-healthy",
        "target": {"service_id": "frontend", "instance_id": "front-1"},
        "facts": {
            "runtime_type": "go", "thread_count_max": 9,
            "lock_waiter_count_max": 8, "blocked_thread_ratio_max": 0.89,
            "uninterruptible_thread_count_max": 0, "cpu_tick_delta": 30,
        },
        "pressure": {}, "log": None,
        "evidence_refs": ["ev_healthy"], "collection_status": "DONE",
    }
    findings = analyze_observations([obs])
    assert not any(item["finding_type"] == "lock_contention" for item in findings)


def test_memleak_self_is_not_shared_resource():
    """目标自身是内存泄漏来源 → self_code_or_process_pressure（回归守卫）。"""
    scope = _scope("productcatalog", [], [])
    observations = [
        _obs("productcatalog", "pc-1", {"memory": True},
             facts={"vmrss_mb": 1500, "vmrss_slope_bytes_per_second": 3 * 1024 * 1024}),
    ]
    assert assess_cluster(scope, observations)["classification"] == "self_code_or_process_pressure"
