"""Long-lived diagnostic target session and signal-trigger tests."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app, repo
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import Actor, TaskStatus


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
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


def _target_payload(service: str = "checkout") -> dict:
    return {
        "service_id": service,
        "environment": "production",
        "display_name": f"{service} production",
        "target_scope": {
            "cluster_id": "prod-a",
            "service_id": service,
            "instances": [{"agent_id": "agent-1", "pid": 4321}],
        },
        "baseline": {"latency_p95_ms": 300},
        "signal_policy": {
            "auto_case_severities": ["high", "critical"],
            "cooldown_seconds": 900,
        },
    }


def _create_target(client: TestClient, service: str = "checkout") -> dict:
    response = client.post("/api/v1/target-sessions", json=_target_payload(service))
    assert response.status_code == 200
    return response.json()["data"]


def _signal_payload(
    *, severity: str = "high", dedupe_key: str = "alert-1",
) -> dict:
    return {
        "signal_type": "latency_regression",
        "severity": severity,
        "observed_at": datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc).isoformat(),
        "payload": {"summary": "checkout p95 latency exceeded 900 ms"},
        "dedupe_key": dedupe_key,
    }


def _seed_profile_task(*, agent_id: str = "agent-1", pid: int = 4321) -> tuple[str, datetime]:
    repo.register_agent(
        agent_id, f"{agent_id}-host", "10.0.0.10", capabilities=["continuous_perf"],
    )
    task = repo.create_task(CreateTaskRequest(
        name="continuous checkout profile",
        agent_id=agent_id,
        target_pid=pid,
        collector_type="continuous_perf",
        sample_rate=11,
        duration_sec=60,
    ))
    repo.transition_task(task.id, TaskStatus.RUNNING, "accepted", Actor.SERVER)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collected", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analyzing", Actor.ANALYZER)
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    ended = started + timedelta(seconds=10)
    repo.add_artifacts(task.id, [{
        "artifact_type": "continuous_window",
        "object_key": f"tasks/{task.id}/window_000/perf.data",
        "filename": "window_000/perf.data",
        "content_type": "application/octet-stream",
        "size_bytes": 1024,
        "metadata": {
            "window_index": 0,
            "start_ts": started.timestamp(),
            "end_ts": ended.timestamp(),
        },
    }, {
        "artifact_type": "continuous_top_json",
        "object_key": f"tasks/{task.id}/window_000/top.json",
        "filename": "window_000/top.json",
        "content_type": "application/json",
        "size_bytes": 256,
        "metadata": {"window_index": 0},
    }])
    repo.transition_task(task.id, TaskStatus.DONE, "analysis complete", Actor.ANALYZER)
    return task.id, ended


def test_manual_case_inherits_target_environment_and_scope(client: TestClient):
    target = _create_target(client)
    created = client.post("/api/v1/cases", json={
        "title": "checkout latency regression",
        "problem_description": "checkout latency exceeded the target baseline",
        "recovery_goal": "restore latency below the target baseline",
        "environment": "client-supplied-wrong-value",
        "target_scope": {"service_id": "wrong-service"},
        "target_session_id": target["target_session_id"],
    })

    assert created.status_code == 200
    case = created.json()["data"]
    assert case["target_session_id"] == target["target_session_id"]
    assert case["environment"] == "production"
    assert case["target_scope"] == target["target_scope"]


def test_target_sessions_are_tenant_scoped(client: TestClient, monkeypatch):
    target = _create_target(client)
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-b")

    assert client.get(
        f"/api/v1/target-sessions/{target['target_session_id']}",
    ).status_code == 404
    assert client.get("/api/v1/target-sessions").json()["data"]["items"] == []
    assert client.post(
        f"/api/v1/target-sessions/{target['target_session_id']}/signals",
        json=_signal_payload(),
    ).status_code == 404


def test_high_signal_opens_one_case_and_dedupe_is_idempotent(client: TestClient):
    target = _create_target(client)
    url = f"/api/v1/target-sessions/{target['target_session_id']}/signals"

    first = client.post(url, json=_signal_payload())
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["created"] is True
    assert first_data["signal"]["status"] == "TRIGGERED"
    case = first_data["triggered_case"]
    assert case["target_session_id"] == target["target_session_id"]
    assert case["environment"] == "production"

    duplicate = client.post(url, json=_signal_payload())
    assert duplicate.status_code == 200
    duplicate_data = duplicate.json()["data"]
    assert duplicate_data["created"] is False
    assert duplicate_data["signal"]["signal_id"] == first_data["signal"]["signal_id"]
    assert len(client.get(url).json()["data"]["items"]) == 1
    assert client.get("/api/v1/cases").json()["data"]["total"] == 1


def test_distinct_signal_within_cooldown_links_existing_case(client: TestClient):
    target = _create_target(client)
    url = f"/api/v1/target-sessions/{target['target_session_id']}/signals"
    first = client.post(url, json=_signal_payload(dedupe_key="alert-1")).json()["data"]
    second = client.post(url, json=_signal_payload(dedupe_key="alert-2")).json()["data"]

    assert second["signal"]["status"] == "SUPPRESSED_COOLDOWN"
    assert second["signal"]["triggered_case_id"] == first["triggered_case"]["case_id"]
    assert second["triggered_case"]["case_id"] == first["triggered_case"]["case_id"]
    assert client.get("/api/v1/cases").json()["data"]["total"] == 1


def test_low_or_paused_target_signal_does_not_open_case(client: TestClient):
    low_target = _create_target(client, "payment")
    low_url = f"/api/v1/target-sessions/{low_target['target_session_id']}/signals"
    low = client.post(
        low_url, json=_signal_payload(severity="low", dedupe_key="low-1"),
    ).json()["data"]
    assert low["triggered_case"] is None
    assert low["signal"]["status"] == "RECORDED"

    paused_target = _create_target(client, "cart")
    paused = client.post(
        f"/api/v1/target-sessions/{paused_target['target_session_id']}/transition",
        json={"action": "pause", "reason": "planned maintenance", "expected_row_version": 0},
    )
    assert paused.status_code == 200
    paused_url = f"/api/v1/target-sessions/{paused_target['target_session_id']}/signals"
    paused_signal = client.post(
        paused_url, json=_signal_payload(dedupe_key="paused-1"),
    ).json()["data"]
    assert paused_signal["triggered_case"] is None
    assert paused_signal["signal"]["status"] == "RECORDED"
    assert client.get("/api/v1/cases").json()["data"]["total"] == 0


def test_target_transition_uses_optimistic_version_and_archive_is_terminal(client: TestClient):
    target = _create_target(client)
    url = f"/api/v1/target-sessions/{target['target_session_id']}/transition"

    stale = client.post(
        url, json={"action": "pause", "reason": "planned maintenance", "expected_row_version": 2},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "TARGET_SESSION_VERSION_CONFLICT"

    archived = client.post(
        url, json={"action": "archive", "reason": "service retired", "expected_row_version": 0},
    )
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "ARCHIVED"
    resume = client.post(
        url, json={"action": "resume", "reason": "try to restore", "expected_row_version": 1},
    )
    assert resume.status_code == 409
    assert resume.json()["detail"].startswith("INVALID_TARGET_SESSION_TRANSITION")


def test_profile_task_indexes_queryable_windows_and_signal_links_them(client: TestClient):
    target = _create_target(client)
    task_id, observed_at = _seed_profile_task()
    index_url = (
        f"/api/v1/target-sessions/{target['target_session_id']}"
        "/profile-windows/index-task"
    )
    indexed = client.post(index_url, json={"task_id": task_id})
    assert indexed.status_code == 200, indexed.text
    window = indexed.json()["data"]["items"][0]
    assert window["task_id"] == task_id
    assert {item["artifact_type"] for item in window["artifact_refs"]} == {
        "continuous_window", "continuous_top_json",
    }

    duplicate = client.post(index_url, json={"task_id": task_id})
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["items"][0]["profile_window_id"] == (
        window["profile_window_id"]
    )
    queried = client.get(
        f"/api/v1/target-sessions/{target['target_session_id']}/profile-windows",
        params={
            "start": (observed_at - timedelta(minutes=2)).isoformat(),
            "end": (observed_at + timedelta(minutes=2)).isoformat(),
        },
    )
    assert queried.status_code == 200
    assert [item["profile_window_id"] for item in queried.json()["data"]["items"]] == [
        window["profile_window_id"],
    ]

    signal_url = f"/api/v1/target-sessions/{target['target_session_id']}/signals"
    signal = client.post(signal_url, json={
        **_signal_payload(dedupe_key="profile-alert"),
        "observed_at": observed_at.isoformat(),
    })
    assert signal.status_code == 200, signal.text
    data = signal.json()["data"]
    assert data["signal"]["profile_window_ids"] == [window["profile_window_id"]]
    assert data["triggered_case"]["initial_task_ids"] == [task_id]
    events = client.get(
        f"/api/v1/cases/{data['triggered_case']['case_id']}/events",
    ).json()["data"]["items"]
    assert events[0]["payload"]["profile_window_ids"] == [window["profile_window_id"]]


def test_profile_task_must_match_explicit_target_instance(client: TestClient):
    target = _create_target(client)
    task_id, _ = _seed_profile_task(agent_id="other-agent", pid=9999)
    response = client.post(
        f"/api/v1/target-sessions/{target['target_session_id']}/profile-windows/index-task",
        json={"task_id": task_id},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "PROFILE_TASK_SCOPE_MISMATCH"
