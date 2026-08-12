"""恢复验证闭环（verification / manual-actions）与多轮引导（next_best_action）测试。"""

from fastapi.testclient import TestClient

import pytest

from server.app.database import init_db, reset_engine
from server.app.main import _judge_recovery, app
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path / "cache"))
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


# ── _judge_recovery 纯函数 ─────────────────────────────────


def test_judge_recovery_healthy_return():
    baseline = {"process_cpu_cores": 1.5, "iowait_ratio": 0.05, "rss_bytes": 100_000_000}
    current = {"process_cpu_cores": 0.4, "iowait_ratio": 0.01, "rss_bytes": 40_000_000}
    result = _judge_recovery(baseline, current)
    assert result["status"] == "recovered"
    assert result["metrics"]["process_cpu_cores"]["verdict"] == "recovered"


def test_judge_recovery_degraded():
    baseline = {"process_cpu_cores": 1.0, "iowait_ratio": 0.02, "rss_bytes": 100_000_000}
    current = {"process_cpu_cores": 2.5, "iowait_ratio": 0.02, "rss_bytes": 100_000_000}
    result = _judge_recovery(baseline, current)
    assert result["status"] == "degraded"


def test_judge_recovery_no_change():
    baseline = {"process_cpu_cores": 1.0, "iowait_ratio": 0.03}
    current = {"process_cpu_cores": 0.9, "iowait_ratio": 0.03}
    result = _judge_recovery(baseline, current)
    assert result["status"] == "not_recovered"


def test_judge_recovery_indeterminate():
    assert _judge_recovery({}, {}).get("status") == "indeterminate"
    assert _judge_recovery({"process_cpu_cores": 1.0}, {}).get("status") == "indeterminate"


def test_judge_recovery_normal_when_both_low():
    baseline = {"process_cpu_cores": 0.01, "iowait_ratio": 0.0}
    current = {"process_cpu_cores": 0.015, "iowait_ratio": 0.005}
    result = _judge_recovery(baseline, current)
    assert result["status"] == "recovered"  # 本就接近 0，视为正常


def test_judge_recovery_ignores_unchanged_rss_when_absolute_guards_are_healthy():
    result = _judge_recovery(
        {
            "rss_bytes": 50_000_000,
            "container_memory_usage_ratio": 0.05,
            "oom_kill_delta": 0,
            "filesystem_used_ratio": 0.2,
            "tcp_retransmit_ratio": 0,
            "tcp_timeout_delta": 0,
        },
        {
            "rss_bytes": 52_000_000,
            "container_memory_usage_ratio": 0.06,
            "oom_kill_delta": 0,
            "filesystem_used_ratio": 0.2,
            "tcp_retransmit_ratio": 0,
            "tcp_timeout_delta": 0,
        },
    )

    assert result["metrics"]["rss_bytes"]["verdict"] == "unchanged"
    assert result["status"] == "recovered"


# ── manual-actions API ─────────────────────────────────────


def _create_case(client) -> str:
    resp = client.post("/api/v1/cases", json={
        "title": "测试恢复闭环",
        "problem_description": "service-x 变慢",
        "recovery_goal": "确认原因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "service-x", "instances": [], "dependencies": []},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["case_id"]


def test_manual_action_records_event(client):
    case_id = _create_case(client)
    resp = client.post(f"/api/v1/cases/{case_id}/manual-actions", json={
        "action_ref": "rec_optimization",
        "result": "completed",
        "notes": "已重启实例并观察 5 分钟",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["record"]["result"] == "completed"

    events = client.get(f"/api/v1/cases/{case_id}/events").json()["data"]
    items = events.get("items", events) if isinstance(events, dict) else events
    assert any(item.get("event_type") == "manual_action" for item in items)


def test_manual_action_rejects_bad_result(client):
    case_id = _create_case(client)
    resp = client.post(f"/api/v1/cases/{case_id}/manual-actions", json={"result": "hacked"})
    assert resp.status_code == 400


def test_verification_requires_diagnosis(client):
    case_id = _create_case(client)
    resp = client.post(f"/api/v1/cases/{case_id}/verification", json={})
    assert resp.status_code == 409
    assert "诊断" in resp.json()["detail"] or "diagnosis" in resp.json()["detail"].lower()


# ── next_best_action（多轮引导）────────────────────────────


def test_next_best_action_from_orchestrator():
    from server.app.diagnosis.orchestrator import DiagnosisOrchestrator

    orchestrator = DiagnosisOrchestrator.__new__(DiagnosisOrchestrator)

    # 证据不足 → 建议探针
    assessment = {
        "classification": "insufficient_evidence",
        "root_location": {"type": "unknown"},
        "domain_cause": {"type": "unknown"},
    }
    next_action = orchestrator._build_next_best_action(assessment, ["缺少目标 PID 对应的 Profile TopN"], None)
    assert next_action["type"] == "probe"
    assert next_action["probe_id"] == "process_cpu_profile"
    assert next_action["needs_approval"] is True

    # I/O 缺口 → 建议块设备延迟探针
    assessment_io = {"classification": "insufficient_evidence", "root_location": {"type": "unknown"}, "domain_cause": {"type": "unknown"}}
    next_io = orchestrator._build_next_best_action(assessment_io, ["缺少块设备延迟直方图"], None)
    assert next_io["probe_id"] == "process_io_latency"

    # 无特定缺口 → 建议日志扫描
    next_log = orchestrator._build_next_best_action(assessment, ["缺少区分性证据"], None)
    assert next_log["probe_id"] == "process_log_scan"
    assert next_log["needs_approval"] is False

    # 已有根因 → 建议验证恢复
    assessment_ok = {
        "classification": "root_cause_identified",
        "root_location": {"type": "self", "target_ref": "svc-1"},
        "domain_cause": {"type": "cpu"},
    }
    next_verify = orchestrator._build_next_best_action(assessment_ok, [], None)
    assert next_verify["type"] == "verify"
    assert "验证" in next_verify["title"]
