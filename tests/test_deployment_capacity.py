"""E7: 部署承载评估 —— 容量结论带出处，缺数据明确拒答，历史回测达标。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.app.diagnosis.agent_runtime import (
    DeploymentRequirements,
    assess_deployment_capacity,
    backtest_deployment_assessments,
)


def _req(**overrides) -> DeploymentRequirements:
    base = {
        "replicas": 2,
        "cpu_cores_per_replica": 2,
        "memory_mb_per_replica": 2048,
        "disk_mb_per_replica": 4096,
    }
    base.update(overrides)
    return DeploymentRequirements(**base)


def _inventory(alloc_cpu=8.0, alloc_mem=8192, alloc_disk=16384) -> list[dict]:
    return [{
        "node_id": f"node-{i + 1}",
        "allocatable_cpu_cores": alloc_cpu,
        "allocatable_memory_mb": alloc_mem,
        "allocatable_disk_mb": alloc_disk,
        "schedulable": True,
    } for i in range(3)]


def _evidence(time_start: str | None = None,
              time_end: str | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [{
        "evidence_id": "ev-cap-1",
        "observed_at": now.isoformat(),
        "valid_time": {
            "start": time_start or (now - timedelta(minutes=15)).isoformat(),
            "end": time_end or now.isoformat(),
        },
        "content_projection": {"deployment_inventory": _inventory()},
    }]


def test_ready_verdict_with_provenance():
    assessment = assess_deployment_capacity(
        _req(),
        target_scope={"cluster_id": "prod-a", "service_id": "checkout", "environment": "production"},
        tool_evidence=_evidence(),
    )
    assert assessment.verdict == "ready"
    assert len(assessment.eligible_nodes) == 3
    # 出处字段
    assert assessment.evidence_refs == ["ev-cap-1"]
    assert assessment.time_window["start"]  # 时间窗口来自证据 valid_time
    assert assessment.resource_scope == {
        "cluster_id": "prod-a", "service_id": "checkout", "environment": "production",
    }
    assert assessment.data_freshness > 0.0
    assert assessment.requirements is not None


def test_missing_requirements_refuses_instead_of_guessing():
    assessment = assess_deployment_capacity(
        None,
        target_scope={"cluster_id": "prod-a"},
        tool_evidence=_evidence(),
    )
    assert assessment.verdict == "insufficient_data"
    assert assessment.missing_inputs


def test_missing_inventory_refuses_instead_of_using_utilization():
    # 只有利用率指标，没有可分配容量清单 → 明确拒答
    utilization_only = [{
        "evidence_id": "ev-util-1",
        "observed_at": "2026-08-14T00:00:00Z",
        "valid_time": {},
        "content_projection": {"cpu_percent": 80.0},
    }]
    assessment = assess_deployment_capacity(_req(), target_scope={}, tool_evidence=utilization_only)
    assert assessment.verdict == "insufficient_data"
    assert "不能用瞬时利用率替代容量" in assessment.summary
    assert assessment.requirements is not None


def test_not_ready_when_no_eligible_node():
    assessment = assess_deployment_capacity(
        _req(cpu_cores_per_replica=32),
        target_scope={},
        tool_evidence=_evidence(),
    )
    assert assessment.verdict == "not_ready"
    assert assessment.eligible_nodes == []


def test_conditional_when_partial_capacity():
    inventory = [
        {"node_id": "big", "allocatable_cpu_cores": 16, "allocatable_memory_mb": 16384,
         "allocatable_disk_mb": 32768, "schedulable": True},
        {"node_id": "small", "allocatable_cpu_cores": 1, "allocatable_memory_mb": 512,
         "allocatable_disk_mb": 1024, "schedulable": True},
    ]
    evidence = [{
        "evidence_id": "ev-cap-2",
        "observed_at": "2026-08-14T00:15:00Z",
        "valid_time": {},
        "content_projection": {"deployment_inventory": inventory},
    }]
    assessment = assess_deployment_capacity(_req(replicas=2), target_scope={}, tool_evidence=evidence)
    assert assessment.verdict == "conditional"
    assert assessment.eligible_nodes == ["big"]


# ── 历史回测 ──────────────────────────────────────────────────────────


def test_backtest_refuses_insufficient_data_and_scores_decidable():
    decidable = {
        "requirements": _req().model_dump(mode="json"),
        "target_scope": {},
        "tool_evidence": _evidence(),
        "oracle_verdict": "ready",
    }
    insufficient = {
        "requirements": _req().model_dump(mode="json"),
        "target_scope": {},
        "tool_evidence": [{"evidence_id": "ev-util-2", "observed_at": "2026-08-14T00:00:00Z",
                          "valid_time": {}, "content_projection": {"cpu_percent": 90.0}}],
        "oracle_verdict": "insufficient_data",  # 数据不足 → 评估器必须拒答
    }
    wrong = {
        "requirements": _req().model_dump(mode="json"),
        "target_scope": {},
        "tool_evidence": _evidence(),
        "oracle_verdict": "not_ready",
    }
    report = backtest_deployment_assessments([decidable, insufficient, wrong])
    assert report["total"] == 3
    # insufficient 记录被正确拒答（refused_but_oracle_decided == 0）
    assert report["refused_but_oracle_decided"] == 0
    assert report["correct_refusal_rate"] >= 0.95
    # wrong 记录判定为 ready 但 Oracle 是 not_ready → 计入 mismatch
    assert report["mismatches"] and report["mismatches"][0]["oracle"] == "not_ready"
    assert report["correct"] == 1


def test_backtest_gate_passed():
    records = [{
        "requirements": _req().model_dump(mode="json"),
        "target_scope": {},
        "tool_evidence": _evidence(),
        "oracle_verdict": "ready",
    }] * 20
    report = backtest_deployment_assessments(records)
    assert report["accuracy"] == 1.0
    assert report["gates"]["passed"] is True
