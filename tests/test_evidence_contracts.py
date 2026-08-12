"""EvidenceContract Registry 覆盖性与一致性测试。"""

import json
from pathlib import Path

import pytest

from server.app.diagnosis.evidence_contracts import (
    ALL_MECHANISMS,
    PROBE_FACTS,
    SYMPTOM_MECHANISMS,
    contract_satisfied,
    contracts_for_hypothesis,
    contracts_for_symptom,
    get_contract,
    list_contracts,
    missing_facts,
    probe_supplies_facts,
)


def _case_ids() -> list[str]:
    root = Path(__file__).resolve().parents[1] / "benchmarks" / "ai_ops_v2" / "public" / "cases.json"
    return [item["case_id"] for item in json.loads(root.read_text(encoding="utf-8"))["cases"]]


def _oracle(case_id: str) -> dict:
    root = Path(__file__).resolve().parents[1] / "benchmarks" / "ai_ops_v2" / "private" / "oracles.json"
    data = json.loads(root.read_text(encoding="utf-8"))
    by_id = {item["case_id"]: item for item in data["cases"]}
    return by_id[case_id]


def test_contracts_are_versioned_and_unique():
    contracts = list_contracts()
    mechanisms = [item.mechanism for item in contracts]
    assert len(mechanisms) == len(set(mechanisms))
    assert all(item.schema_version == "evidence-contract.v1" for item in contracts)
    assert "cpu_saturation" in mechanisms
    assert "runtime_lock_contention" in mechanisms
    assert "downstream_dependency" in mechanisms


def test_get_contract_raises_for_unknown():
    with pytest.raises(ValueError):
        get_contract("does_not_exist")


def test_runtime_lock_contract_requires_runtime_facts_and_runtime_probe():
    contract = get_contract("runtime_lock_contention")
    assert "runtime_type" in contract.required_facts
    assert "blocked_thread_ratio_max" in contract.required_facts
    assert "runtime_thread_snapshot" in contract.candidate_probes


def test_downstream_contract_requires_connectivity_probe():
    contract = get_contract("downstream_dependency")
    assert "connection_refused_count" in contract.required_facts
    assert "endpoint_connectivity_probe" in contract.candidate_probes


def test_latency_symptom_forces_runtime_mechanism():
    contracts = contracts_for_symptom("latency_increase")
    mechanisms = {item.mechanism for item in contracts}
    assert "runtime_lock_contention" in mechanisms
    assert "downstream_dependency" in mechanisms


def test_connection_failure_symptom_includes_downstream_and_network():
    contracts = contracts_for_symptom("connection_failure")
    mechanisms = {item.mechanism for item in contracts}
    assert "downstream_dependency" in mechanisms
    assert "network_degradation" in mechanisms


def test_contracts_for_hypothesis_maps_lock_type():
    contracts = contracts_for_hypothesis("LOCK_CONTENTION")
    assert any(item.mechanism == "runtime_lock_contention" for item in contracts)


def test_missing_facts_and_satisfaction():
    contract = get_contract("cpu_saturation")
    assert missing_facts(contract, {"process_cpu_core_usage": 1.2}) == [
        "avg_cpu_user_pct", "top_function.name",
    ]
    assert not contract_satisfied(contract, {"process_cpu_core_usage": 1.2})
    assert contract_satisfied(contract, {
        "process_cpu_core_usage": 1.2,
        "avg_cpu_user_pct": 80.0,
        "top_function.name": "fib",
    })


def test_probe_facts_cover_contract_required_facts():
    """每个契约的必需事实都能由某个候选探针提供（数据一致性守卫）。"""
    for contract in list_contracts():
        supplied = set().union(*(probe_supplies_facts(probe) for probe in contract.candidate_probes))
        missing = [fact for fact in contract.required_facts if fact not in supplied]
        assert not missing, (
            f"contract={contract.mechanism} 的必需事实无探针提供: {missing}"
        )


def test_symptom_mapping_covers_all_normalized_symptoms():
    symptoms = {
        "latency_increase", "cpu_saturation", "io_degradation", "memory_pressure",
        "noisy_neighbor", "error_increase", "connection_failure",
        "unknown_performance_issue", "runtime_stall", "disk_exhaustion",
        "network_degradation",
    }
    assert set(SYMPTOM_MECHANISMS) >= symptoms


@pytest.mark.parametrize(
    ("case_id", "expected_mechanism"),
    [
        ("OB-SINGLE-CPU-001", "cpu_saturation"),
        ("OB-SINGLE-REDIS-001", "downstream_dependency"),
        ("OB-SINGLE-PAYMENT-001", "downstream_dependency"),
        ("OB-SINGLE-GO-LOCK-001", "runtime_lock_contention"),
        ("OB-SINGLE-PYTHON-LOCK-001", "runtime_lock_contention"),
        ("OB-SINGLE-JAVA-LOCK-001", "runtime_lock_contention"),
        ("OB-SINGLE-RUNTIME-STALL-001", "runtime_stall"),
        ("OB-SINGLE-MEMLEAK-001", "memory_leak"),
        ("OB-SINGLE-OOM-001", "process_oom"),
        ("OB-SINGLE-DISK-001", "filesystem_exhaustion"),
        ("OB-SINGLE-NETLOSS-001", "network_degradation"),
        ("OB-SINGLE-NOISY-CPU-001", "same_host_noisy_neighbor"),
    ],
)
def test_contract_covers_key_case_mechanism(case_id, expected_mechanism):
    """关键案例的根因机制必须存在于契约中，否则自适应规划无从兜底。"""
    assert case_id in _case_ids(), f"{case_id} 不在 ai_ops_v2 数据集"
    assert expected_mechanism in ALL_MECHANISMS


def test_redis_and_payment_oracles_are_downstream_entities():
    """Payment/Redis 是 root_entity 修复目标，契约必须支持其下游定位。"""
    for case_id in ("OB-SINGLE-REDIS-001", "OB-SINGLE-PAYMENT-001"):
        oracle = _oracle(case_id)
        assert oracle["expected"]["location_type"] == "downstream"
        assert oracle["expected"]["root_entity"]
