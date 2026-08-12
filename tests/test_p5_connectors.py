"""P5 全量数据源测试：Trace、运行时 Profile、服务基线。"""

from __future__ import annotations

import json

import pytest

from server.app.diagnosis.data_sources import (
    OpenTelemetryTraceConnector,
    RuntimeProfileConnector,
)
from server.app.diagnosis.service_baseline import build_baseline, detect_anomaly, rolling_window
from server.app.diagnosis.source_gateway import SourceGatewayError, SourceQueryRequest


def _request(operation: str, resource: dict | None = None, parameters: dict | None = None) -> SourceQueryRequest:
    return SourceQueryRequest(
        tenant_id="tenant-a",
        operation=operation,
        resource=resource or {},
        parameters=parameters or {},
        requested_time_range_minutes=15,
    )


def test_trace_connector_lists_operations():
    def fake_get(url):
        assert "/api/services/paymentservice/operations" in url
        return 200, json.dumps({"data": ["get", "post", "error_handler"]})

    connector = OpenTelemetryTraceConnector(http_get=fake_get, endpoint="http://jaeger")
    result = connector.execute(_request("traces.list", resource={"service_id": "paymentservice"}))
    assert result["available"] is True
    assert "error_handler" in result["operations"]


def test_trace_connector_requires_service():
    connector = OpenTelemetryTraceConnector(endpoint="http://jaeger")
    with pytest.raises(SourceGatewayError):
        connector.execute(_request("traces.list"))


def test_trace_connector_graceful_on_failure():
    def failing(url):
        raise OSError("down")

    connector = OpenTelemetryTraceConnector(http_get=failing, endpoint="http://jaeger")
    result = connector.execute(_request("traces.list", resource={"service_id": "s"}))
    assert result["available"] is False
    assert result["traces"] == []


class _Task:
    def __init__(self, task_id, agent_id, pid, collector_type):
        self.id = task_id
        self.agent_id = agent_id
        self.target_pid = pid
        self.collector_type = collector_type


class _Repo:
    def __init__(self):
        self.tasks = {}
        self.artifacts = {}

    def register(self, task_id, agent_id, pid, artifact_type):
        self.tasks[task_id] = _Task(task_id, agent_id, pid, artifact_type)
        self.artifacts[task_id] = [{"artifact_type": artifact_type, "metadata": {"size_bytes": 100}}]


def test_runtime_profile_parser_finds_artifacts():
    repo = _Repo()
    repo.register("t1", "a1", 100, "pprof_raw")
    repo.register("t2", "a1", 100, "pprof_raw")
    repo.register("t3", "a1", 200, "pprof_raw")  # 不同 pid
    connector = RuntimeProfileConnector(repo)
    result = connector.execute(_request(
        "profile.parse",
        resource={"agent_id": "a1", "pid": "100"},
        parameters={"profile_type": "go_pprof"},
    ))
    assert result["artifact_count"] == 2
    assert all(item["artifact_type"] == "pprof_raw" for item in result["profiles"])


def test_runtime_profile_rejects_unknown_type():
    connector = RuntimeProfileConnector(_Repo())
    with pytest.raises(SourceGatewayError):
        connector.execute(_request(
            "profile.parse",
            resource={"agent_id": "a1", "pid": "100"},
            parameters={"profile_type": "bogus"},
        ))


def test_baseline_computes_percentiles():
    baseline = build_baseline([10, 20, 30, 40, 50])
    assert baseline["available"] is True
    assert baseline["p50"] == 30
    assert baseline["p95"] == 48.0
    assert baseline["count"] == 5


def test_baseline_empty_unavailable():
    assert build_baseline([])["available"] is False


def test_anomaly_detection():
    baseline = build_baseline([10, 20, 30, 40, 50])
    normal = detect_anomaly(30, baseline)
    assert normal["anomalous"] is False
    spike = detect_anomaly(120, baseline)
    assert spike["anomalous"] is True
    assert spike["severity"] == "critical"
    assert detect_anomaly(None, baseline)["anomalous"] is False


def test_rolling_window_splits():
    windows = rolling_window(list(range(10)), window=3)
    assert windows[0] == [0, 1, 2]
    assert windows[-1] == [9]
