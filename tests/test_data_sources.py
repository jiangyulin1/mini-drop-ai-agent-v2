"""P5 数据源连接器测试：Prometheus 指标 + 日志模板突变检测。"""

from __future__ import annotations

import json

import pytest

from server.app.diagnosis.data_sources import LogTemplateConnector, PrometheusConnector
from server.app.diagnosis.source_gateway import SourceGatewayError, SourceQueryRequest


def _request(operation: str, resource: dict | None = None, parameters: dict | None = None) -> SourceQueryRequest:
    return SourceQueryRequest(
        tenant_id="tenant-a",
        operation=operation,
        resource=resource or {},
        parameters=parameters or {},
        requested_time_range_minutes=15,
    )


def test_prometheus_connector_queries_range():
    def fake_get(endpoint, params):
        assert "/api/v1/query_range" in endpoint
        assert params["query"] == "up"
        body = json.dumps({"data": {"result": [
            {"metric": {"__name__": "up"}, "values": [["1", "1"], ["2", "0"]]},
        ]}})
        return 200, body

    connector = PrometheusConnector(http_get=fake_get, endpoint="http://prom.example")
    result = connector.execute(_request("metrics.query_range", parameters={"metric": "up"}))
    assert result["available"] is True
    assert result["series_count"] == 1
    assert result["samples"][0]["values"][0]["value"] == "1"


def test_prometheus_connector_unavailable_returns_graceful():
    def failing_get(endpoint, params):
        raise OSError("connection refused")

    connector = PrometheusConnector(http_get=failing_get, endpoint="http://prom.example")
    result = connector.execute(_request("metrics.query_range", parameters={"metric": "up"}))
    assert result["available"] is False
    assert result["samples"] == []


def test_prometheus_requires_metric():
    connector = PrometheusConnector(endpoint="http://prom.example")
    with pytest.raises(SourceGatewayError):
        connector.execute(_request("metrics.query_range"))


class _Repo:
    def __init__(self):
        self.tasks = {}
        self.artifacts = {}

    def register_log_task(self, task_id: str, agent_id: str, pid: int, lines: list[str]):
        self.tasks[task_id] = type("T", (), {
            "id": task_id, "agent_id": agent_id, "target_pid": pid,
            "collector_type": "log_scan",
        })()
        self.artifacts[task_id] = [{
            "artifact_type": "log_scan",
            "metadata": {"data": {
                "log_files": [{
                    "error_lines": [{"text": line} for line in lines],
                }],
            }},
        }]


def test_log_template_detects_mutation():
    repo = _Repo()
    # 基线窗口：1 条连接失败
    repo.register_log_task("t1", "a1", 100, ["normal", "connection refused"])
    # 最近窗口：4 条连接失败
    for i in range(4):
        repo.register_log_task(f"t2_{i}", "a1", 100, [f"connection refused {i}", "normal"])
    connector = LogTemplateConnector(repo)
    result = connector.execute(_request(
        "log.query",
        resource={"agent_id": "a1", "pid": "100"},
        parameters={"template": "connection_failure"},
    ))
    assert result["recent_count"] >= 4
    assert result["baseline_count"] >= 1
    assert result["mutation"]["mutation"] is True


def test_log_template_unknown_template_rejected():
    connector = LogTemplateConnector(_Repo())
    with pytest.raises(SourceGatewayError):
        connector.execute(_request(
            "log.query",
            resource={"agent_id": "a1", "pid": "100"},
            parameters={"template": "bogus"},
        ))


def test_log_template_requires_target():
    connector = LogTemplateConnector(_Repo())
    with pytest.raises(SourceGatewayError):
        connector.execute(_request("log.query", parameters={"template": "timeout"}))
