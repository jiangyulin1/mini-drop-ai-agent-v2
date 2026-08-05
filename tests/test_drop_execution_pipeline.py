"""Durable Drop execution/analysis pipeline tests."""

from datetime import timedelta
from unittest import mock

import pytest

from analyzer.mini_drop_analyzer.worker import AnalysisWorker
from server.app.database import init_db, new_session, reset_engine
from server.app.models import AnalysisJobModel, Base
from server.app.generated import healthcheck_pb2, hotmethod_pb2
from server.app.grpc_services.healthcheck_service import HealthCheckService
from server.app.grpc_services.hotmethod_service import HotmethodService
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import Actor, TaskStatus, now_utc


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_ANALYZER_UPLOAD", "0")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _started_task(repo: SqlRepository, collector_type: str = "sys_metrics"):
    repo.register_agent("agent-drop", "worker", "10.0.0.2", capabilities=[collector_type])
    task = repo.create_task(CreateTaskRequest(
        name="durable pipeline",
        agent_id="agent-drop",
        target_pid=1234,
        collector_type=collector_type,
        sample_rate=10,
        duration_sec=5,
    ))
    pulled = repo.heartbeat("agent-drop", "10.0.0.2")
    return pulled


def test_dispatch_creates_durable_attempt_and_dual_status():
    repo = SqlRepository()
    task = _started_task(repo)

    attempts = repo.list_task_attempts(task.id)
    assert len(attempts) == 1
    assert attempts[0]["attempt_id"] == task.current_attempt_id
    assert attempts[0]["status"] == "RUNNING"
    refreshed = repo.tasks[task.id]
    assert refreshed.collection_status == "RUNNING"
    assert refreshed.analysis_status == "WAITING"


def test_http_request_id_reaches_grpc_task_description():
    repo = SqlRepository()
    repo.register_agent("agent-drop", "worker", "10.0.0.2", capabilities=["sys_metrics"])
    task = repo.create_task(CreateTaskRequest(
        name="traceable",
        agent_id="agent-drop",
        target_pid=1234,
        collector_type="sys_metrics",
    ), request_id="trace-drop-1")

    response = HealthCheckService(repo).Do(healthcheck_pb2.HealthCheckRequest(
        agent_id="agent-drop",
        ip_addr="10.0.0.2",
    ), None)

    assert response.pending is True
    assert response.task_desc.task_id == task.id
    assert response.task_desc.request_id == "trace-drop-1"


def test_artifact_replay_is_attempt_idempotent():
    repo = SqlRepository()
    task = _started_task(repo)
    artifact = {
        "artifact_type": "sys_metrics",
        "filename": "metrics.json",
        "object_key": f"tasks/{task.id}/metrics.json",
        "sha256": "a" * 64,
    }

    first = repo.add_artifacts(task.id, [artifact], attempt_id=task.current_attempt_id)
    second = repo.add_artifacts(task.id, [artifact], attempt_id=task.current_attempt_id)

    assert first == second
    assert len(repo.artifacts[task.id]) == 1


def test_analysis_worker_completes_preanalyzed_collector_result():
    repo = SqlRepository()
    task = _started_task(repo)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collection complete", Actor.AGENT)
    artifact_ids = repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "filename": "metrics.json",
        "object_key": f"tasks/{task.id}/metrics.json",
    }], attempt_id=task.current_attempt_id)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analysis queued", Actor.SERVER)
    repo.finish_attempt(
        task.id,
        task.current_attempt_id,
        status="COLLECTED",
        result_message="采集到 5 个时间点",
    )
    job = repo.create_analysis_job(task.id, task.current_attempt_id, artifact_ids)

    assert AnalysisWorker(repo, worker_id="worker-a").run_once() is True

    refreshed = repo.tasks[task.id]
    assert refreshed.status == "DONE"
    assert refreshed.collection_status == "COLLECTED"
    assert refreshed.analysis_status == "SUCCEEDED"
    assert "采集到 5 个时间点" in refreshed.status_reason
    assert repo.get_analysis_job(job.id).status == "SUCCEEDED"


def test_expired_analysis_lease_is_reclaimed_once():
    repo = SqlRepository()
    task = _started_task(repo)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collection complete", Actor.AGENT)
    ids = repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "filename": "metrics.json",
        "object_key": f"tasks/{task.id}/metrics.json",
    }], attempt_id=task.current_attempt_id)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analysis queued", Actor.SERVER)
    job = repo.create_analysis_job(task.id, task.current_attempt_id, ids)

    assert repo.claim_analysis_job("worker-a", lease_sec=10).id == job.id
    session = new_session()
    stored = session.get(AnalysisJobModel, job.id)
    stored.lease_expires_at = now_utc() - timedelta(seconds=1)
    session.commit()
    session.close()

    reclaimed = repo.claim_analysis_job("worker-b", lease_sec=10)
    assert reclaimed.id == job.id
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.retry_count == 1


def test_running_cancel_sets_attempt_directive():
    repo = SqlRepository()
    task = _started_task(repo)
    repo.cancel_task(task.id, "operator cancelled")

    cancel, reason = repo.should_cancel_attempt(task.id, task.current_attempt_id)
    assert cancel is True
    assert reason == "operator cancelled"
    assert repo.get_attempt(task.current_attempt_id).status == "CANCEL_REQUESTED"

    response = HealthCheckService(repo).Do(healthcheck_pb2.HealthCheckRequest(
        agent_id="agent-drop",
        ip_addr="10.0.0.2",
        busy=True,
        active_task_id=task.id,
        active_attempt_id=task.current_attempt_id,
    ), None)
    assert response.cancel_active_task is True
    assert response.cancel_reason == "operator cancelled"


def test_cancelled_task_invalidates_pending_analysis_job():
    repo = SqlRepository()
    task = _started_task(repo)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collection complete", Actor.AGENT)
    ids = repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "filename": "metrics.json",
        "object_key": f"tasks/{task.id}/metrics.json",
    }], attempt_id=task.current_attempt_id)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analysis queued", Actor.SERVER)
    repo.finish_attempt(task.id, task.current_attempt_id, status="COLLECTED")
    job = repo.create_analysis_job(task.id, task.current_attempt_id, ids)

    repo.cancel_task(task.id, "operator cancelled")

    cancelled = repo.get_analysis_job(job.id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.error_code == "TASK_CANCELLED"
    assert repo.claim_analysis_job("worker-after-cancel") is None


def test_terminal_cancel_replay_preserves_agent_exit_code():
    repo = SqlRepository()
    task = _started_task(repo)
    repo.cancel_task(task.id, "operator cancelled")

    HotmethodService(repo).NotifyResult(hotmethod_pb2.TaskResult(
        task_id=task.id,
        attempt_id=task.current_attempt_id,
        cancelled=True,
        error_message="collector process group terminated",
        error_code="TASK_CANCELLED",
        exit_code=-15,
        resource_usage_json='{"rss_mb":12.5}',
    ), _AbortContext())

    attempt = repo.get_attempt(task.current_attempt_id)
    assert attempt.status == "CANCELLED"
    assert attempt.exit_code == -15
    assert attempt.resource_usage_json == {"rss_mb": 12.5}


def test_grpc_result_is_queued_then_completed_by_analyzer_worker():
    repo = SqlRepository()
    task = _started_task(repo)
    HotmethodService(repo).NotifyResult(hotmethod_pb2.TaskResult(
        task_id=task.id,
        attempt_id=task.current_attempt_id,
        result_message="采集到 5 个时间点",
        artifact_metadata_json=(
            '[{"artifact_type":"sys_metrics","filename":"metrics.json",'
            f'"object_key":"tasks/{task.id}/metrics.json"}}]'
        ),
    ), _AbortContext())

    queued = repo.tasks[task.id]
    assert queued.status == "ANALYZING"
    assert queued.collection_status == "COLLECTED"
    assert queued.analysis_status == "PENDING"
    assert repo.list_task_analysis_jobs(task.id)[0]["status"] == "PENDING"

    AnalysisWorker(repo, worker_id="worker-grpc").run_once()
    assert repo.tasks[task.id].status == "DONE"


def test_grpc_failure_preserves_stable_agent_error_code():
    repo = SqlRepository()
    task = _started_task(repo)
    HotmethodService(repo).NotifyResult(hotmethod_pb2.TaskResult(
        task_id=task.id,
        attempt_id=task.current_attempt_id,
        error_message="collector binary is unavailable",
        error_code="COLLECTOR_UNAVAILABLE",
        exit_code=127,
    ), _AbortContext())

    attempt = repo.get_attempt(task.current_attempt_id)
    assert attempt.status == "FAILED"
    assert attempt.error_code == "COLLECTOR_UNAVAILABLE"
    assert attempt.exit_code == 127
    assert repo.tasks[task.id].status == "FAILED"


def test_perf_worker_materializes_remote_raw_artifact(monkeypatch, tmp_path):
    repo = SqlRepository()
    task = _started_task(repo, "perf_cpu")
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collection complete", Actor.AGENT)
    ids = repo.add_artifacts(task.id, [{
        "artifact_type": "raw",
        "filename": "perf.data",
        "bucket": "mini-drop",
        "object_key": f"tasks/{task.id}/perf.data",
    }], attempt_id=task.current_attempt_id)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analysis queued", Actor.SERVER)
    repo.finish_attempt(task.id, task.current_attempt_id, status="COLLECTED")
    repo.create_analysis_job(task.id, task.current_attempt_id, ids)
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))

    generated_path = tmp_path / task.id / "top.json"
    generated_path.parent.mkdir(parents=True)
    generated_path.write_text("[]", encoding="utf-8")
    generated = [{
        "artifact_type": "top_json",
        "filename": "top.json",
        "local_path": str(generated_path),
        "size_bytes": 2,
    }]
    with mock.patch(
        "analyzer.mini_drop_analyzer.worker.inspect_artifact",
        return_value={
            "availability": "available",
            "actual_size_bytes": 9,
            "integrity_status": "not_checked",
        },
    ), mock.patch(
        "analyzer.mini_drop_analyzer.worker.read_artifact_bytes",
        return_value=b"perf-data",
    ), mock.patch(
        "analyzer.mini_drop_analyzer.worker.analyze_raw_perf_artifacts",
        return_value=generated,
    ):
        assert AnalysisWorker(repo, worker_id="worker-perf").run_once() is True

    assert repo.tasks[task.id].status == "DONE"
    assert any(item["artifact_type"] == "top_json" for item in repo.artifacts[task.id])


def test_analyzer_rejects_integrity_mismatch_without_retry():
    repo = SqlRepository()
    task = _started_task(repo, "perf_cpu")
    repo.transition_task(task.id, TaskStatus.UPLOADING, "collection complete", Actor.AGENT)
    ids = repo.add_artifacts(task.id, [{
        "artifact_type": "raw",
        "filename": "perf.data",
        "bucket": "mini-drop",
        "object_key": f"tasks/{task.id}/perf.data",
        "sha256": "a" * 64,
        "size_bytes": 9,
    }], attempt_id=task.current_attempt_id)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "analysis queued", Actor.SERVER)
    repo.finish_attempt(task.id, task.current_attempt_id, status="COLLECTED")
    job = repo.create_analysis_job(task.id, task.current_attempt_id, ids)

    with mock.patch(
        "analyzer.mini_drop_analyzer.worker.inspect_artifact",
        return_value={
            "availability": "available",
            "actual_size_bytes": 9,
            "integrity_status": "mismatch",
            "availability_reason": "sha256 mismatch",
        },
    ):
        assert AnalysisWorker(repo, worker_id="worker-integrity").run_once() is True

    failed = repo.get_analysis_job(job.id)
    assert failed.status == "FAILED"
    assert failed.retry_count == 1
    assert failed.error_code == "ANALYSIS_INPUT_INTEGRITY_MISMATCH"
    assert repo.tasks[task.id].status == "FAILED"


def test_pending_task_expires_before_dispatch(monkeypatch):
    monkeypatch.setenv("MINI_DROP_COLLECTION_QUEUE_TTL_SEC", "60")
    repo = SqlRepository()
    repo.register_agent("agent-drop", "worker", "10.0.0.2", capabilities=["sys_metrics"])
    task = repo.create_task(CreateTaskRequest(
        name="deadline",
        agent_id="agent-drop",
        target_pid=1234,
        collector_type="sys_metrics",
        duration_sec=5,
    ))
    session = new_session()
    stored = session.get(type(task), task.id)
    stored.collection_deadline_at = now_utc() - timedelta(seconds=1)
    session.commit()
    session.close()

    assert repo.heartbeat("agent-drop", "10.0.0.2") is None
    expired = repo.tasks[task.id]
    assert expired.status == "FAILED"
    assert expired.collection_status == "FAILED"
    assert "COLLECTION_QUEUE_DEADLINE_EXCEEDED" in expired.status_reason


class _AbortContext:
    @staticmethod
    def abort(_code, message):
        raise AssertionError(message)
