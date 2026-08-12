"""受控连接探针：Agent 采集器纯逻辑 + Server 域分析与集群归因测试。"""

import json
import socket
from pathlib import Path

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.connection_probe import ConnectionProbeCollector
from server.app.diagnosis.domain_analyzers import analyze_observations, assess_cluster
from server.app.diagnosis.orchestrator import STRUCTURED_ARTIFACT_TYPES, _normalized_facts


def _listener() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    return srv, srv.getsockname()[1]


def test_tcp_check_reports_reachable_and_refused():
    srv, port = _listener()
    try:
        reachable, latency, error = ConnectionProbeCollector._tcp_check("127.0.0.1", port)
        assert reachable is True
        assert error is None
        assert latency is not None and latency >= 0
        refused, _, _ = ConnectionProbeCollector._tcp_check("127.0.0.1", port + 2000)
        assert refused is False
    finally:
        srv.close()


def test_collect_produces_endpoint_facts(tmp_path):
    srv, port = _listener()
    collector = ConnectionProbeCollector()
    collector.OUTPUT_BASE = str(tmp_path)
    try:
        task = CollectorTask(
            id="probe-1", collector_type="connection_probe",
            target_pid=1, sample_rate=1, duration_sec=2,
            options={"endpoints": [
                {"service": "paymentservice", "host": "127.0.0.1", "port": port},
            ]},
        )
        result = collector.collect(task)
        assert result.ok
        assert result.artifacts[0]["artifact_type"] == "connection_probe"
        data = json.loads(
            (Path(tmp_path) / "probe-1" / "connection_probe.json").read_text(encoding="utf-8")
        )
        assert data["summary"]["endpoint.reachable"] is True
        assert data["summary"]["endpoint.unreachable_count"] == 0
        assert data["endpoints"][0]["service"] == "paymentservice"
        assert data["endpoints"][0]["reachable"] is True
    finally:
        srv.close()


def test_summarize_marks_unreachable_when_any_endpoint_fails():
    endpoints = [
        {"service": "a", "reachable": True, "container_state": "running"},
        {"service": "b", "reachable": False, "container_state": "running"},
    ]
    summary = ConnectionProbeCollector._summarize(endpoints)
    assert summary["endpoint.reachable"] is False
    assert summary["endpoint.unreachable_count"] == 1
    assert summary["endpoint.downstream_service"] == "a,b"


def test_summarize_worst_container_state_wins():
    endpoints = [
        {"service": "a", "reachable": True, "container_state": "running"},
        {"service": "b", "reachable": True, "container_state": "paused"},
    ]
    summary = ConnectionProbeCollector._summarize(endpoints)
    assert summary["endpoint.container_state"] == "paused"


def _observation(service: str, instance_id: str, facts: dict) -> dict:
    return {
        "task_id": f"task-{instance_id}",
        "collector_type": "connection_probe",
        "observed_at": "2026-08-11T00:00:00Z",
        "duration_sec": 5,
        "target": {
            "service_id": service, "instance_id": instance_id,
            "host_id": "host-1", "agent_id": "a1", "pid": 1234,
        },
        "collection_status": "DONE",
        "status_reason": "",
        "failure_kind": None,
        "summary": {},
        "facts": facts,
        "fact_domains": {},
        "top_function": {},
        "pressure": {},
        "log": None,
        "evidence_refs": [f"ev_{instance_id}"],
    }


def test_analyze_observations_emits_endpoint_unreachable():
    obs = _observation("checkoutservice", "checkout-1", {
        "endpoint.reachable": False,
        "endpoint.unreachable_count": 1,
        "endpoint.downstream_service": "paymentservice",
    })
    findings = analyze_observations([obs])
    types = {item["finding_type"] for item in findings}
    assert "endpoint_unreachable" in types


def test_connection_probe_is_a_structured_artifact_and_normalizes_facts():
    """connection_probe 必须是结构化产物，其 summary 进入扁平 facts 供 EvidenceContract 使用。"""
    assert "connection_probe" in STRUCTURED_ARTIFACT_TYPES
    artifact = {
        "schema_version": "connection_probe.v1",
        "summary": {
            "endpoint.reachable": False,
            "endpoint.unreachable_count": 1,
            "endpoint.downstream_service": "redis-cart",
            "endpoint.container_state": "paused",
        },
    }
    facts = _normalized_facts({"connection_probe": artifact}, {})
    assert facts.get("endpoint.reachable") is False
    assert facts.get("endpoint.downstream_service") == "redis-cart"
    assert facts.get("endpoint.container_state") == "paused"


def test_assess_cluster_classifies_downstream_from_endpoint_probe():
    scope = {
        "target_service": "checkoutservice",
        "same_host_instance_ids": [],
        "downstream_service_ids": ["paymentservice"],
        "instances": [
            {"instance_id": "checkout-1", "service_id": "checkoutservice", "host_id": "host-1"},
        ],
    }
    obs = _observation("checkoutservice", "checkout-1", {
        "endpoint.reachable": False,
        "endpoint.unreachable_count": 1,
        "endpoint.downstream_service": "paymentservice",
    })
    assessment = assess_cluster(scope, [obs])
    assert assessment["classification"] == "downstream_dependency"
    assert assessment["root_location"]["type"] == "downstream"
