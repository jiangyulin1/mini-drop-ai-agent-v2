"""自适应多轮补证循环测试：证据契约缺失事实驱动新一轮受控采集。"""

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.state_machine import Actor, TaskStatus


TERMINAL = {
    "COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED",
    "BUDGET_EXHAUSTED", "TOPOLOGY_UNAVAILABLE", "USER_CANCELED", "FAILED",
}


@pytest.fixture
def client():
    return TestClient(app)


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
        capabilities=["sys_metrics", "log_scan", "runtime_snapshot"],
    )


def _payload(query: str) -> dict:
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
        # 禁用 R2 探针，避免补证循环进入 CPU Profile 审批等待，聚焦 R1 自适应轮。
        "budget": {"max_medium_risk_probes": 0},
    }


def _normal_summary() -> dict:
    return {
        "avg_cpu_user_pct": 18.0,
        "avg_cpu_sys_pct": 4.0,
        "avg_cpu_iowait_pct": 1.0,
        "load1m": 0.8,
        "thread_count": 20,
        "fd_count": 20,
        "vmrss_mb": 200,
        "process_cpu_core_usage": 0.4,
        "tcp_retransmit_pct": 0.1,
        "tcp_timeout_delta": 0,
        "container_memory_usage_ratio": 0.3,
        "target_fs_used_pct": 40.0,
        "root_fs_used_pct": 50.0,
    }


def _runtime_normal_summary() -> dict:
    return {
        "runtime_type": "go",
        "thread_count_max": 20,
        "lock_waiter_count_max": 0,
        "blocked_thread_ratio_max": 0.0,
        "uninterruptible_thread_count_max": 0,
        "cpu_tick_delta": 10,
    }


def _complete_probe_task(task_id: str, probe_id: str):
    repo.transition_task(task_id, TaskStatus.RUNNING, "agent accepted", Actor.SERVER)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    if probe_id == "runtime_thread_snapshot":
        artifact_type, summary = "runtime_metrics", _runtime_normal_summary()
    elif probe_id == "process_log_scan":
        artifact_type, summary = "log_scan", {
            "log_files": 2, "error_count": 0,
            "patterns": {"connection_refused": 0, "timeout": 0},
            "levels": {"INFO": 10, "ERROR": 0}, "top_errors": [],
        }
    else:
        artifact_type, summary = "sys_metrics", _normal_summary()
    repo.add_artifacts(task_id, [{
        "artifact_type": artifact_type,
        "object_key": f"tasks/{task_id}/{artifact_type}.json",
        "metadata": {"data": {"sample_count": 10, "summary": summary}},
    }])
    repo.transition_task(task_id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)


def _drive_until_terminal(client: TestClient, diagnosis_id: str) -> tuple[dict, list[str]]:
    """推进诊断直到终态；每次推进前完成所有已下发探针任务。"""
    detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    completed_probes: list[str] = []
    guard = 0
    while detail["status"] not in TERMINAL:
        guard += 1
        assert guard < 20, f"诊断未在有限步内结束: {detail['status']}"
        tasks = {
            task.id: task for task in repo.tasks.values()
            if task.request_params.get("options", {}).get("diagnosis_id") == diagnosis_id
        }
        active = [
            (task_id, task) for task_id, task in tasks.items()
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.UPLOADING, TaskStatus.ANALYZING}
        ]
        if not active:
            detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
            continue
        for task_id, task in active:
            probe_id = task.request_params.get("options", {}).get("probe_id", "host_process_metrics")
            _complete_probe_task(task_id, probe_id)
            completed_probes.append(probe_id)
        detail = client.get(f"/api/v1/diagnoses/{diagnosis_id}").json()["data"]
    return detail, completed_probes


def test_latency_diagnosis_collects_runtime_snapshot_via_adaptive_round(client, monkeypatch):
    """延迟症状必须触发运行时契约，最终采到 runtime_snapshot（修复记忆短板 #1）。"""
    _reset_repo(monkeypatch)
    payload = _payload("service-a 延迟升高，请求耗时从 10ms 涨到 2s，请定位原因")
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    first_round = {probe["probe_id"] for probe in created["probes"]}
    assert "host_process_metrics" in first_round

    detail, completed = _drive_until_terminal(client, diagnosis_id)
    assert detail["status"] in TERMINAL
    assert "runtime_thread_snapshot" in completed, f"runtime 探针未采到，实际: {completed}"


def test_adaptive_loop_runs_at_least_two_rounds(client, monkeypatch):
    """第一轮完成后仍缺契约事实时，必须安排新一轮探针而不是立即收敛。"""
    _reset_repo(monkeypatch)
    payload = _payload("service-a 延迟升高，部分请求超时，需要区分是自身还是依赖导致")
    created = client.post("/api/v1/diagnoses", json=payload).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    first_round = {probe["probe_id"] for probe in created["probes"]}

    detail, completed = _drive_until_terminal(client, diagnosis_id)
    assert detail["status"] in TERMINAL
    all_planned = {probe["probe_id"] for probe in detail["probes"]}
    # 第一轮不会一次采齐所有契约事实：之后必须出现第一轮之外的新探针。
    assert all_planned - first_round, f"没有出现第二轮补证探针，第一轮={first_round}，全部={all_planned}"
    assert len(completed) >= 2
