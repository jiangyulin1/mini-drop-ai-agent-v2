"""EvidenceContract Registry：故障机制 → 必需事实 → 候选探针 → 确认策略。

一份契约声明"要确认（或排除）某根因机制，需要哪些事实、用哪些受控探针采集、
至少满足什么独立来源/时间窗条件"。诊断规划不再由一个 symptom 字符串静态决定，
而是按活跃候选的契约缺失事实生成下一步采集动作。

契约中的 ``required_facts`` 引用扁平 ``facts`` dict 的键（orchestrator 的
``_normalized_facts`` 输出）。``PROBE_FACTS`` 描述每个探针能补足哪些事实，
供 Adaptive Planner 计算信息增益。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel


class EvidenceContract(StrictModel):
    """一个根因机制的证据合同。"""

    schema_version: Literal["evidence-contract.v1"] = "evidence-contract.v1"
    mechanism: str = Field(min_length=1, max_length=128)
    applicability: list[str] = Field(
        default_factory=lambda: ["all"],
        max_length=16,
        description="适用运行时可空集；非空时表示只适用于 java/go/python 等运行时",
    )
    hypothesis_types: list[str] = Field(
        default_factory=list, max_length=16,
        description="与 orchestrator._candidate_matches_hypothesis 对应的假设类型",
    )
    required_facts: list[str] = Field(
        default_factory=list, max_length=32,
        description="确认该机制所需的扁平事实键",
    )
    candidate_probes: list[str] = Field(
        default_factory=list, max_length=16,
        description="能补足上述事实的受控探针 probe_id",
    )
    confirmation_policy: dict[str, Any] = Field(
        default_factory=lambda: {
            "min_independent_source_families": 2,
            "min_incident_windows": 1,
        },
        description="允许确认的最低独立证据来源族数与事故窗数",
    )


# 每个受控探针能提供的扁平事实键。键必须与 required_facts 一一对应，
# 供 planner 计算"该探针可补足的缺失事实占比"。
PROBE_FACTS: dict[str, set[str]] = {
    "host_process_metrics": {
        "process_cpu_core_usage", "avg_cpu_user_pct", "avg_cpu_sys_pct",
        "avg_cpu_iowait_pct", "load1m", "host_core_count",
        "vmrss_mb", "vmrss_slope_bytes_per_second", "vmrss_trend",
        "container_memory_usage_ratio", "container_oom_kill_delta",
        "thread_count", "fd_count",
        "tcp_retransmit_pct", "tcp_timeout_delta",
        "target_fs_used_pct", "root_fs_used_pct",
    },
    "process_memory_map": {
        "vmrss_mb", "vmrss_slope_bytes_per_second", "vmrss_trend",
        "container_memory_usage_ratio",
    },
    "process_log_scan": {
        "log_error_count",
        "connection_refused_count", "connection_reset_count", "timeout_count",
        "out_of_memory_count", "enospc_count", "deadlock_count", "lock_timeout_count",
    },
    "runtime_thread_snapshot": {
        "runtime_type", "blocked_thread_ratio_max", "lock_waiter_count_max",
        "uninterruptible_thread_count_max",
    },
    "process_cpu_profile": {"process_cpu_core_usage", "top_function.name"},
    "process_io_latency": {"avg_cpu_iowait_pct", "block_latency_high"},
    "endpoint_connectivity_probe": {
        "endpoint.reachable", "endpoint.container_state",
        "endpoint.connect_latency_ms", "endpoint.http_status",
    },
}


_CONTRACTS: list[EvidenceContract] = [
    EvidenceContract(
        mechanism="cpu_saturation",
        hypothesis_types=["CPU_SATURATION", "SELF_CODE_REGRESSION"],
        required_facts=[
            "process_cpu_core_usage", "avg_cpu_user_pct", "top_function.name",
        ],
        candidate_probes=["host_process_metrics", "process_cpu_profile"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 2,
        },
    ),
    EvidenceContract(
        mechanism="runtime_lock_contention",
        applicability=["java", "go", "python"],
        hypothesis_types=["LOCK_CONTENTION"],
        required_facts=[
            "runtime_type", "blocked_thread_ratio_max", "lock_waiter_count_max",
        ],
        candidate_probes=["runtime_thread_snapshot", "process_cpu_profile"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 2,
        },
    ),
    EvidenceContract(
        mechanism="runtime_stall",
        applicability=["java", "go", "python"],
        hypothesis_types=["RUNTIME_STALL"],
        required_facts=[
            "runtime_type", "uninterruptible_thread_count_max",
        ],
        candidate_probes=["runtime_thread_snapshot"],
        confirmation_policy={
            "min_independent_source_families": 1,
            "min_incident_windows": 2,
        },
    ),
    EvidenceContract(
        mechanism="memory_leak",
        hypothesis_types=["MEMORY_LEAK", "HOST_MEMORY_PRESSURE"],
        required_facts=[
            "vmrss_slope_bytes_per_second", "vmrss_trend", "container_memory_usage_ratio",
        ],
        candidate_probes=["host_process_metrics", "process_memory_map"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 3,
        },
    ),
    EvidenceContract(
        mechanism="process_oom",
        hypothesis_types=["HOST_MEMORY_PRESSURE"],
        required_facts=[
            "container_oom_kill_delta", "container_memory_usage_ratio",
        ],
        candidate_probes=["host_process_metrics", "process_log_scan"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 1,
        },
    ),
    EvidenceContract(
        mechanism="filesystem_exhaustion",
        hypothesis_types=["FILESYSTEM_EXHAUSTION", "LOG_WRITE_AMPLIFICATION"],
        required_facts=[
            "target_fs_used_pct", "root_fs_used_pct", "enospc_count",
        ],
        candidate_probes=["host_process_metrics", "process_log_scan"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 1,
        },
    ),
    EvidenceContract(
        mechanism="network_degradation",
        hypothesis_types=["NETWORK_DEGRADATION", "DOWNSTREAM_LATENCY"],
        required_facts=[
            "tcp_retransmit_pct", "tcp_timeout_delta",
            "endpoint.reachable", "endpoint.container_state",
        ],
        candidate_probes=["host_process_metrics", "endpoint_connectivity_probe", "process_log_scan"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 2,
        },
    ),
    EvidenceContract(
        mechanism="downstream_dependency",
        hypothesis_types=["DOWNSTREAM_LATENCY", "SHARED_DEPENDENCY_FAILURE"],
        required_facts=[
            "connection_refused_count", "connection_reset_count", "timeout_count",
            "endpoint.reachable", "endpoint.container_state",
        ],
        candidate_probes=["process_log_scan", "endpoint_connectivity_probe"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 1,
        },
    ),
    EvidenceContract(
        mechanism="same_host_noisy_neighbor",
        hypothesis_types=["SAME_HOST_NOISY_NEIGHBOR", "HOST_DISK_CONTENTION"],
        required_facts=[
            "avg_cpu_iowait_pct", "host_core_count", "process_cpu_core_usage",
        ],
        candidate_probes=["host_process_metrics", "process_io_latency"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 1,
        },
    ),
    EvidenceContract(
        mechanism="host_disk_contention",
        hypothesis_types=["HOST_DISK_CONTENTION", "SAME_HOST_NOISY_NEIGHBOR"],
        required_facts=["avg_cpu_iowait_pct", "block_latency_high"],
        candidate_probes=["process_io_latency", "host_process_metrics"],
        confirmation_policy={
            "min_independent_source_families": 2,
            "min_incident_windows": 2,
        },
    ),
]

_CONTRACTS_BY_MECHANISM: dict[str, EvidenceContract] = {
    item.mechanism: item for item in _CONTRACTS
}

# symptom → 初始机制种子（首轮规划用）。latency_increase 不能只看 CPU：
# 运行时锁 / 下游停顿是当前测试最常漏采的两类。
SYMPTOM_MECHANISMS: dict[str, list[str]] = {
    "cpu_saturation": ["cpu_saturation"],
    "latency_increase": ["runtime_lock_contention", "downstream_dependency", "cpu_saturation"],
    "io_degradation": ["host_disk_contention", "filesystem_exhaustion"],
    "memory_pressure": ["memory_leak", "process_oom"],
    "noisy_neighbor": ["same_host_noisy_neighbor", "host_disk_contention"],
    "error_increase": [
        "downstream_dependency", "cpu_saturation",
        # 报错/超时同样可能由运行时锁/停顿（GIL/futex/monitor）触发。
        "runtime_lock_contention", "runtime_stall",
    ],
    "connection_failure": ["downstream_dependency", "network_degradation"],
    "runtime_stall": ["runtime_stall", "runtime_lock_contention"],
    "disk_exhaustion": ["filesystem_exhaustion"],
    "network_degradation": ["network_degradation", "downstream_dependency"],
    "unknown_performance_issue": [
        # 语义不明的性能问题必须保留运行时契约：GO-LOCK / RUNTIME-STALL 案例
        # 都从该症状入口进入，缺运行时契约会导致永远采不到 runtime_snapshot。
        "cpu_saturation", "memory_leak", "downstream_dependency",
        "runtime_lock_contention", "runtime_stall",
    ],
}

ALL_MECHANISMS: list[str] = [item.mechanism for item in _CONTRACTS]


def list_contracts() -> list[EvidenceContract]:
    return list(_CONTRACTS)


def get_contract(mechanism: str) -> EvidenceContract:
    try:
        return _CONTRACTS_BY_MECHANISM[mechanism]
    except KeyError as exc:
        raise ValueError(f"未注册的证据契约: {mechanism}") from exc


def contracts_for_symptom(symptom: str) -> list[EvidenceContract]:
    mechanisms = SYMPTOM_MECHANISMS.get(symptom)
    if not mechanisms:
        mechanisms = ALL_MECHANISMS
    return [get_contract(mechanism) for mechanism in mechanisms]


def contracts_for_hypothesis(hypothesis_type: str) -> list[EvidenceContract]:
    return [
        item for item in _CONTRACTS
        if hypothesis_type in item.hypothesis_types
    ]


def probe_supplies_facts(probe_id: str) -> set[str]:
    return PROBE_FACTS.get(probe_id, set())


def missing_facts(contract: EvidenceContract, facts: dict[str, Any]) -> list[str]:
    """契约中当前证据尚未提供的必需事实键。facts 为扁平 dict，直接按键判断。"""
    return [fact for fact in contract.required_facts if fact not in facts]


def contract_satisfied(contract: EvidenceContract, facts: dict[str, Any]) -> bool:
    return not missing_facts(contract, facts)
