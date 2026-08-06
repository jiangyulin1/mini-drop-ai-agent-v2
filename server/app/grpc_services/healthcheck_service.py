"""HealthCheck gRPC 服务：1 Hz 心跳 + 任务下发。"""

import json
from typing import Any

from mini_drop_observability.tracing import start_span
from server.app.generated import healthcheck_pb2, healthcheck_pb2_grpc, hotmethod_pb2


class HealthCheckService(healthcheck_pb2_grpc.HealthCheckServicer):
    """Agent 心跳服务，一次 RPC 同时完成保活和任务拉取。"""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def Do(self, request: healthcheck_pb2.HealthCheckRequest, context) -> healthcheck_pb2.HealthCheckResponse:
        # 记录心跳，检查有无待执行任务
        if hasattr(self._repo, "record_agent_metrics"):
            self._repo.record_agent_metrics(request.agent_id, _metrics_from_request(request))
        response = healthcheck_pb2.HealthCheckResponse()
        response.status = healthcheck_pb2.HealthCheckResponse.SERVING

        active_task_id = getattr(request, "active_task_id", "")
        active_attempt_id = getattr(request, "active_attempt_id", "")
        if active_task_id and hasattr(self._repo, "should_cancel_attempt"):
            cancel, reason = self._repo.should_cancel_attempt(active_task_id, active_attempt_id)
            response.cancel_active_task = cancel
            if cancel:
                response.cancel_reason = reason

        if getattr(request, "busy", False):
            if hasattr(self._repo, "heartbeat_only"):
                self._repo.heartbeat_only(request.agent_id, request.ip_addr)
            response.pending = False
            return response

        task = self._repo.heartbeat(request.agent_id, request.ip_addr)

        if task is None:
            response.pending = False
            return response

        # 构造 TaskDesc 并嵌入响应
        response.pending = True
        task_desc = response.task_desc
        task_desc.task_id = task.id
        task_desc.attempt_id = getattr(task, "current_attempt_id", "") or ""
        task_desc.request_id = getattr(task, "request_id", "") or ""
        task_desc.traceparent = getattr(task, "traceparent", "") or ""
        task_desc.task_type = 0  # 通用任务
        task_desc.profiler_type = self._profiler_type(task.collector_type)
        task_desc.timeout_sec = task.duration_sec + 30  # 留 30 秒余量
        task_desc.sample_argv.hz = task.sample_rate
        task_desc.sample_argv.duration = task.duration_sec
        task_desc.sample_argv.pid = task.target_pid
        task_desc.sample_argv.callgraph = task.request_params.get("options", {}).get("callgraph", "fp")
        default_event = "cpu" if task.collector_type == "java_async" else "cpu-cycles"
        task_desc.sample_argv.event = task.request_params.get("options", {}).get("event", default_event)
        task_desc.sample_argv.subprocess = task.request_params.get("options", {}).get("subprocess", False)
        options = task.request_params.get("options", {})
        if isinstance(options, dict):
            options_json = json.dumps(
                options,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(options_json.encode("utf-8")) <= 8192:
                task_desc.options_json = options_json
        with start_span(
            "mini_drop.task.dispatch",
            traceparent=task_desc.traceparent,
            kind="producer",
            attributes={
                "mini_drop.task.id": task.id,
                "mini_drop.attempt.id": task_desc.attempt_id,
                "mini_drop.agent.id": request.agent_id,
            },
        ):
            return response

    @staticmethod
    def _profiler_type(collector_type: str) -> int:
        """将 collector_type 字符串映射为 protobuf profiler_type 值。

        Proto 定义（hotmethod.proto）:
          0=perf, 1=async-profiler(Java), 2=pprof(Go), 3=py-spy, 4=bpftrace
          5=memory_smaps, 6=sys_metrics, 7=continuous_perf
          8=process_scan, 9=log_scan
        """
        mapping: dict[str, int] = {
            "perf_cpu": 0,
            "java_async": 1,
            "go_pprof": 2,
            "pyspy": 3,
            "ebpf_io": 4,
            "memory_smaps": 5,
            "sys_metrics": 6,
            "continuous_perf": 7,
            "process_scan": 8,
            "log_scan": 9,
        }
        return mapping.get(collector_type, 0)


def _pid_stats_to_dict(stats) -> dict:
    return {
        "cpu_percent": round(float(stats.cpu_percent), 3),
        "rss_mb": round(float(stats.rss_mb), 3),
        "read_kb_s": round(float(stats.read_kb_s), 3),
        "write_kb_s": round(float(stats.write_kb_s), 3),
        "children_count": int(stats.children_count),
    }


def _metrics_from_request(request: healthcheck_pb2.HealthCheckRequest) -> dict:
    return {
        "self": _pid_stats_to_dict(request.self_pstats),
        "children": _pid_stats_to_dict(request.children_pstats),
    }
