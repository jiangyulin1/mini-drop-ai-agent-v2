"""Hotmethod gRPC 服务：接收 Agent 采集结果。"""

import json
import re
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty

from mini_drop_observability.tracing import start_span
from server.app.analyzer_runner import analyze_raw_perf_artifacts
from server.app.generated import hotmethod_pb2_grpc
from server.app.logging_utils import log_event
from server.app.state_machine import Actor, TERMINAL_STATES, TaskStatus

MAX_ARTIFACTS_PER_TASK = 32
MAX_ARTIFACT_FIELD_LENGTH = 512
MAX_ERROR_MESSAGE_LENGTH = 1024


class HotmethodService(hotmethod_pb2_grpc.HotmethodServicer):
    """采集结果上报服务。"""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def NotifyResult(self, request, context) -> Empty:
        with start_span(
            "mini_drop.result.notify",
            traceparent=getattr(request, "traceparent", ""),
            kind="server",
            attributes={
                "mini_drop.task.id": request.task_id,
                "mini_drop.attempt.id": getattr(request, "attempt_id", ""),
            },
        ):
            return self._notify_result(request, context)

    def _notify_result(self, request, context) -> Empty:
        task_id = request.task_id
        task = self._repo.tasks.get(task_id)
        if task is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "任务不存在")
        current = TaskStatus(task.status)
        request_id = _safe_text(
            getattr(request, "request_id", "") or getattr(task, "request_id", ""),
            max_length=64,
        )
        log_event(
            "info",
            "agent_result_received",
            task_id=task_id,
            attempt_id=getattr(request, "attempt_id", ""),
            request_id=request_id,
            cancelled=bool(getattr(request, "cancelled", False)),
            has_error=bool(getattr(request, "error_message", "")),
        )
        attempt_id = getattr(request, "attempt_id", "") or getattr(task, "current_attempt_id", None)

        if attempt_id and hasattr(self._repo, "get_attempt"):
            attempt = self._repo.get_attempt(attempt_id)
            if attempt is None or attempt.task_id != task_id:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "执行尝试与任务不匹配")

        # Agent may resend after losing the gRPC acknowledgement. A terminal
        # task already contains the authoritative result, so acknowledge the
        # replay without writing duplicate events or artifacts.
        if current in TERMINAL_STATES:
            if current == TaskStatus.CANCELLED and hasattr(self._repo, "finish_attempt"):
                try:
                    self._repo.finish_attempt(
                        task_id, attempt_id, status="CANCELLED",
                        error_code="TASK_CANCELLED",
                        error_message=getattr(request, "error_message", "") or task.status_reason,
                        exit_code=getattr(request, "exit_code", 0),
                        resource_usage=_resource_usage(request),
                    )
                except ValueError:
                    pass
            return Empty()

        if getattr(request, "cancelled", False):
            error_code = _safe_error_code(getattr(request, "error_code", "")) or "TASK_CANCELLED"
            if hasattr(self._repo, "finish_attempt"):
                self._repo.finish_attempt(
                    task_id, attempt_id, status="CANCELLED",
                    error_code=error_code,
                    error_message=_safe_text(request.error_message) or "Agent 已终止采集器",
                    exit_code=getattr(request, "exit_code", 0),
                    resource_usage=_resource_usage(request),
                )
            self._repo.cancel_task(
                task_id,
                _safe_text(request.error_message) or "Agent 已终止采集器",
                Actor.AGENT,
            )
            return Empty()

        if request.error_message:
            reason = _safe_text(request.error_message, max_length=MAX_ERROR_MESSAGE_LENGTH) or "Agent reported collection failure"
            error_code = _safe_error_code(getattr(request, "error_code", "")) or "RUNNER_FAILED"
            # Agent 报告采集失败
            if hasattr(self._repo, "finish_attempt"):
                self._repo.finish_attempt(
                    task_id, attempt_id, status="FAILED",
                    error_code=error_code, error_message=reason,
                    exit_code=getattr(request, "exit_code", 0),
                    resource_usage=_resource_usage(request),
                )
            self._repo.transition_task(
                task_id, TaskStatus.FAILED,
                reason, Actor.AGENT,
            )
            return Empty()

        if current == TaskStatus.PENDING:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "任务尚未下发，不能上报结果")

        success_message = _safe_text(
            getattr(request, "result_message", ""),
            max_length=MAX_ERROR_MESSAGE_LENGTH,
        )

        if current == TaskStatus.RUNNING:
            self._repo.transition_task(
                task_id, TaskStatus.UPLOADING,
                success_message or "采集完成，准备上传产物", Actor.AGENT,
            )
            current = TaskStatus.UPLOADING

        # 解析 artifact 元数据
        artifacts: list[dict] = []
        if request.artifact_metadata_json:
            try:
                artifacts = json.loads(request.artifact_metadata_json)
            except json.JSONDecodeError:
                artifacts = [{"artifact_type": request.artifact_type, "cos_key": request.cos_key}]
        elif request.cos_key:
            artifacts = [{"artifact_type": request.artifact_type or "raw", "cos_key": request.cos_key}]
        artifacts = _sanitize_artifacts(artifacts)

        artifact_ids: list[int] = []
        if artifacts:
            try:
                artifact_ids = self._repo.add_artifacts(task_id, artifacts, attempt_id=attempt_id)
            except TypeError:
                # Compatibility with the in-memory repository used by a few
                # lightweight tests and external integrations.
                self._repo.add_artifacts(task_id, artifacts)

        if current == TaskStatus.UPLOADING:
            self._repo.transition_task(
                task_id, TaskStatus.ANALYZING,
                "产物已记录，等待分析", Actor.SERVER,
            )
            current = TaskStatus.ANALYZING
        if hasattr(self._repo, "finish_attempt"):
            self._repo.finish_attempt(
                task_id, attempt_id, status="COLLECTED",
                result_message=success_message,
                exit_code=getattr(request, "exit_code", 0),
                resource_usage=_resource_usage(request),
            )
        if hasattr(self._repo, "create_analysis_job"):
            self._repo.create_analysis_job(task_id, attempt_id, artifact_ids)
        else:
            # Legacy in-memory mode has no asynchronous worker.
            if not _has_analysis_result(artifacts):
                generated_artifacts = analyze_raw_perf_artifacts(task_id, artifacts)
                if generated_artifacts:
                    self._repo.add_artifacts(task_id, generated_artifacts)
                    artifacts.extend(generated_artifacts)
            if _has_analysis_result(artifacts):
                analysis_reason = _analysis_done_reason(artifacts)
                final_reason = (
                    f"{analysis_reason}；{success_message}"
                    if success_message and success_message != analysis_reason
                    else analysis_reason
                )
                self._repo.transition_task(
                    task_id, TaskStatus.DONE, final_reason, Actor.ANALYZER,
                )

        return Empty()


def _has_analysis_result(artifacts: list[dict]) -> bool:
    artifact_types = {item.get("artifact_type") for item in artifacts}
    return bool({
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
    } & artifact_types)


def _analysis_done_reason(artifacts: list[dict]) -> str:
    artifact_types = {item.get("artifact_type") for item in artifacts}
    if "ebpf_metrics" in artifact_types:
        return "eBPF IO 延迟分布已生成"
    if "memory_json" in artifact_types:
        return "内存时间序列分析已生成"
    if "sys_metrics" in artifact_types:
        return "系统多维指标分析已生成"
    if "process_scan" in artifact_types:
        return "进程扫描清单已生成"
    if "log_scan" in artifact_types:
        return "日志扫描结果已生成"
    if "continuous_summary" in artifact_types:
        return "连续采样窗口分析已生成"
    if "java_flamegraph_html" in artifact_types:
        return "Java 火焰图已生成"
    if "java_profile_jfr" in artifact_types:
        return "Java JFR 采样文件已生成"
    if "pprof_raw" in artifact_types:
        return "pprof 原始数据已记录"
    if {"flamegraph_json", "flamegraph_svg", "top_json"} & artifact_types:
        return "Analyzer 已生成火焰图和热点分析结果"
    return "分析结果已生成"


def _sanitize_artifacts(raw_artifacts) -> list[dict]:
    if not isinstance(raw_artifacts, list):
        return []

    sanitized: list[dict] = []
    for item in raw_artifacts[:MAX_ARTIFACTS_PER_TASK]:
        if not isinstance(item, dict):
            continue

        artifact_type = _safe_text(item.get("artifact_type") or "raw", max_length=64)
        if not artifact_type:
            artifact_type = "raw"
        artifact: dict = {"artifact_type": artifact_type}

        for key in ("bucket", "object_key", "cos_key", "filename", "local_path", "content_type"):
            value = _safe_text(item.get(key))
            if value:
                artifact[key] = value
        if not artifact.get("object_key") and artifact.get("cos_key"):
            artifact["object_key"] = artifact["cos_key"]

        sha256 = _safe_text(item.get("sha256"), max_length=64).lower()
        if re.fullmatch(r"[0-9a-f]{64}", sha256):
            artifact["sha256"] = sha256

        try:
            size_bytes = int(item.get("size_bytes", 0) or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        artifact["size_bytes"] = max(0, size_bytes)

        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            artifact["metadata"] = _sanitize_metadata(metadata)

        sanitized.append(artifact)
    return sanitized


def _sanitize_metadata(metadata: dict) -> dict:
    result: dict = {}
    for key, value in list(metadata.items())[:32]:
        safe_key = _safe_text(key, max_length=64)
        if not safe_key:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[safe_key] = value if not isinstance(value, str) else _safe_text(value)
    return result


def _safe_text(value, max_length: int = MAX_ARTIFACT_FIELD_LENGTH) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "")[:max_length].strip()


def _safe_error_code(value: Any) -> str:
    code = _safe_text(value, max_length=128).upper()
    return code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", code) else ""


def _resource_usage(request) -> dict:
    raw = getattr(request, "resource_usage_json", "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return _sanitize_metadata(value)
