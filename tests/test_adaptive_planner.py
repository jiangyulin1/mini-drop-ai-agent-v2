"""Adaptive Planner 测试：缺失事实→探针选择、下游连通、R2 门控与低增益停止。"""

from server.app.diagnosis.adaptive_planner import (
    build_probe_candidates,
    select_probe_actions,
    uncovered_mechanisms,
)
from server.app.diagnosis.probe_registry import list_probes


def _scope(target_service="checkoutservice"):
    return {
        "target_service": target_service,
        "target_anchor": {"instance_id": "checkoutservice-1", "service_id": target_service},
        "instances": [
            {"instance_id": "checkoutservice-1", "service_id": target_service, "host_id": "h2"},
            {"instance_id": "paymentservice-1", "service_id": "paymentservice", "host_id": "h2"},
        ],
        "dependencies": [
            {"source_service": "checkoutservice", "target_service": "paymentservice", "relation": "CALLS"},
        ],
        "downstream_service_ids": ["paymentservice"],
    }


def _hypotheses(types, status="UNTESTED"):
    return [
        {
            "hypothesis_id": f"hyp_{index}_{kind.lower()}",
            "type": kind,
            "status": status,
            "affected_targets": ["checkoutservice-1"],
        }
        for index, kind in enumerate(types)
    ]


def _r1_probes():
    return [item for item in list_probes() if item.risk_level == "R1"]


def test_runtime_symptom_with_no_runtime_facts_selects_runtime_snapshot():
    # 运行时契约是唯一相关契约时，唯一可补足缺失事实的 R1 探针必须被选中。
    candidates = build_probe_candidates(
        symptom="latency_increase",
        hypotheses=_hypotheses(["LOCK_CONTENTION", "RUNTIME_STALL"]),
        observations=[],  # 尚未采集任何运行时事实
        scope=_scope(),
        available_probes=_r1_probes(),
        targets=_scope()["instances"],
        round_number=1,
    )
    actions = select_probe_actions(candidates, max_actions=2)
    assert actions, "运行时症状在无 runtime 事实时必须能生成探针候选"
    probe_ids = [item["source_id"] for item in actions]
    assert "runtime_thread_snapshot" in probe_ids, f"实际选中: {probe_ids}"


def test_runtime_probe_is_considered_for_mixed_hypotheses():
    # 混合假设下 runtime_thread_snapshot 必须出现在候选集（即使被更便宜的探针排后）。
    candidates = build_probe_candidates(
        symptom="latency_increase",
        hypotheses=_hypotheses(["LOCK_CONTENTION", "DOWNSTREAM_LATENCY", "CPU_SATURATION"]),
        observations=[],
        scope=_scope(),
        available_probes=_r1_probes(),
        targets=_scope()["instances"],
        round_number=1,
    )
    assert any(
        candidate.action_id == "probe:runtime_thread_snapshot"
        and candidate.expected_information_gain == 1.0
        for candidate in candidates
    )


def test_connection_failure_symptom_selects_connection_probe_and_log():
    candidates = build_probe_candidates(
        symptom="connection_failure",
        hypotheses=_hypotheses(["DOWNSTREAM_LATENCY", "NETWORK_DEGRADATION"]),
        observations=[],
        scope=_scope(),
        available_probes=_r1_probes(),
        targets=_scope()["instances"],
        round_number=1,
        connection_endpoints=[{"service": "paymentservice", "address": "paymentservice:50051"}],
    )
    actions = select_probe_actions(candidates, max_actions=3)
    probe_ids = {item["source_id"] for item in actions}
    assert "endpoint_connectivity_probe" in probe_ids, f"实际选中: {probe_ids}"
    connection = next(item for item in actions if item["source_id"] == "endpoint_connectivity_probe")
    assert connection["parameters"]["endpoints"][0]["service"] == "paymentservice"
    assert "endpoint.reachable" in connection["parameters"]["missing_facts"]
    assert "endpoint.container_state" in connection["parameters"]["missing_facts"]


def test_runtime_facts_present_suppress_runtime_probe():
    candidates = build_probe_candidates(
        symptom="runtime_stall",
        hypotheses=_hypotheses(["RUNTIME_STALL", "LOCK_CONTENTION"]),
        observations=[{
            "target": {"instance_id": "checkoutservice-1"},
            "facts": {
                "runtime_type": "go",
                "blocked_thread_ratio_max": 0.5,
                "lock_waiter_count_max": 4,
                "uninterruptible_thread_count_max": 1,
            },
            "pressure": {},
        }],
        scope=_scope(),
        available_probes=_r1_probes(),
        targets=_scope()["instances"],
        round_number=2,
    )
    # 运行时事实已齐：不应再为 runtime_lock/stall 选 runtime 探针（可能仍为其他契约补证）。
    selected = select_probe_actions(candidates, max_actions=3)
    if selected:
        runtime_selected = [
            item for item in selected if item["source_id"] == "runtime_thread_snapshot"
        ]
        assert not runtime_selected, "已满足 runtime 契约时不应再选 runtime 探针"


def test_uncovered_mechanisms_reports_runtime_mechanism():
    uncovered = uncovered_mechanisms(
        "latency_increase",
        _hypotheses(["LOCK_CONTENTION", "DOWNSTREAM_LATENCY"]),
        observations=[],  # 无事实 → runtime_lock 未覆盖
    )
    assert "runtime_lock_contention" in uncovered


def test_r2_not_selected_without_grant():
    # 只给 R2 探针时，不允许生成候选（allow_r2=False 默认）。
    r2_only = [item for item in list_probes() if item.risk_level == "R2"]
    candidates = build_probe_candidates(
        symptom="cpu_saturation",
        hypotheses=_hypotheses(["CPU_SATURATION"]),
        observations=[],
        scope=_scope(),
        available_probes=r2_only,
        targets=_scope()["instances"],
        round_number=1,
    )
    assert candidates == []


def test_fully_satisfied_contracts_generate_no_candidates():
    candidates = build_probe_candidates(
        symptom="cpu_saturation",
        hypotheses=_hypotheses(["CPU_SATURATION"]),
        observations=[{
            "target": {"instance_id": "checkoutservice-1"},
            "facts": {
                "process_cpu_core_usage": 3.5,
                "avg_cpu_user_pct": 90.0,
                "top_function.name": "fib",
            },
            "pressure": {"cpu": True},
        }],
        scope=_scope(),
        available_probes=_r1_probes(),
        targets=_scope()["instances"],
        round_number=1,
    )
    assert candidates == []


def test_paired_evidence_fork_changes_registered_action_class_and_aa_is_stable():
    hypotheses = _hypotheses(["CPU_SATURATION", "LOCK_CONTENTION"])
    base = {
        "symptom": "latency_increase",
        "hypotheses": hypotheses,
        "scope": _scope(),
        "available_probes": _r1_probes(),
        "targets": _scope()["instances"],
        "round_number": 2,
    }

    def selected(facts):
        candidates = build_probe_candidates(
            **base,
            observations=[{
                "target": _scope()["instances"][0],
                "facts": facts,
                "pressure": {},
            }],
        )
        return [item["source_id"] for item in select_probe_actions(candidates, max_actions=2)]

    runtime_already_observed = {
        "runtime_type": "go",
        "blocked_thread_ratio_max": 0.5,
        "lock_waiter_count_max": 4,
        "uninterruptible_thread_count_max": 1,
    }
    cpu_already_observed = {
        "process_cpu_core_usage": 3.5,
        "avg_cpu_user_pct": 90.0,
        "top_function.name": "fib",
    }
    first_a = selected(runtime_already_observed)
    first_b = selected(runtime_already_observed)
    paired = selected(cpu_already_observed)
    assert first_a == first_b  # A/A control: no random drift
    assert first_a == ["host_process_metrics"]
    assert paired == ["runtime_thread_snapshot"]
