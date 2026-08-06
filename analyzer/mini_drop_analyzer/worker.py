"""Lease-based Mini-Drop analysis worker.

The worker is intentionally a separate process from the HTTP/gRPC control
plane.  It claims durable ``analysis_jobs`` rows, materializes remote input
when necessary, runs the registered pipeline, and commits the terminal task
state only while it still owns the lease.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

import server.app._env  # noqa: F401

from mini_drop_observability.tracing import configure_tracing, shutdown_tracing, start_span
from server.app.analyzer_runner import analyze_raw_perf_artifacts
from server.app.artifact_service import inspect_artifact, read_artifact_bytes
from server.app.database import init_db
from server.app.grpc_services.hotmethod_service import (
    _analysis_done_reason,
    _has_analysis_result,
)
from server.app.logging_utils import log_event
from server.app.sql_repository import SqlRepository
from server.app.state_machine import TaskStatus
from server.app import storage


ANALYSIS_RESULT_TYPES = {
    "flamegraph_json",
    "flamegraph_svg",
    "top_json",
    "ebpf_metrics",
    "continuous_summary",
    "continuous_flamegraph_json",
    "continuous_top_json",
    "java_flamegraph_html",
    "java_profile_jfr",
    "memory_json",
    "pprof_raw",
    "sys_metrics",
    "process_scan",
    "log_scan",
}
ANALYZER_VERSION = "0.2.0"
DEFAULT_MAX_INPUT_BYTES = 1024 * 1024 * 1024


class AnalysisWorker:
    def __init__(
        self,
        repo: SqlRepository | None = None,
        *,
        worker_id: str | None = None,
        lease_sec: int = 300,
    ) -> None:
        self.repo = repo or SqlRepository()
        self.worker_id = worker_id or f"analyzer-{socket.gethostname()}-{os.getpid()}"
        self.lease_sec = max(10, lease_sec)

    def run_once(self) -> bool:
        self.repo.heartbeat_analyzer(self.worker_id, status="IDLE")
        job = self.repo.claim_analysis_job(self.worker_id, self.lease_sec)
        if job is None:
            return False
        self.repo.heartbeat_analyzer(
            self.worker_id, status="RUNNING", current_job_id=job.id,
        )

        log_event(
            "info",
            "analysis_job_claimed",
            analysis_job_id=job.id,
            task_id=job.task_id,
            attempt_id=job.attempt_id,
            pipeline=job.pipeline,
        )
        renew_stop = threading.Event()
        renewer: threading.Thread | None = None
        try:
            task = self.repo.tasks.get(job.task_id)
            if task is None:
                raise AnalysisFailure("TASK_NOT_FOUND", "分析任务关联的 Task 不存在", False)
            if TaskStatus(task.status) == TaskStatus.CANCELLED:
                raise AnalysisFailure("TASK_CANCELLED", "任务已取消，无需继续分析", False)

            trace_scope = start_span(
                "mini_drop.analysis.run",
                traceparent=getattr(task, "traceparent", ""),
                link_only=True,
                kind="consumer",
                attributes={
                    "mini_drop.task.id": job.task_id,
                    "mini_drop.attempt.id": job.attempt_id,
                    "mini_drop.analysis.job.id": job.id,
                    "mini_drop.analysis.pipeline": job.pipeline,
                },
            )
            trace_scope.__enter__()

            artifacts = list(self.repo.artifacts.get(job.task_id, []))
            if not self.repo.renew_analysis_lease(job.id, self.worker_id, self.lease_sec):
                raise AnalysisFailure("ANALYSIS_LEASE_LOST", "分析租约已丢失", True)
            # 启动后台续租线程：下载（最多 1GB）与分析（最多 180s）的总时长
            # 可能超过租约 TTL，期间不续租会被 claim_analysis_job 重新领走，
            # 导致慢任务被重复分析并最终强制判 FAILED。
            renewer = self._start_lease_renewer(job.id, renew_stop)
            generated_ids: list[int] = []
            if not _has_analysis_result(artifacts):
                if job.pipeline != "perf_flamegraph":
                    raise AnalysisFailure(
                        "ANALYSIS_RESULT_MISSING",
                        f"采集器 {task.collector_type} 未产生可展示结果",
                        False,
                    )
                materialized, cleanup_root = self._materialize_perf_input(
                    job.id, job.task_id, artifacts,
                )
                try:
                    generated = analyze_raw_perf_artifacts(job.task_id, materialized)
                finally:
                    if cleanup_root is not None:
                        shutil.rmtree(cleanup_root, ignore_errors=True)
                if not generated:
                    raise AnalysisFailure(
                        "PERF_ANALYSIS_FAILED",
                        "perf 原始制品存在，但未能生成火焰图结果",
                        True,
                    )
                generated = self._publish_generated_outputs(job, generated)
                generated_ids = self.repo.add_artifacts(
                    job.task_id, generated, attempt_id=job.attempt_id,
                )
                artifacts.extend(generated)

            output_ids = list(dict.fromkeys([
                *generated_ids,
                *[
                    int(item["id"])
                    for item in artifacts
                    if item.get("artifact_type") in ANALYSIS_RESULT_TYPES and item.get("id") is not None
                ],
            ]))
            reason = _analysis_done_reason(artifacts)
            attempt = self.repo.get_attempt(job.attempt_id)
            if attempt is not None and attempt.result_message and attempt.result_message != reason:
                reason = f"{reason}；{attempt.result_message}"
            if not self.repo.renew_analysis_lease(job.id, self.worker_id, self.lease_sec):
                raise AnalysisFailure("ANALYSIS_LEASE_LOST", "提交前分析租约已丢失", True)
            self.repo.complete_analysis_job(
                job.id,
                self.worker_id,
                output_ids,
                reason,
                analyzer_version=ANALYZER_VERSION,
            )
            log_event(
                "info",
                "analysis_job_completed",
                analysis_job_id=job.id,
                task_id=job.task_id,
                output_count=len(output_ids),
            )
        except AnalysisFailure as exc:
            self._record_failure(job.id, exc.code, str(exc), exc.retryable)
            log_event(
                "warning",
                "analysis_job_failed",
                analysis_job_id=job.id,
                task_id=job.task_id,
                error_code=exc.code,
                retryable=exc.retryable,
            )
        except Exception as exc:  # defensive worker boundary
            self._record_failure(job.id, "ANALYZER_UNEXPECTED", str(exc), True)
            log_event(
                "error",
                "analysis_job_crashed",
                analysis_job_id=job.id,
                task_id=job.task_id,
                error=type(exc).__name__,
            )
        finally:
            renew_stop.set()
            if renewer is not None:
                renewer.join(timeout=2)
            scope = locals().get("trace_scope")
            if scope is not None:
                scope.__exit__(None, None, None)
        self.repo.heartbeat_analyzer(self.worker_id, status="IDLE")
        return True

    def _start_lease_renewer(self, job_id: str, stop_event: threading.Event) -> threading.Thread:
        """后台线程周期续租，覆盖下载+分析长耗时阶段。"""
        interval = max(1.0, self.lease_sec * 0.4)

        def _renew() -> None:
            while not stop_event.wait(interval):
                try:
                    self.repo.renew_analysis_lease(job_id, self.worker_id, self.lease_sec)
                except Exception:
                    # 续租失败（DB 抖动等）不打断主流程；提交前的
                    # renew_analysis_lease 最终校验负责兜底。
                    log_event(
                        "warning",
                        "analysis_lease_renew_failed",
                        analysis_job_id=job_id,
                        worker_id=self.worker_id,
                    )

        thread = threading.Thread(
            target=_renew,
            name=f"lease-renewer-{job_id[:16]}",
            daemon=True,
        )
        thread.start()
        return thread

    def _record_failure(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        """Record a failure only while this worker still owns the lease."""

        try:
            self.repo.fail_analysis_job(
                job_id,
                self.worker_id,
                error_code,
                error_message,
                retryable=retryable,
            )
        except ValueError as exc:
            if str(exc) != "ANALYSIS_LEASE_LOST":
                raise
            log_event(
                "warning",
                "analysis_failure_commit_skipped",
                analysis_job_id=job_id,
                error_code=error_code,
                reason="lease_lost",
            )

    def _materialize_perf_input(
        self,
        job_id: str,
        task_id: str,
        artifacts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], Path | None]:
        for artifact in artifacts:
            if artifact.get("artifact_type") != "raw":
                continue
            filename = artifact.get("filename") or ""
            if filename and filename != "perf.data":
                continue
            inspected = inspect_artifact(
                task_id,
                artifact,
                check_availability=True,
                verify_hash=bool(artifact.get("sha256")),
            )
            if inspected.get("availability") != "available":
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_UNAVAILABLE",
                    str(inspected.get("availability_reason") or "analysis input unavailable"),
                    True,
                )
            if inspected.get("integrity_status") == "mismatch":
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_INTEGRITY_MISMATCH",
                    str(inspected.get("availability_reason") or "analysis input integrity mismatch"),
                    False,
                )
            max_bytes = _max_input_bytes()
            actual_size = int(inspected.get("actual_size_bytes") or 0)
            if actual_size > max_bytes:
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_TOO_LARGE",
                    f"analysis input is {actual_size} bytes; limit is {max_bytes}",
                    False,
                )
            local_path = artifact.get("local_path")
            if local_path and Path(local_path).is_file():
                return artifacts, None

            try:
                payload = read_artifact_bytes(artifact)
            except Exception as exc:
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_UNAVAILABLE",
                    f"无法读取 perf 原始制品: {type(exc).__name__}",
                    True,
                ) from exc
            if not payload:
                raise AnalysisFailure("ANALYSIS_INPUT_EMPTY", "perf 原始制品为空", False)
            if len(payload) > max_bytes:
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_TOO_LARGE",
                    f"analysis input is {len(payload)} bytes; limit is {max_bytes}",
                    False,
                )
            expected_size = int(artifact.get("size_bytes") or 0)
            if expected_size and len(payload) != expected_size:
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_INTEGRITY_MISMATCH",
                    "analysis input size does not match registered metadata",
                    False,
                )
            expected_sha = str(artifact.get("sha256") or "").lower()
            if expected_sha and hashlib.sha256(payload).hexdigest() != expected_sha:
                raise AnalysisFailure(
                    "ANALYSIS_INPUT_INTEGRITY_MISMATCH",
                    "analysis input sha256 does not match registered metadata",
                    False,
                )

            root = Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()
            temp_root = root / ".analysis-input" / job_id
            temp_root.mkdir(parents=True, exist_ok=True)
            perf_path = temp_root / "perf.data"
            perf_path.write_bytes(payload)
            materialized = [dict(item) for item in artifacts]
            target = next(item for item in materialized if item.get("artifact_type") == "raw")
            target["local_path"] = str(perf_path)
            target["filename"] = "perf.data"
            return materialized, temp_root
        raise AnalysisFailure(
            "ANALYSIS_INPUT_MISSING",
            "perf analysis job has no raw perf.data artifact",
            False,
        )

    @staticmethod
    def _publish_generated_outputs(job, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Publish analyzer outputs when Server and worker do not share a disk."""

        upload_enabled = os.getenv("MINI_DROP_ANALYZER_UPLOAD", "1").strip().lower() in {
            "1", "true", "yes", "on",
        }
        bucket = os.getenv("MINIO_BUCKET", "mini-drop")
        result: list[dict[str, Any]] = []
        for artifact in artifacts:
            item = dict(artifact)
            item["metadata"] = {
                **(item.get("metadata") or {}),
                "analysis_job_id": job.id,
                "attempt_id": job.attempt_id,
                "analyzer_version": ANALYZER_VERSION,
            }
            if not upload_enabled:
                result.append(item)
                continue
            local_path = item.get("local_path")
            if not local_path or not Path(local_path).is_file():
                result.append(item)
                continue
            filename = item.get("filename") or Path(local_path).name
            object_key = f"tasks/{job.task_id}/attempts/{job.attempt_id}/analysis/{filename}"
            storage.upload_file(
                local_path,
                bucket,
                object_key,
                item.get("content_type") or "application/octet-stream",
            )
            item["bucket"] = bucket
            item["object_key"] = object_key
            result.append(item)
        return result


def _max_input_bytes() -> int:
    try:
        value = int(os.getenv("MINI_DROP_ANALYZER_MAX_INPUT_BYTES", str(DEFAULT_MAX_INPUT_BYTES)))
    except ValueError:
        value = DEFAULT_MAX_INPUT_BYTES
    return max(1, value)


class AnalysisFailure(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


_stop = False


def _handle_signal(_signum, _frame) -> None:
    global _stop
    _stop = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop asynchronous analyzer worker")
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--lease-sec", type=int, default=300)
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()

    configure_tracing("mini-drop-analyzer", service_version=ANALYZER_VERSION)
    try:
        init_db()
        worker = AnalysisWorker(worker_id=args.worker_id, lease_sec=args.lease_sec)
        if args.once:
            worker.run_once()
            return

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        while not _stop:
            worked = worker.run_once()
            if not worked:
                time.sleep(max(0.1, args.poll_interval))
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
