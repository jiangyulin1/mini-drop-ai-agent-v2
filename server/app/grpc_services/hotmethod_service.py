"""Hotmethod gRPC 服务：接收 Agent 采集结果。"""

import json
from typing import Any

from google.protobuf.empty_pb2 import Empty

from server.app.analyzer_runner import analyze_raw_perf_artifacts
from server.app.generated import hotmethod_pb2_grpc
from server.app.state_machine import Actor, TaskStatus

MAX_ARTIFACTS_PER_TASK = 32
MAX_ARTIFACT_FIELD_LENGTH = 512
MAX_ERROR_MESSAGE_LENGTH = 1024


class HotmethodService(hotmethod_pb2_grpc.HotmethodServicer):
    """采集结果上报服务。"""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def NotifyResult(self, request, context) -> Empty:
        task_id = request.task_id

        if request.error_message:
            reason = _safe_text(request.error_message, max_length=MAX_ERROR_MESSAGE_LENGTH) or "Agent reported collection failure"
            # Agent 报告采集失败
            self._repo.transition_task(
                task_id, TaskStatus.FAILED,
                reason, Actor.AGENT,
            )
            return Empty()

        # 采集成功：先迁移到 UPLOADING，写入产物元数据
        self._repo.transition_task(
            task_id, TaskStatus.UPLOADING,
            "采集完成，准备上传产物", Actor.AGENT,
        )

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

        if artifacts:
            self._repo.add_artifacts(task_id, artifacts)

        # 产物写入后迁移到 ANALYZING；如果 Agent 已同步产出分析结果，MVP 闭环直接完成任务。
        self._repo.transition_task(
            task_id, TaskStatus.ANALYZING,
            "产物已记录，等待分析", Actor.SERVER,
        )
        if not _has_analysis_result(artifacts):
            generated_artifacts = analyze_raw_perf_artifacts(task_id, artifacts)
            if generated_artifacts:
                self._repo.add_artifacts(task_id, generated_artifacts)
                artifacts.extend(generated_artifacts)
        if _has_analysis_result(artifacts):
            self._repo.transition_task(
                task_id, TaskStatus.DONE,
                "Analyzer 已生成火焰图和热点分析结果", Actor.ANALYZER,
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
        "memory_json",
        "pprof_raw",
        "sys_metrics",
    } & artifact_types)


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
