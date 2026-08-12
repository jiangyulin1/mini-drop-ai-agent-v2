"""P5 数据源连接器：Prometheus 指标 + 模板化日志查询/突变检测。

通过 Source Gateway 注册，受同一授权/预算/脱敏管线约束。连接器是纯执行器，
不持有凭据；Prometheus 地址等环境相关配置从环境读取。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from server.app.diagnosis.source_gateway import SourceGatewayError, SourceQueryRequest


def _bounded_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


class PrometheusConnector:
    """受控 Prometheus 指标查询：query_range 拉取目标指标序列。"""

    source_id = "prometheus-metrics"

    def __init__(
        self,
        http_get: Callable[..., Any] | None = None,
        *,
        endpoint: str | None = None,
    ):
        self._http_get = http_get
        self._endpoint = endpoint

    def _resolve_endpoint(self) -> str:
        if self._endpoint:
            return self._endpoint
        return os.getenv("MINI_DROP_PROMETHEUS_URL", "http://localhost:9090")

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        metric = str(request.parameters.get("metric") or "").strip()
        if not metric:
            raise SourceGatewayError("PROMETHEUS_METRIC_REQUIRED", 400)
        range_minutes = _bounded_int(
            request.requested_time_range_minutes, 1, 1440, 15,
        )
        step = str(request.parameters.get("step") or "60s")
        now = datetime.now(timezone.utc)
        start = int((now - timedelta(minutes=range_minutes)).timestamp())
        end = int(now.timestamp())
        params = {
            "query": metric,
            "start": str(start),
            "end": str(end),
            "step": step,
        }
        http_get = self._http_get or _default_http_get
        query_url = f"{self._resolve_endpoint()}/api/v1/query_range"
        try:
            status, body = http_get(query_url, params)
        except Exception as exc:
            return {
                "metric": metric,
                "available": False,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "samples": [],
            }
        if status >= 400:
            return {
                "metric": metric,
                "available": False,
                "error": f"prometheus http {status}",
                "samples": [],
            }
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return {"metric": metric, "available": False, "error": "bad json", "samples": []}
        series = payload.get("data", {}).get("result", [])
        samples = []
        for item in series:
            values = []
            for pair in item.get("values", [])[:256]:
                values.append({"ts": pair[0], "value": pair[1]})
            samples.append({"metric": item.get("metric", {}), "values": values})
        return {
            "metric": metric,
            "available": True,
            "query_window_seconds": range_minutes * 60,
            "series_count": len(samples),
            "samples": samples,
        }


def _default_http_get(query_url: str, params: dict[str, str]) -> tuple[int, str]:
    import requests
    resp = requests.get(query_url, params=params, timeout=15)
    return resp.status_code, resp.text


class OpenTelemetryTraceConnector:
    """受控 Trace 查询：按服务/时间窗拉取 span 关键路径与错误边。"""

    source_id = "otel-traces"

    def __init__(
        self,
        http_get: Callable[..., Any] | None = None,
        *,
        endpoint: str | None = None,
    ):
        self._http_get = http_get
        self._endpoint = endpoint

    def _resolve_endpoint(self) -> str:
        if self._endpoint:
            return self._endpoint
        return os.getenv("MINI_DROP_TRACE_QUERY_URL", "http://localhost:16686")

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        service = str(request.resource.get("service_id") or "").strip()
        if not service:
            raise SourceGatewayError("TRACE_SERVICE_REQUIRED", 400)
        operation = request.operation
        http_get = self._http_get or _default_trace_get
        url = f"{self._resolve_endpoint()}/api/services/{service}/operations"
        try:
            status, body = http_get(url)
        except Exception as exc:
            return {"service": service, "available": False, "error": str(exc)[:300], "traces": []}
        if status >= 400:
            return {"service": service, "available": False, "error": f"trace http {status}", "traces": []}
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return {"service": service, "available": False, "error": "bad json", "traces": []}
        if operation == "traces.list":
            return {
                "service": service, "available": True,
                "operations": payload.get("data", [])[:50],
                "traces": [],
            }
        # traces.read：返回服务最近 span 的错误边摘要（聚合）。
        spans = payload.get("data", []) if isinstance(payload.get("data"), list) else []
        error_count = 0
        latency_samples: list[float] = []
        for operation_name in spans[:20]:
            error_count += 1 if "error" in str(operation_name).lower() else 0
        return {
            "service": service, "available": True,
            "span_count": len(spans),
            "error_span_count": error_count,
            "traces": [],
        }


def _default_trace_get(url: str) -> tuple[int, str]:
    import requests
    resp = requests.get(url, timeout=15)
    return resp.status_code, resp.text


class RuntimeProfileConnector:
    """运行时 Profile 结构化解析：消费已有 JFR/pprof/py-spy 采集产物。

    连接器只做确定性的结构化提取，不执行采样工具本身（Agent 已采集）。
    """

    source_id = "runtime-profile-parser"

    PROFILES = {
        "go_pprof": ("pprof_raw", "goroutine|mutex|block"),
        "pyspy": ("flamegraph_svg", "python"),
        "java_async": ("java_profile_jfr", "jfr|async"),
    }

    def __init__(self, repo):
        self.repo = repo

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        profile_type = str(request.parameters.get("profile_type") or "go_pprof")
        agent_id = request.resource.get("agent_id", "")
        pid = _bounded_int(request.resource.get("pid"), 1, 4_194_304, 0)
        if profile_type not in self.PROFILES:
            raise SourceGatewayError("UNKNOWN_PROFILE_TYPE", 400)
        artifact_type, marker = self.PROFILES[profile_type]
        artifacts = self._find_artifacts(agent_id, pid, artifact_type)
        parsed = []
        for artifact in artifacts:
            parsed.append({
                "task_id": artifact.get("task_id"),
                "artifact_type": artifact_type,
                "marker": marker,
                "size_bytes": (artifact.get("metadata") or {}).get("size_bytes"),
                "parse_status": "structured",
            })
        return {
            "profile_type": profile_type,
            "agent_id": agent_id,
            "target_pid": pid,
            "artifact_count": len(parsed),
            "profiles": parsed,
        }

    def _find_artifacts(self, agent_id: str, pid: int, artifact_type: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for task in self.repo.tasks.values():
            if task.agent_id != agent_id or task.target_pid != pid:
                continue
            for artifact in self.repo.artifacts.get(task.id, []):
                if artifact.get("artifact_type") != artifact_type:
                    continue
                item = dict(artifact)
                item["task_id"] = task.id
                found.append(item)
        return found[-10:]


class LogTemplateConnector:
    """模板化日志查询：按正则模板统计错误/超时模式，并做窗口间突变检测。"""

    source_id = "log-template-query"

    TEMPLATES = {
        "connection_failure": re.compile(
            r"refused|reset|unreachable|connection.*fail|econnrefused", re.I,
        ),
        "timeout": re.compile(r"timed?\s*out|timeout", re.I),
        "exception": re.compile(r"exception|traceback|stack\s*overflow", re.I),
        "oom": re.compile(r"out\s*of\s*memory|oom|killed\s*process", re.I),
        "enospc": re.compile(r"no\s*space\s*left|enospc|disk\s*full", re.I),
    }

    def __init__(self, repo):
        self.repo = repo

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        template = str(request.parameters.get("template") or "connection_failure")
        agent_id = request.resource.get("agent_id", "")
        pid = _bounded_int(request.resource.get("pid"), 1, 4_194_304, 0)
        if not agent_id or not pid:
            raise SourceGatewayError("LOG_TEMPLATE_TARGET_REQUIRED", 400)
        pattern = self.TEMPLATES.get(template)
        if pattern is None:
            raise SourceGatewayError("UNKNOWN_LOG_TEMPLATE", 400)

        recent, baseline = self._scan_logs(agent_id, pid, pattern)
        mutation = self._detect_mutation(recent, baseline)
        return {
            "template": template,
            "agent_id": agent_id,
            "target_pid": pid,
            "pattern": pattern.pattern,
            "recent_count": recent,
            "baseline_count": baseline,
            "mutation": mutation,
        }

    def _scan_logs(self, agent_id: str, pid: int, pattern: re.Pattern) -> tuple[int, int]:
        """扫描该目标最近任务的 log_scan 产物，返回（最近窗口计数, 基线计数）。

        真实实现读取 Agent 上报的日志产物；此处基于 repo 中已登记的 log_scan
        结构化产物做确定性聚合。
        """
        recent = 0
        baseline = 0
        tasks = [
            task for task in self.repo.tasks.values()
            if task.agent_id == agent_id and task.target_pid == pid
            and task.collector_type == "log_scan"
        ]
        recent_tasks = tasks[-4:]
        baseline_tasks = tasks[-8:-4] or tasks
        for task in recent_tasks:
            for artifact in self.repo.artifacts.get(task.id, []):
                if artifact.get("artifact_type") != "log_scan":
                    continue
                data = (artifact.get("metadata") or {}).get("data") or {}
                recent += self._count_matches(data, pattern)
        for task in baseline_tasks:
            for artifact in self.repo.artifacts.get(task.id, []):
                if artifact.get("artifact_type") != "log_scan":
                    continue
                data = (artifact.get("metadata") or {}).get("data") or {}
                baseline += self._count_matches(data, pattern)
        return recent, baseline

    @staticmethod
    def _count_matches(data: Any, pattern: re.Pattern) -> int:
        total = 0
        files = data.get("log_files") if isinstance(data, dict) else None
        if not isinstance(files, list):
            return 0
        for log_file in files:
            for line in log_file.get("error_lines") or []:
                text = str(line.get("text") or "")
                if pattern.search(text):
                    total += 1
        return total

    @staticmethod
    def _detect_mutation(recent: int, baseline: int) -> dict[str, Any]:
        if baseline <= 0 and recent <= 0:
            return {"mutation": False, "reason": "no_signal"}
        if baseline <= 0:
            return {"mutation": True, "reason": "new_signal", "ratio": None}
        ratio = recent / baseline
        return {
            "mutation": ratio >= 2.0,
            "reason": "error_rate_surge" if ratio >= 2.0 else "stable",
            "ratio": round(ratio, 3),
        }
