"""受控修复动作（Actuation Gateway）测试。

验证首个可执行、可回滚动作 ``mini-drop.cleanup-expired-cache``：
1. dry-run 只读列出过期产物；
2. execute 必须先 dry-run（无 attempt 拒绝）；
3. execute 把文件移入隔离区（移动而非删除，可回滚）；
4. 幂等：重复执行不重复处理；
5. rollback 恢复缓存；
6. 路径越界被拒绝；
7. 未开放动作（policy_only）拒绝执行；
8. 策略硬拒绝不可被绕过。
"""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("MINI_DROP_QUARANTINE_ROOT", str(tmp_path / "quarantine"))
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


def _seed_expired_cache(root: Path, task_id: str = "task_20260701_000000_abc123", age_days: float = 10.0):
    """写入一个超过保留期的任务产物目录。"""
    task_dir = root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "sys_metrics.json").write_text('{"schema_version": "sys_metrics.v2"}', encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(task_dir, (old, old))
    for child in task_dir.iterdir():
        os.utime(child, (old, old))
    return task_dir


def _seed_fresh_cache(root: Path, task_id: str = "task_20260801_000000_abc123"):
    task_dir = root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "sys_metrics.json").write_text("{}", encoding="utf-8")
    return task_dir


def _tenant() -> dict:
    return {"tenant_id": "tenant-a"}


def test_dry_run_lists_only_expired(client, tmp_path):
    cache = Path(os.environ["MINI_DROP_ARTIFACT_ROOT"])
    _seed_expired_cache(cache, "task_20260701_000000_expired1")
    _seed_expired_cache(cache, "task_20260702_000000_expired2")
    _seed_fresh_cache(cache, "task_20260801_000000_fresh")

    resp = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/dry-run",
        json={**_tenant(), "parameters": {"retention_days": 7}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["action_id"] == "mini-drop.cleanup-expired-cache"
    assert data["stage"] == "DRY_RUN_COMPLETED"
    assert data["dry_run"]["candidate_count"] == 2
    task_ids = {item["task_id"] for item in data["dry_run"]["items"]}
    assert "task_20260701_000000_expired1" in task_ids
    assert "task_20260801_000000_fresh" not in task_ids
    # dry-run 不得移动任何文件
    assert (cache / "task_20260701_000000_expired1").is_dir()


def test_execute_requires_prior_dry_run(client):
    # 缺失 attempt_id → 400
    resp = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/execute",
        json={**_tenant(), "dry_run_passed": True, "rollback_ready": True},
    )
    assert resp.status_code == 400
    assert "dry_run_attempt_id" in resp.json()["detail"]
    # 无效 attempt_id → 409（必须先 dry-run）
    resp2 = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/execute",
        json={**_tenant(), "dry_run_attempt_id": "act_missing", "dry_run_passed": True, "rollback_ready": True},
    )
    assert resp2.status_code == 409
    assert "dry-run" in resp2.json()["detail"]


def test_execute_moves_to_quarantine_and_is_idempotent(client, tmp_path):
    cache = Path(os.environ["MINI_DROP_ARTIFACT_ROOT"])
    quarantine = Path(os.environ["MINI_DROP_QUARANTINE_ROOT"])
    _seed_expired_cache(cache, "task_20260701_000000_expired1")

    dry = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/dry-run",
        json={**_tenant(), "parameters": {"retention_days": 7}},
    ).json()["data"]
    attempt_id = dry["attempt_id"]

    resp = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/execute",
        json={**_tenant(), "dry_run_attempt_id": attempt_id, "dry_run_passed": True, "rollback_ready": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["stage"] == "COMPLETED"
    assert len(data["executed"]) == 1
    # 源目录已移入隔离区
    assert not (cache / "task_20260701_000000_expired1").exists()
    assert (quarantine / "task_20260701_000000_expired1").is_dir()

    # 同一 dry-run attempt 再次执行：幂等重放，返回已执行结果且不重复处理
    resp2 = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/execute",
        json={**_tenant(), "dry_run_attempt_id": attempt_id, "dry_run_passed": True, "rollback_ready": True},
    )
    assert resp2.status_code == 200
    assert resp2.json()["data"]["idempotent_replay"] is True
    assert resp2.json()["data"]["executed"] == data["executed"]


def test_rollback_restores_quarantine(client):
    cache = Path(os.environ["MINI_DROP_ARTIFACT_ROOT"])
    quarantine = Path(os.environ["MINI_DROP_QUARANTINE_ROOT"])
    _seed_expired_cache(cache, "task_20260701_000000_expired1")

    dry = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/dry-run",
        json={**_tenant(), "parameters": {"retention_days": 7}},
    ).json()["data"]
    client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/execute",
        json={**_tenant(), "dry_run_attempt_id": dry["attempt_id"], "dry_run_passed": True, "rollback_ready": True},
    )
    assert not (cache / "task_20260701_000000_expired1").exists()
    assert (quarantine / "task_20260701_000000_expired1").is_dir()

    resp = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/rollback",
        json={**_tenant()},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["stage"] == "COMPLETED"
    assert (cache / "task_20260701_000000_expired1").is_dir()
    assert not (quarantine / "task_20260701_000000_expired1").exists()


def test_policy_only_action_cannot_execute(client):
    # service 动作仍是 policy_only，dry-run 必须拒绝
    resp = client.post(
        "/api/v1/actions/service.drain-unhealthy-instance/dry-run",
        json={**_tenant(), "parameters": {}},
    )
    assert resp.status_code == 409
    assert "policy_only" in resp.json()["detail"]


def test_path_escape_rejected(client):
    # 直接构造越界路径的 dry-run item 应被拒绝
    from server.app.diagnosis.actuation import ActuationError, _safe_resolve, cache_root, quarantine_root

    with pytest.raises(ActuationError):
        _safe_resolve(cache_root(), cache_root().parent / "outside.txt")
    with pytest.raises(ActuationError):
        _safe_resolve(quarantine_root(), cache_root())  # 跨根目录


def test_evaluate_allows_executable_action(client):
    """已标记 executable 的动作在完整前置条件下应通过策略评估。"""
    from server.app.diagnosis.action_registry import (
        ActionEvaluationRequest,
        evaluate_action,
    )
    from server.app.diagnosis.authorization import AuthorizationDecision

    result = evaluate_action("mini-drop.cleanup-expired-cache", ActionEvaluationRequest(
        tenant_id="tenant-a",
        environment="production",
        target_count=1,
        healthy_replicas_after_action=1,
        rollback_ready=True,
        dry_run_passed=True,
    ))
    assert result.executable is True
    assert result.decision in (AuthorizationDecision.USER_APPROVAL, AuthorizationDecision.CHANGE_APPROVAL)

    # 缺少 dry-run / rollback 前置条件时不允许执行
    result2 = evaluate_action("mini-drop.cleanup-expired-cache", ActionEvaluationRequest(
        tenant_id="tenant-a",
        environment="production",
        dry_run_passed=False,
        rollback_ready=False,
    ))
    assert result2.executable is False
    assert "DRY_RUN_REQUIRED" in result2.reason_codes
    assert "ROLLBACK_NOT_READY" in result2.reason_codes


def test_audit_records_execution(client):
    cache = Path(os.environ["MINI_DROP_ARTIFACT_ROOT"])
    _seed_expired_cache(cache, "task_20260701_000000_expired1")
    dry = client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/dry-run",
        json={**_tenant(), "parameters": {"retention_days": 7}},
    ).json()["data"]
    client.post(
        "/api/v1/actions/mini-drop.cleanup-expired-cache/execute",
        json={**_tenant(), "dry_run_attempt_id": dry["attempt_id"], "dry_run_passed": True, "rollback_ready": True},
    )
    events = [e.event_type for e in repo.audit_logs]
    assert "ACTION_DRY_RUN" in events
    assert "ACTION_EXECUTED" in events
