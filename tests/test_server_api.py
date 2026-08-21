"""HTTP API 测试。

通过 FastAPI TestClient 验证各 REST 端点，
测试使用独立 repo 实例避免与 gRPC 测试共享状态。

注意：TestClient 会触发 FastAPI startup 事件尝试启动 gRPC server。
50051 端口被占用时 gRPC 启动失败不影响 HTTP 端点功能，
测试在 setUp 中直接清理 repo 状态。
"""

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from server.app import storage as store
from server.app.database import init_db, reset_engine
from server.app.main import (
    _ensure_minio_bucket_with_retry,
    _run_offline_sweep_pass,
    app,
    repo,
)
from server.app.models import Base
from server.app.prometheus_metrics import REGISTRY
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    """每个测试使用独立 SQLite 内存库，确保用例间无状态交叉。"""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("MINI_DROP_API_KEY", raising=False)
    monkeypatch.delenv("MINI_DROP_WEB_AUTO_SESSION_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_REQUIRE_STORAGE", "1")
    monkeypatch.setattr(store, "ensure_bucket", lambda _bucket: None)
    monkeypatch.setattr(store, "bucket_available", lambda _bucket: True)
    REGISTRY.clear()
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.agent_metrics.clear()
    repo.register_agent("agent_local_demo", "demo-host", "10.0.0.10")
    repo.register_agent("a1", "agent-one", "10.0.0.11")
    repo.register_agent("a2", "agent-two", "10.0.0.12")
    repo.register_agent("a3", "agent-three", "10.0.0.13")
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="client")
def client_fixture():
    """提供预配置的 TestClient 实例。"""
    return TestClient(app)


class TestHealthz:
    """健康与用户信息端点。"""

    def test_healthz_returns_service_info(self, client: TestClient):
        resp = client.get("/api/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["service"] == "mini-drop-server"

    def test_healthz_requires_live_analyzer_when_enabled(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_REQUIRE_ANALYZER", "1")
        missing = client.get("/api/healthz").json()["data"]
        assert missing["healthy"] is False
        assert missing["checks"]["analyzer"]["status"] == "unavailable"

        repo.heartbeat_analyzer("analyzer-test")
        ready = client.get("/api/healthz").json()["data"]
        assert ready["healthy"] is True
        assert ready["checks"]["analyzer"]["workers_online"] == 1

    def test_core_health_ignores_analyzer_bootstrap_dependency(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_REQUIRE_ANALYZER", "1")
        body = client.get("/api/healthz?core_only=true").json()["data"]
        assert body["healthy"] is True

    def test_liveness_and_readiness_are_separate(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_REQUIRE_ANALYZER", "1")
        live = client.get("/api/livez")
        ready = client.get("/api/readyz")
        assert live.status_code == 200
        assert live.json()["data"]["alive"] is True
        assert ready.status_code == 503
        assert ready.json()["data"]["healthy"] is False

    def test_core_readiness_ignores_only_analyzer(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_REQUIRE_ANALYZER", "1")
        ready = client.get("/api/readyz?core_only=true")
        assert ready.status_code == 200
        assert ready.json()["data"]["healthy"] is True

    def test_health_storage_probe_is_read_only_and_redacts_internal_errors(
        self, client: TestClient, monkeypatch,
    ):
        probe = mock.Mock(side_effect=RuntimeError("secret storage hostname"))
        monkeypatch.setattr(store, "bucket_available", probe)

        response = client.get("/api/readyz")

        assert response.status_code == 503
        storage = response.json()["data"]["checks"]["storage"]
        assert storage == {
            "status": "unavailable",
            "error_code": "dependency_unavailable",
        }
        assert "secret storage hostname" not in response.text
        probe.assert_called_once_with("mini-drop")

    def test_local_storage_mode_does_not_require_minio(
        self, client: TestClient, monkeypatch,
    ):
        monkeypatch.setenv("MINI_DROP_REQUIRE_STORAGE", "0")
        probe = mock.Mock(side_effect=AssertionError("MinIO must not be probed"))
        monkeypatch.setattr(store, "bucket_available", probe)

        response = client.get("/api/readyz?core_only=true")

        assert response.status_code == 200
        assert response.json()["data"]["checks"]["storage"] == {"status": "disabled"}
        probe.assert_not_called()

    def test_health_redacts_database_and_analyzer_errors(
        self, client: TestClient, monkeypatch,
    ):
        monkeypatch.setenv("MINI_DROP_REQUIRE_ANALYZER", "1")
        monkeypatch.setattr(
            "server.app.app_factory.new_session",
            mock.Mock(side_effect=RuntimeError("postgresql://user:password@secret-db")),
        )
        monkeypatch.setattr(
            repo,
            "analysis_health",
            mock.Mock(side_effect=RuntimeError("secret analyzer topology")),
        )

        response = client.get("/api/readyz")

        assert response.status_code == 503
        checks = response.json()["data"]["checks"]
        assert checks["database"] == {
            "status": "unavailable",
            "error_code": "dependency_unavailable",
        }
        assert checks["analyzer"] == {
            "status": "unavailable",
            "error_code": "dependency_unavailable",
        }
        assert "password" not in response.text
        assert "secret analyzer topology" not in response.text

    def test_me_returns_server_derived_identity(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_PRINCIPAL_ID", "operator-a")
        monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
        monkeypatch.setenv("MINI_DROP_API_ROLES", "operator,authorization_admin")
        resp = client.get("/api/me")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["user_id"] == "operator-a"
        assert data["tenant_id"] == "tenant-a"
        assert data["roles"] == ["authorization_admin", "operator"]

    def test_ai_config_never_returns_key(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_AI_API_KEY", "secret-must-not-leak")
        monkeypatch.setenv("MINI_DROP_AI_MODEL", "deepseek-v4-flash")
        resp = client.get("/api/ai-config")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["has_api_key"] is True
        assert data["model"] == "deepseek-v4-flash"
        assert "secret-must-not-leak" not in resp.text

    def test_ai_validation_endpoint(self, client: TestClient):
        result = {
            "run_id": "ai_validation_test",
            "status": "PASSED",
            "passed_count": 8,
            "failed_count": 0,
            "total_count": 8,
            "checks": [],
        }
        with mock.patch("server.app.app_factory.run_ai_validation_suite", return_value=result):
            resp = client.post("/api/ai-validation/runs")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "PASSED"


class TestTaskKinds:
    def test_task_kinds_expose_form_metadata(self, client: TestClient):
        resp = client.get("/api/task-kinds")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["schema_version"] == "1.0"
        assert len(data["items"]) == 13
        network_discovery = next(
            item for item in data["items"] if item["key"] == "network_discovery"
        )
        assert network_discovery["display_name"]
        assert network_discovery["result_label"]
        assert any(item["key"] == "connection_probe" for item in data["items"])
        perf = next(item for item in data["items"] if item["key"] == "perf_cpu")
        assert perf["defaults"]["sample_rate"] == 99
        assert perf["parameter_schema"]["duration_sec"]["maximum"] == 60
        assert perf["presentation"]["flamegraph"] is True

    def test_task_kinds_filter_by_agent_capability(self, client: TestClient):
        repo.register_agent(
            "limited-agent",
            "limited-host",
            "10.0.0.20",
            capabilities=["sys_metrics", "memory_smaps"],
        )

        resp = client.get("/api/task-kinds", params={"agent_id": "limited-agent"})

        assert resp.status_code == 200
        assert {item["key"] for item in resp.json()["data"]["items"]} == {
            "sys_metrics",
            "memory_smaps",
        }

    def test_task_kinds_reject_unknown_agent(self, client: TestClient):
        resp = client.get("/api/task-kinds", params={"agent_id": "missing"})

        assert resp.status_code == 404


class TestStartupMinio:
    def test_bucket_init_retries_transient_failure(self, monkeypatch):
        calls = {"count": 0}

        def flaky_ensure(bucket: str) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("not ready")
            assert bucket == "mini-drop"

        monkeypatch.setenv("MINI_DROP_MINIO_READY_RETRIES", "2")
        monkeypatch.setenv("MINI_DROP_MINIO_READY_DELAY_SEC", "0")
        monkeypatch.setattr(store, "ensure_bucket", flaky_ensure)

        _ensure_minio_bucket_with_retry("mini-drop")

        assert calls["count"] == 2

    def test_bucket_init_raises_after_retry_exhausted(self, monkeypatch):
        monkeypatch.setenv("MINI_DROP_MINIO_READY_RETRIES", "2")
        monkeypatch.setenv("MINI_DROP_MINIO_READY_DELAY_SEC", "0")
        monkeypatch.setattr(store, "ensure_bucket", lambda bucket: (_ for _ in ()).throw(RuntimeError("down")))

        with pytest.raises(RuntimeError, match="down"):
            _ensure_minio_bucket_with_retry("mini-drop")


class TestMaintenanceLoop:
    def test_one_failed_step_does_not_starve_the_remaining_steps(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            repo,
            "mark_offline_agents",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db glitch")),
        )
        monkeypatch.setattr(
            repo,
            "recover_stale_tasks",
            lambda **_kwargs: calls.append("recover"),
        )
        monkeypatch.setattr(
            repo,
            "persist_agent_metric_snapshots",
            lambda: calls.append("metrics"),
        )
        monkeypatch.setattr(
            "server.app.main.diagnosis_orchestrator.advance_active",
            lambda: calls.append("diagnosis"),
        )

        _run_offline_sweep_pass(timeout_sec=30, stale_task_timeout_sec=900)

        assert calls == ["recover", "metrics", "diagnosis"]
        metrics = REGISTRY.generate()
        assert (
            'mini_drop_maintenance_runs_total{outcome="failure",step="agent_offline_detection"}'
            in metrics
        )
        assert (
            'mini_drop_maintenance_runs_total{outcome="success",step="stale_task_recovery"}'
            in metrics
        )


class TestApiAuth:
    def test_auth_disabled_by_default(self, client: TestClient):
        resp = client.get("/api/tasks")
        assert resp.status_code == 200

    def test_invalid_request_id_is_replaced(self, client: TestClient):
        response = client.get("/api/tasks", headers={"X-Request-ID": "bad id\nvalue"})

        assert response.status_code == 200
        generated = response.headers["x-request-id"]
        assert generated != "bad id\nvalue"
        assert len(generated) == 12

    def test_auth_enabled_rejects_missing_token(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/tasks")
        assert resp.status_code == 401

    def test_auth_enabled_accepts_bearer_token(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/tasks", headers={"Authorization": "Bearer secret-token"})
        assert resp.status_code == 200

    def test_auth_enabled_accepts_x_api_key(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/tasks", headers={"X-API-Key": "secret-token"})
        assert resp.status_code == 200

    def test_cookie_endpoint_validates_key_server_side(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")

        rejected = client.post("/api/auth/set-cookie", json={"api_key": "wrong"})
        accepted = client.post(
            "/api/auth/set-cookie",
            json={"api_key": "secret-token"},
            headers={"X-Forwarded-Proto": "https"},
        )

        assert rejected.status_code == 401
        assert accepted.status_code == 200
        cookie = accepted.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie

    def test_bootstrap_is_disabled_unless_explicitly_enabled(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")

        response = client.post("/api/auth/bootstrap")

        assert response.status_code == 404

    def test_bootstrap_sets_cookie_without_returning_key(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        monkeypatch.setenv("MINI_DROP_WEB_AUTO_SESSION_ENABLED", "1")

        response = client.post(
            "/api/auth/bootstrap",
            headers={"X-Forwarded-Proto": "https", "Origin": "https://testserver"},
        )

        assert response.status_code == 200
        assert response.json()["data"] == {"authenticated": True}
        assert "secret-token" not in response.text
        cookie = response.headers["set-cookie"]
        assert "secret-token" not in cookie
        assert "mini_drop_web_session=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie
        assert client.get("https://testserver/api/tasks").status_code == 200

    def test_bootstrap_rejects_cross_site_origin(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        monkeypatch.setenv("MINI_DROP_WEB_AUTO_SESSION_ENABLED", "1")

        response = client.post("/api/auth/bootstrap", headers={"Origin": "https://other.example"})

        assert response.status_code == 403

    def test_healthz_stays_public_when_auth_enabled(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/healthz")
        assert resp.status_code == 200

    def test_metrics_stays_public_when_auth_enabled(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
        monkeypatch.setenv("MINI_DROP_API_KEY", "secret-token")
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        assert "mini_drop" in resp.text or resp.text.strip() == ""


class TestAgents:
    def test_list_agents_includes_latest_metrics(self, client: TestClient):
        repo.record_agent_metrics("a1", {
            "self": {"cpu_percent": 1.5, "rss_mb": 32.0, "read_kb_s": 0.1, "write_kb_s": 0.2},
            "children": {"children_count": 2},
        })
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()["data"]
        agents = data if isinstance(data, list) else data.get("items", [])
        agent = next(item for item in agents if item["id"] == "a1")
        assert agent["latest_metrics"]["self"]["cpu_percent"] == 1.5


class TestCreateTask:
    """任务创建端点。"""

    def test_create_task_records_pending_status(self, client: TestClient):
        resp = client.post("/api/tasks", headers={"X-Request-ID": "trace-create-1"}, json={
            "name": "demo cpu profile",
            "agent_id": "agent_local_demo",
            "target_pid": 1234,
            "collector_type": "perf_cpu",
            "sample_rate": 99,
            "duration_sec": 10,
        })
        assert resp.status_code == 200
        body = resp.json()
        task_id = body["data"]["task_id"]
        assert body["data"]["status"] == "PENDING"
        assert resp.headers["x-request-id"] == "trace-create-1"

        # 通过详情端点确认
        detail = client.get(f"/api/tasks/{task_id}")
        assert detail.json()["data"]["status"] == "PENDING"
        assert detail.json()["data"]["request_id"] == "trace-create-1"

    def test_create_task_writes_status_event(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "test", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]
        assert len(events) >= 1
        assert events[0]["to_status"] == "PENDING"
        assert events[0]["reason"] == "Web 请求创建任务"

    def test_create_task_writes_audit_log(self, client: TestClient):
        client.post("/api/tasks", json={
            "name": "test", "agent_id": "a2",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        log_data = client.get("/api/audit-logs").json()["data"]
        logs = log_data if isinstance(log_data, list) else log_data.get("items", [])
        assert any(log["event_type"] == "TASK_CREATED" for log in logs)

    def test_create_task_replaces_unreadable_name(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "?? Go pprof ??",
            "agent_id": "a1",
            "target_pid": 7344,
            "collector_type": "go_pprof",
        })

        assert resp.status_code == 200
        task_id = resp.json()["data"]["task_id"]
        detail = client.get(f"/api/tasks/{task_id}").json()["data"]
        assert detail["name"] == "Go CPU 剖析 · a1 · PID 7344"

    def test_create_task_preserves_readable_name(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "订单服务 CPU 基线采集",
            "agent_id": "a1",
            "target_pid": 42,
            "collector_type": "perf_cpu",
        })

        assert resp.status_code == 200
        task_id = resp.json()["data"]["task_id"]
        detail = client.get(f"/api/tasks/{task_id}").json()["data"]
        assert detail["name"] == "订单服务 CPU 基线采集"

    def test_rejects_zero_duration(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "duration_sec": 0,
        })
        assert resp.status_code == 400

    def test_rejects_negative_sample_rate(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "sample_rate": -1,
        })
        assert resp.status_code == 400

    def test_rejects_too_long_duration(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "duration_sec": 121,
        })
        assert resp.status_code == 400

    def test_rejects_too_high_sample_rate(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
            "sample_rate": 1000,
        })
        assert resp.status_code == 400

    def test_rejects_unknown_agent(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "bad-agent", "agent_id": "missing_agent",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        assert resp.status_code == 404

    def test_rejects_collector_not_supported_by_agent(self, client: TestClient):
        repo.register_agent(
            "limited-agent",
            "limited-host",
            "10.0.0.20",
            capabilities=["sys_metrics"],
        )

        resp = client.post("/api/tasks", json={
            "name": "unsupported", "agent_id": "limited-agent",
            "target_pid": 1, "collector_type": "perf_cpu",
        })

        assert resp.status_code == 409
        assert "不支持采集器" in resp.json()["detail"]

    def test_idempotency_key_replays_original_task(self, client: TestClient):
        payload = {
            "name": "idempotent", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        }
        headers = {"Idempotency-Key": "web-operation-001"}

        first = client.post("/api/tasks", json=payload, headers=headers)
        replay = client.post("/api/tasks", json=payload, headers=headers)

        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json()["data"]["task_id"] == replay.json()["data"]["task_id"]
        assert client.get("/api/tasks").json()["data"]["total"] == 1

    def test_idempotency_key_rejects_different_payload(self, client: TestClient):
        headers = {"Idempotency-Key": "web-operation-conflict"}
        first = {
            "name": "first", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        }
        second = {**first, "target_pid": 2}
        assert client.post("/api/tasks", json=first, headers=headers).status_code == 200

        conflict = client.post("/api/tasks", json=second, headers=headers)

        assert conflict.status_code == 409


class TestTaskControl:
    def _create(self, client: TestClient) -> str:
        response = client.post("/api/tasks", json={
            "name": "control", "agent_id": "a1",
            "target_pid": 12, "collector_type": "perf_cpu",
        })
        return response.json()["data"]["task_id"]

    def test_cancel_pending_task_is_idempotent(self, client: TestClient):
        task_id = self._create(client)

        first = client.post(
            f"/api/tasks/{task_id}/cancel",
            json={"reason": "operator cancelled"},
        )
        replay = client.post(
            f"/api/tasks/{task_id}/cancel",
            json={"reason": "operator cancelled"},
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json()["data"]["status"] == "CANCELLED"
        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]
        assert [event["to_status"] for event in events].count("CANCELLED") == 1

    def test_retry_creates_new_pending_task(self, client: TestClient):
        task_id = self._create(client)
        client.post(f"/api/tasks/{task_id}/cancel", json={"reason": "retry test"})

        retried = client.post(
            f"/api/tasks/{task_id}/retry",
            json={},
            headers={"Idempotency-Key": "retry-operation-001"},
        )
        replay = client.post(
            f"/api/tasks/{task_id}/retry",
            json={},
            headers={"Idempotency-Key": "retry-operation-001"},
        )

        assert retried.status_code == 200
        assert retried.json()["data"]["task_id"] != task_id
        assert retried.json()["data"]["status"] == "PENDING"
        assert replay.json()["data"]["task_id"] == retried.json()["data"]["task_id"]
        retried_id = retried.json()["data"]["task_id"]
        audit_data = client.get("/api/audit-logs").json()["data"]
        audit_logs = (
            audit_data if isinstance(audit_data, list) else audit_data.get("items", [])
        )
        retry_logs = [
            log
            for log in audit_logs
            if log["event_type"] == "TASK_RETRIED"
            and log["task_id"] == retried_id
        ]
        assert len(retry_logs) == 1
        assert retry_logs[0]["metadata"]["retry_of"] == task_id

    def test_retry_rejects_active_task(self, client: TestClient):
        task_id = self._create(client)

        response = client.post(f"/api/tasks/{task_id}/retry", json={})

        assert response.status_code == 409


class TestTaskListAndDetail:
    """任务列表与详情端点。"""

    def test_list_returns_empty_initially(self, client: TestClient):
        resp = client.get("/api/tasks")
        assert resp.json()["data"]["total"] == 0

    def test_list_returns_created_tasks(self, client: TestClient):
        client.post("/api/tasks", json={
            "name": "task1", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        client.post("/api/tasks", json={
            "name": "task2", "agent_id": "a1",
            "target_pid": 2, "collector_type": "ebpf_io",
        })
        resp = client.get("/api/tasks")
        assert resp.json()["data"]["total"] == 2

    def test_detail_returns_full_fields(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "detail-test", "agent_id": "a3",
            "target_pid": 9999, "collector_type": "pyspy",
            "sample_rate": 11, "duration_sec": 5,
        })
        task_id = resp.json()["data"]["task_id"]
        detail = client.get(f"/api/tasks/{task_id}").json()["data"]
        assert detail["name"] == "detail-test"
        assert detail["target_pid"] == 9999
        assert detail["collector_type"] == "pyspy"
        assert detail["sample_rate"] == 11
        assert detail["duration_sec"] == 5
        assert detail["collection_status"] == "PENDING"
        assert detail["analysis_status"] == "WAITING"

    def test_attempt_and_analysis_job_endpoints(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "attempt-test", "agent_id": "a1",
            "target_pid": 777, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.heartbeat("a1", "10.0.0.11")

        attempts = client.get(f"/api/tasks/{task_id}/attempts")
        assert attempts.status_code == 200
        assert len(attempts.json()["data"]) == 1
        assert attempts.json()["data"][0]["status"] == "RUNNING"

        jobs = client.get(f"/api/tasks/{task_id}/analysis-jobs")
        assert jobs.status_code == 200
        assert jobs.json()["data"] == []

    def test_nonexistent_task_returns_404(self, client: TestClient):
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404


class TestTaskEvents:
    """状态迁移事件端点。"""

    def test_events_are_returned_in_order(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "events-test", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]

        # 手动推进两步
        repo.transition_task(task_id, TaskStatus.RUNNING, "heartbeat", Actor.SERVER)
        repo.transition_task(task_id, TaskStatus.UPLOADING, "done collecting", Actor.AGENT)

        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]
        statuses = [e["to_status"] for e in events]
        assert statuses == ["PENDING", "RUNNING", "UPLOADING"]

        metrics = client.get("/api/metrics").text
        assert 'mini_drop_task_transitions_total{from="NONE",to="PENDING"}' in metrics
        assert 'mini_drop_task_transitions_total{from="PENDING",to="RUNNING"}' in metrics
        assert 'mini_drop_task_transitions_total{from="RUNNING",to="UPLOADING"}' in metrics

    def test_events_404_for_nonexistent_task(self, client: TestClient):
        resp = client.get("/api/tasks/does-not-exist/events")
        assert resp.status_code == 404


class TestTaskArtifacts:
    """产物查询端点。"""

    def test_empty_artifacts_for_new_task(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "art-test", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        arts = client.get(f"/api/tasks/{task_id}/artifacts").json()["data"]
        assert arts == []

    def test_artifacts_after_result_report(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "art2", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{"artifact_type": "raw", "bucket": "mini-drop", "object_key": "tasks/x/perf.data"}])
        arts = client.get(f"/api/tasks/{task_id}/artifacts").json()["data"]
        assert len(arts) == 1
        assert arts[0]["artifact_type"] == "raw"

    def test_artifact_list_reports_live_availability(self, client: TestClient, monkeypatch):
        resp = client.post("/api/tasks", json={
            "name": "art-availability", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "raw",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/perf.data",
            "filename": "perf.data",
            "size_bytes": 12,
        }])
        monkeypatch.setattr(store, "object_size", lambda bucket, key: 12)

        artifact = client.get(f"/api/tasks/{task_id}/artifacts").json()["data"][0]

        assert artifact["artifact_id"].startswith("art_")
        assert artifact["task_id"] == task_id
        assert artifact["availability"] == "available"
        assert artifact["actual_size_bytes"] == 12
        assert artifact["integrity_status"] == "not_checked"

    def test_storage_reconciliation_exposes_missing_and_mismatch(
        self,
        client: TestClient,
        monkeypatch,
    ):
        resp = client.post("/api/tasks", json={
            "name": "art-reconcile", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [
            {
                "artifact_type": "raw",
                "bucket": "mini-drop",
                "object_key": f"tasks/{task_id}/missing.data",
                "size_bytes": 10,
            },
            {
                "artifact_type": "top_json",
                "bucket": "mini-drop",
                "object_key": f"tasks/{task_id}/mismatch.json",
                "size_bytes": 10,
            },
        ])
        monkeypatch.setattr(
            store,
            "object_size",
            lambda bucket, key: None if key.endswith("missing.data") else 4,
        )

        data = client.get("/api/storage/reconciliation").json()["data"]

        assert data["summary"]["scanned"] == 2
        assert data["summary"]["missing"] == 1
        assert data["summary"]["integrity_mismatch"] == 1

    def test_artifact_content_reads_local_json(self, client: TestClient, tmp_path, monkeypatch):
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
        top_path = tmp_path / "top.json"
        top_path.write_text('[{"name":"fib_hotspot","samples":10,"percent":80.0}]', encoding="utf-8")
        resp = client.post("/api/tasks", json={
            "name": "art-content", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "filename": "top.json",
            "local_path": str(top_path),
            "content_type": "application/json",
        }])

        content = client.get(f"/api/tasks/{task_id}/artifacts/top_json/content")
        assert content.status_code == 200
        assert content.json()["data"][0]["name"] == "fib_hotspot"

    def test_artifact_content_rejects_path_outside_root(self, client: TestClient, tmp_path, monkeypatch):
        root = tmp_path / "artifacts"
        outside = tmp_path / "outside.json"
        root.mkdir()
        outside.write_text('{"secret": true}', encoding="utf-8")
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(root))
        resp = client.post("/api/tasks", json={
            "name": "art-content-forbidden", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "filename": "top.json",
            "local_path": str(outside),
            "content_type": "application/json",
        }])

        content = client.get(f"/api/tasks/{task_id}/artifacts/top_json/content")
        assert content.status_code == 403

    def test_artifact_content_reads_minio_object(self, client: TestClient, monkeypatch):
        resp = client.post("/api/tasks", json={
            "name": "art-object", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/top.json",
            "content_type": "application/json",
        }])
        monkeypatch.setattr(store, "read_object_bytes", lambda bucket, key: b'[{"name":"fib","samples":1}]')

        content = client.get(f"/api/tasks/{task_id}/artifacts/top_json/content")

        assert content.status_code == 200
        assert content.json()["data"][0]["name"] == "fib"

    def test_artifact_content_falls_back_to_minio_when_local_path_missing(
        self,
        client: TestClient,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
        resp = client.post("/api/tasks", json={
            "name": "art-object-fallback", "agent_id": "a1",
            "target_pid": 1, "collector_type": "pyspy",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "flamegraph_svg",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/pyspy.svg",
            "local_path": str(tmp_path / task_id / "pyspy.svg"),
            "content_type": "image/svg+xml",
        }])
        monkeypatch.setattr(store, "read_object_bytes", lambda bucket, key: b"<svg></svg>")

        content = client.get(f"/api/tasks/{task_id}/artifacts/flamegraph_svg/content")

        assert content.status_code == 200
        assert content.json()["data"]["text"] == "<svg></svg>"

    def test_artifact_download_streams_minio_through_server(self, client: TestClient, monkeypatch):
        resp = client.post("/api/tasks", json={
            "name": "art-download", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "raw",
            "filename": "perf data.bin",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/perf.data",
            "content_type": "application/octet-stream",
        }])
        monkeypatch.setattr(store, "stream_object", lambda bucket, key: iter([b"part-1", b"part-2"]))
        monkeypatch.setattr(store, "object_size", lambda bucket, key: len(b"part-1part-2"))

        download = client.get(f"/api/tasks/{task_id}/artifacts/raw/download")

        assert download.status_code == 200
        assert download.content == b"part-1part-2"
        assert "perf%20data.bin" in download.headers["content-disposition"]

    def test_artifact_download_rejects_missing_object(self, client: TestClient, monkeypatch):
        resp = client.post("/api/tasks", json={
            "name": "missing-object", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "top_json",
            "filename": "top.json",
            "bucket": "mini-drop",
            "object_key": f"tasks/{task_id}/top.json",
            "content_type": "application/json",
        }])
        monkeypatch.setattr(store, "object_size", lambda bucket, key: None)

        download = client.get(f"/api/tasks/{task_id}/artifacts/top_json/download")

        assert download.status_code == 404
        assert "结构化证据 JSON" in download.json()["detail"]

    def test_artifact_download_reads_local_file(self, client: TestClient, tmp_path, monkeypatch):
        monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
        artifact_path = tmp_path / "report.txt"
        artifact_path.write_bytes(b"local-report")
        resp = client.post("/api/tasks", json={
            "name": "local-download", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        repo.add_artifacts(task_id, [{
            "artifact_type": "raw",
            "filename": "report.txt",
            "local_path": str(artifact_path),
            "content_type": "text/plain",
        }])

        download = client.get(f"/api/tasks/{task_id}/artifacts/raw/download")

        assert download.status_code == 200
        assert download.content == b"local-report"


class TestStoragePresign:
    """对象存储预签名 URL 端点。"""

    def test_presign_returns_url(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            store,
            "presigned_get_url",
            lambda bucket, key, expires: "http://minio:9000/mini-drop/artifact.svg",
        )
        resp = client.get("/api/storage/presign", params={
            "bucket": "mini-drop",
            "key": "tasks/demo/flamegraph.svg",
            "expires": 600,
        })
        assert resp.status_code == 200
        assert resp.json()["data"]["url"].startswith("http://minio:9000")

    def test_presign_rejects_empty_key(self, client: TestClient):
        resp = client.get("/api/storage/presign", params={"bucket": "mini-drop"})
        assert resp.status_code == 400

    def test_presign_rejects_unallowed_bucket(self, client: TestClient):
        resp = client.get("/api/storage/presign", params={
            "bucket": "other-bucket",
            "key": "tasks/demo/flamegraph.svg",
        })
        assert resp.status_code == 403

    def test_presign_rejects_path_traversal_key(self, client: TestClient):
        resp = client.get("/api/storage/presign", params={
            "bucket": "mini-drop",
            "key": "tasks/../secret.txt",
        })
        assert resp.status_code == 400

    def test_presign_rejects_key_outside_task_artifacts(self, client: TestClient):
        resp = client.get("/api/storage/presign", params={
            "bucket": "mini-drop",
            "key": "public/demo.svg",
        })
        assert resp.status_code == 403

    def test_presign_rejects_invalid_expires(self, client: TestClient):
        resp = client.get("/api/storage/presign", params={
            "bucket": "mini-drop",
            "key": "tasks/demo/flamegraph.svg",
            "expires": 0,
        })
        assert resp.status_code == 400


class TestDiagnose:
    """诊断触发端点（E9：旧一次性诊断已退役为 410 + Case 路径）。"""

    def test_diagnose_retired_with_410_and_pointer(self, client: TestClient):
        resp = client.post("/api/tasks", json={
            "name": "diag", "agent_id": "a1",
            "target_pid": 1, "collector_type": "perf_cpu",
        })
        task_id = resp.json()["data"]["task_id"]
        resp = client.post(f"/api/tasks/{task_id}/diagnose")
        assert resp.status_code == 410
        assert "POST /api/v1/cases" in resp.json()["detail"]
        assert "initial_tasks" in resp.json()["detail"]

        # E9-1：旧一次性诊断不再写入 legacy 单任务诊断表（report_json/ranked_causes）
        aggregate = client.get("/api/diagnoses", params={"limit": 100}).json()["data"]
        assert aggregate["total"] == 0
        history = client.get(f"/api/tasks/{task_id}/diagnoses").json()["data"]
        assert history == []

    def test_diagnose_404_for_nonexistent(self, client: TestClient):
        resp = client.post("/api/tasks/nope/diagnose")
        assert resp.status_code == 404

    def test_diagnosis_detail_404_for_nonexistent(self, client: TestClient):
        resp = client.get("/api/diagnoses/diag_missing")
        assert resp.status_code == 404
