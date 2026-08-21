"""E4: PlanDriver —— Pi 规划，Mini-Drop 执行低风险调查。

覆盖退出条件：Supervisor 自动调度 READ_LOW、连续三轮补证 E2E、重复采集去重、
中断（取消）与 Task 完成唤醒。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import PLAN_DRIVER, _run_plan_driver_pass, app, repo
from server.app.models import Base
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    monkeypatch.setenv("MINI_DROP_PLAN_DRIVER_ENABLED", "0")  # 关闭后台扫描，测驱动本身
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    return TestClient(app)


def _create_case(client: TestClient, *, cluster: bool = False) -> dict:
    target_scope = {"service_id": "checkout"}
    if cluster:
        target_scope = {"cluster_id": "prod-a", "service_id": "checkout"}
    created = client.post("/api/v1/cases", json={
        "title": "plan-driver-case",
        "problem_description": "支付接口超时，请定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": target_scope,
    })
    assert created.status_code == 200, created.text
    return created.json()["data"]


def _register_agent() -> None:
    repo.register_agent(
        "agent-a", "node-a", "192.168.10.11", version="0.3.0",
        capabilities=[
            "sys_metrics", "log_scan", "runtime_snapshot", "memory_smaps",
            "service:checkout", "fault_domain:zone-a",
        ],
    )


def _put_plan(client: TestClient, case: dict, steps: list[dict], *, revision: int = 0) -> dict:
    resp = client.put(f"/api/v1/cases/{case['case_id']}/plans", json={
        "goal": "验证根因假设",
        "expected_case_row_version": case["row_version"],
        "expected_scope_revision": case["scope_revision"],
        "expected_plan_revision": revision,
        "steps": steps,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _step(collector_id: str, priority: int = 60, **overrides) -> dict:
    return {
        "kind": "COLLECTION",
        "collector_id": collector_id,
        "target_refs": ["instance:inst-a"],
        "purpose": f"验证 {collector_id}",
        "hypothesis_refs": ["hyp_1"],
        "priority": priority,
        "risk": "READ_LOW",
        "status": "QUEUED",
        **overrides,
    }


def _complete_task(task_id: str) -> None:
    repo.transition_task(task_id, TaskStatus.RUNNING, "agent 开始采集", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.UPLOADING, "agent 上传产物", Actor.AGENT)
    repo.transition_task(task_id, TaskStatus.ANALYZING, "analyzer 开始分析", Actor.WEB)
    repo.transition_task(task_id, TaskStatus.DONE, "agent 完成采集", Actor.AGENT)


def test_driver_dispatches_read_low_steps_to_tasks(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics"), _step("log_scan")])
    step_ids = [s["step_id"] for s in plan["steps"]]

    result = client.post(f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={})
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["outcome"] == "DISPATCHED"
    # 两个 READ_LOW 步骤都被调度为单目标 Task
    assert len(data["dispatched"]) == 2
    assert all(d["kind"] == "single" for d in data["dispatched"])
    assert all(d["status"] == "RUNNING" for d in data["dispatched"])
    assert all(d["proposal_id"] and d["collection_request_id"] for d in data["dispatched"])
    # Step 状态机推进
    steps = client.get(f"/api/v1/cases/{case['case_id']}/plans/current").json()["data"]["steps"]
    by_id = {s["step_id"]: s for s in steps}
    assert all(by_id[sid]["status"] == "RUNNING" for sid in step_ids)
    # 每个 Step 有独立 Task
    for d in data["dispatched"]:
        assert d["task_id"]
    proposals = repo.list_collection_proposals(case["case_id"], "tenant-a")
    requests = repo.list_collection_requests(case["case_id"], "tenant-a")
    assert {item["plan_step_id"] for item in proposals} == set(step_ids)
    assert {item["plan_step_id"] for item in requests} == set(step_ids)
    assert all(item["plan_revision"] == plan["plan_revision"] for item in requests)


def test_task_done_wake_marks_step_completed(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics")])
    step_id = plan["steps"][0]["step_id"]
    dispatched = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
    ).json()["data"]["dispatched"]
    task_id = dispatched[0]["task_id"]

    # Task 完成 → 唤醒 → Step COMPLETED
    _complete_task(task_id)
    woke = PLAN_DRIVER.on_task_done(case["case_id"], "tenant-a", task_id, status="DONE")
    assert woke["outcome"] == "DISPATCHED"
    steps = client.get(f"/api/v1/cases/{case['case_id']}/plans/current").json()["data"]["steps"]
    assert steps[0]["status"] == "COMPLETED"


def test_three_round_evidence_e2e(client: TestClient):
    """连续三轮补证：每轮新 Plan Revision → 调度 → Task 完成 → Step COMPLETED。"""
    _register_agent()
    case = _create_case(client)
    for revision, collector in enumerate(("sys_metrics", "log_scan", "runtime_snapshot"), start=1):
        if revision == 1:
            plan = _put_plan(client, case, [_step(collector)], revision=0)
        else:
            case = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
            plan = _put_plan(client, case, [_step(collector)], revision=revision - 1)
        step_id = plan["steps"][0]["step_id"]
        dispatched = client.post(
            f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
        ).json()["data"]
        assert dispatched["outcome"] == "DISPATCHED", dispatched
        task_id = dispatched["dispatched"][0]["task_id"]
        _complete_task(task_id)
        PLAN_DRIVER.on_task_done(case["case_id"], "tenant-a", task_id, status="DONE")
        steps = client.get(f"/api/v1/cases/{case['case_id']}/plans/current").json()["data"]["steps"]
        assert steps[0]["status"] == "COMPLETED", steps
        assert steps[0]["collector_id"] == collector
    # 三轮后 Case 保持非终态（无终结动作），证据链完整
    final = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    assert final["state"] not in {"STOPPED", "RESOLVED"}


def test_duplicate_collection_is_reused_not_recollected(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics", priority=80)])
    step_id = plan["steps"][0]["step_id"]
    dispatched = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
    ).json()["data"]["dispatched"]
    _complete_task(dispatched[0]["task_id"])
    PLAN_DRIVER.on_task_done(case["case_id"], "tenant-a", dispatched[0]["task_id"], status="DONE")

    # 新 Plan Revision 再次请求同 collector → 复用，不重采
    case = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    plan2 = _put_plan(client, case, [_step("sys_metrics")], revision=1)
    result = client.post(f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={}).json()["data"]
    assert result["reused"] == [plan2["steps"][0]["step_id"]]
    assert result["dispatched"] == []


def test_cancel_running_step_cancels_native_task(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics")])
    step_id = plan["steps"][0]["step_id"]
    dispatched = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
    ).json()["data"]["dispatched"]
    task_id = dispatched[0]["task_id"]
    cancelled = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{step_id}/cancel", json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    task = repo.tasks[task_id]
    assert str(task.status) == "CANCELLED"


def test_retarget_running_step_cancels_old_native_task(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics")])
    step_id = plan["steps"][0]["step_id"]
    dispatched = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
    ).json()["data"]["dispatched"]
    task_id = dispatched[0]["task_id"]
    retarget = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{step_id}/retarget",
        json={"target_refs": ["instance:inst-b"], "collector_id": "log_scan"},
    )
    assert retarget.status_code == 200, retarget.text
    assert str(repo.tasks[task_id].status) == "CANCELLED"
    step = retarget.json()["data"]
    assert step["status"] == "CANCEL_REQUESTED"


def test_stopping_case_cancels_case_derived_tasks(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics")])
    dispatched = client.post(
        f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={},
    ).json()["data"]["dispatched"]
    task_id = dispatched[0]["task_id"]
    case = client.get(f"/api/v1/cases/{case['case_id']}").json()["data"]
    stopped = client.post(
        f"/api/v1/cases/{case['case_id']}/stop",
        json={"reason": "用户停止", "expected_row_version": case["row_version"]},
    )
    assert stopped.status_code == 200, stopped.text
    assert str(repo.tasks[task_id].status) == "CANCELLED"


def test_cancelled_step_is_not_dispatched(client: TestClient):
    _register_agent()
    case = _create_case(client)
    plan = _put_plan(client, case, [_step("sys_metrics"), _step("log_scan")])
    cancelled_id = plan["steps"][0]["step_id"]
    cancelled = client.post(
        f"/api/v1/cases/{case['case_id']}/steps/{cancelled_id}/cancel", json={},
    )
    assert cancelled.status_code == 200
    result = client.post(f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={}).json()["data"]
    assert result["outcome"] == "DISPATCHED"
    # 已取消步骤不进入调度；剩余步骤正常调度
    dispatched_ids = {d["step_id"] for d in result["dispatched"]}
    assert cancelled_id not in dispatched_ids
    assert len(result["dispatched"]) == 1


def test_data_driven_entry_reuses_existing_task(client: TestClient):
    """数据驱动入口：Case 由已完成 Task 建立 → 计划请求同 collector → 复用不重采。"""
    _register_agent()
    from server.app.schemas import CreateTaskRequest
    task = repo.create_task(CreateTaskRequest(
        name="existing-sys-metrics",
        agent_id="agent-a",
        target_pid=1,
        collector_type="sys_metrics",
        sample_rate=11,
        duration_sec=15,
        options={"source": "initial_entry", "case_id": "pending"},
    ))
    repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "metadata": {"samples": 100, "window_sec": 15},
    }])
    _complete_task(task.id)
    # 数据驱动入口：首屏已有 Task 提交给 AI 建立 Case
    created = client.post("/api/v1/cases", json={
        "title": "data-driven-case",
        "problem_description": "基于已有采集数据定位根因",
        "recovery_goal": "定位根因并给出可验证建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
        "initial_tasks": [task.id],
    })
    assert created.status_code == 200, created.text
    case = created.json()["data"]
    plan = _put_plan(client, case, [_step("sys_metrics")])
    result = client.post(f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={}).json()["data"]
    # 已有 sys_metrics 证据 → 复用，不补采
    assert result["reused"] == [plan["steps"][0]["step_id"]]
    assert result["dispatched"] == []


def test_cluster_step_fans_out_via_membership(client: TestClient):
    _register_agent()
    repo.register_agent(
        "agent-b", "node-b", "192.168.10.12", version="0.3.0",
        capabilities=["sys_metrics", "service:checkout", "fault_domain:zone-b"],
    )
    case = _create_case(client, cluster=True)
    plan = _put_plan(client, case, [_step(
        "sys_metrics", selection_strategy="ALL_IN_SCOPE",
        target_refs=["cluster:prod-a"],
    )])
    result = client.post(f"/api/v1/cases/{case['case_id']}/agent/plan-driver", json={}).json()["data"]
    assert result["outcome"] == "DISPATCHED"
    cluster = result["dispatched"][0]
    assert cluster["kind"] == "cluster"
    assert cluster["targets"] == 2
    # Fanout Run 持久化
    runs = client.get(f"/api/v1/cases/{case['case_id']}/fanout").json()["data"]["items"]
    assert any(run["run_id"] == cluster["run_id"] for run in runs)
    assert runs[0]["strategy"] == "ALL_IN_SCOPE"


def _case_task_ids(case_id: str) -> list[str]:
    return [
        str(task.id) for task in repo.tasks.values()
        if ((task.request_params or {}).get("options") or {}).get("case_id") == case_id
    ]


def test_auto_read_low_flag_blocks_background_scan(client: TestClient, monkeypatch):
    monkeypatch.setenv("MINI_DROP_AGENT_AUTO_READ_LOW", "0")
    _register_agent()
    case = _create_case(client)
    _put_plan(client, case, [_step("sys_metrics")])
    _run_plan_driver_pass()
    assert _case_task_ids(case["case_id"]) == []


def test_auto_read_low_flag_enables_background_scan(client: TestClient, monkeypatch):
    monkeypatch.setenv("MINI_DROP_AGENT_AUTO_READ_LOW", "1")
    _register_agent()
    case = _create_case(client)
    _put_plan(client, case, [_step("sys_metrics")])
    _run_plan_driver_pass()
    assert len(_case_task_ids(case["case_id"])) == 1
