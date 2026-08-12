"""Agent 进程发现（process_scan）与扫描 API 测试。

验证：
1. Agent 端 process_scan 采集器扫描输出结构；
2. Server 扫描 API 的校验（离线/能力缺失/不存在）；
3. 扫描任务的占位 PID 与选项透传；
4. 扫描 API 等待结果并解析进程清单。
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
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


def _register_agent(agent_id="worker-1", capabilities=None):
    repo.register_agent(
        agent_id,
        "worker-1",
        "192.168.10.11",
        version="0.1.0",
        os_info="Ubuntu 24.04",
        capabilities=capabilities or ["process_scan", "sys_metrics"],
    )


def _make_scan_task_done(agent_id="worker-1", processes=None, query="test"):
    """写入一个 DONE 的 process_scan 任务，模拟扫描已完成。"""
    task = repo.create_task(
        CreateTaskRequest(
            name=f"scan:{query or 'all'}:{agent_id}",
            agent_id=agent_id,
            target_pid=1,
            collector_type="process_scan",
            sample_rate=1,
            duration_sec=2,
            options={"query": query, "max_results": 300, "source": "process_scan_api"},
        ),
        idempotency_key=f"scan-{agent_id}-{query}-{int(time.time() // 2)}",
    )
    repo.transition_task(task.id, TaskStatus.RUNNING, "测试领取", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "测试上传", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "测试分析", Actor.ANALYZER)
    repo.transition_task(task.id, TaskStatus.DONE, "测试完成", Actor.ANALYZER)

    artifact_root = Path(os.environ["MINI_DROP_ARTIFACT_ROOT"])
    artifact_path = artifact_root / task.id
    artifact_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "process_scan.v1",
        "task_id": task.id,
        "processes": processes if processes is not None else [
            {"pid": 1234, "comm": "service-x", "cmdline": "/usr/bin/service-x --port 8080",
             "rss_mb": 84.3, "cpu_percent": 12.5, "cpu_seconds": 321.4, "threads": 12, "state": "S"},
            {"pid": 5678, "comm": "python3", "cmdline": "python3 worker.py", "rss_mb": 200.0,
             "cpu_percent": 1.2, "cpu_seconds": 10.0, "threads": 4, "state": "S"},
        ],
    }
    output_path = artifact_path / "process_scan.json"
    output_path.write_text(json.dumps(payload), encoding="utf-8")
    repo.add_artifacts(task.id, [{
        "artifact_type": "process_scan",
        "filename": "process_scan.json",
        "local_path": str(output_path),
        "content_type": "application/json",
        "size_bytes": output_path.stat().st_size,
        "metadata": {"schema_version": "process_scan.v1", "query": "test", "process_count": len(payload["processes"])},
    }])
    return task.id


def test_scan_requires_existing_agent(client):
    resp = client.post("/api/agents/missing/processes/scan", json={"query": "x"})
    assert resp.status_code == 404


def test_scan_requires_online_agent(client):
    _register_agent()
    repo.mark_offline_agents(timeout_sec=-1)  # cutoff 在未来，立即全部离线
    resp = client.post("/api/agents/worker-1/processes/scan", json={"query": "x", "timeout_sec": 2})
    assert resp.status_code == 409


def test_scan_requires_capability(client):
    _register_agent(capabilities=["sys_metrics"])
    resp = client.post("/api/agents/worker-1/processes/scan", json={"query": "x"})
    assert resp.status_code == 409
    assert "process_scan" in resp.json()["detail"]


def test_scan_creates_task_with_placeholder_pid(client):
    _register_agent()
    resp = client.post(
        "/api/agents/worker-1/processes/scan",
        json={"query": "service-x", "timeout_sec": 1},
    )
    # 1 秒超时内 Agent 心跳不会领取，任务仍在 PENDING，接口返回未完成
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "PENDING"
    task = repo.tasks[data["task_id"]]
    assert task.target_pid == 1  # 占位 PID
    assert task.collector_type == "process_scan"
    assert task.request_params["options"]["query"] == "service-x"
    assert task.request_params["options"]["source"] == "process_scan_api"


def test_scan_returns_processes_when_done(client, monkeypatch):
    _register_agent()
    task_id = _make_scan_task_done(query="service-x")

    # 让扫描 API 直接命中已完成的同一幂等任务，并跳过轮询等待
    monkeypatch.setattr("server.app.main.time.sleep", lambda _: None)
    resp = client.post("/api/agents/worker-1/processes/scan", json={"query": "service-x", "timeout_sec": 5})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "DONE"
    assert len(data["processes"]) == 2
    assert data["processes"][0]["pid"] == 1234
    assert data["processes"][0]["comm"] == "service-x"
    assert data["task_id"] == task_id  # 幂等键命中同一任务


def test_scan_parses_empty_result(client, monkeypatch):
    _register_agent()
    _make_scan_task_done(processes=[], query="")
    monkeypatch.setattr("server.app.main.time.sleep", lambda _: None)
    resp = client.post("/api/agents/worker-1/processes/scan", json={"query": "", "timeout_sec": 5})
    assert resp.status_code == 200
    assert resp.json()["data"]["processes"] == []


def test_process_scan_collector_schema(tmp_path):
    """采集器输出必须符合 process_scan.v1 契约（用假 /proc 目录）。"""
    import agent.mini_drop_agent.collectors.process_scan as ps_mod
    from agent.mini_drop_agent.collectors.base import CollectorTask
    from agent.mini_drop_agent.collectors.process_scan import ProcessScanCollector

    fake_proc = tmp_path / "proc"
    (fake_proc / "1234").mkdir(parents=True, exist_ok=True)
    (fake_proc / "1234" / "comm").write_text("service-x", encoding="utf-8")
    (fake_proc / "1234" / "cmdline").write_bytes(b"/usr/bin/service-x\x00--port\x008080")
    (fake_proc / "1234" / "stat").write_text("1234 (service-x) S 1 1 1 0 -1 4194560 100 0 0 0 10 5 0 0 20 0 1 0 100 0 0 0", encoding="utf-8")
    (fake_proc / "1234" / "statm").write_text("100 80 60 5 0 40 0", encoding="utf-8")
    (fake_proc / "1234" / "status").write_text("State:\tS (sleeping)\nThreads:\t12\nUid:\t0\t0\t0\t0\n", encoding="utf-8")

    def fake_snapshot():
        entry = str(fake_proc / "1234")
        stat = ps_mod.ProcessScanCollector._read_stat(entry, 1234)
        return {1234: {
            "ticks": stat["ticks"],
            "cpu_ticks_total": stat["cpu_ticks_total"],
            "rss_bytes": ps_mod.ProcessScanCollector._read_statm(entry),
            "comm": ps_mod.ProcessScanCollector._read_comm(entry),
            "cmdline": ps_mod.ProcessScanCollector._read_cmdline(entry),
            "state": ps_mod.ProcessScanCollector._read_status(entry).get("state", stat["state"]),
            "threads": ps_mod.ProcessScanCollector._read_status(entry).get("threads", 0),
            "username": ps_mod.ProcessScanCollector._read_status(entry).get("username", ""),
        }}

    collector = ProcessScanCollector()
    collector.OUTPUT_BASE = str(tmp_path / "out")
    with patch.object(ProcessScanCollector, "_snapshot", side_effect=[fake_snapshot(), fake_snapshot()]):
        result = collector.collect(CollectorTask(
            id="scan-test-1",
            collector_type="process_scan",
            target_pid=1,
            sample_rate=1,
            duration_sec=2,
            options={"query": "service-x"},
        ))
    assert result.ok
    assert result.artifacts[0]["artifact_type"] == "process_scan"
    payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == "process_scan.v1"
    assert payload["process_count"] == 1
    proc = payload["processes"][0]
    assert proc["pid"] == 1234
    assert proc["comm"] == "service-x"
    assert "service-x" in proc["cmdline"]
    assert proc["rss_mb"] == round(80 * ps_mod.PAGE_SIZE / 1024 / 1024, 1)
    assert proc["threads"] == 12
    assert proc["state"] == "S"
