"""固定探针注册表：模型只能选择这里声明的能力。"""

from __future__ import annotations

from typing import Any

from server.app.diagnosis.schemas import ProbeDefinition
from server.app.task_kinds import TASK_KINDS


_PROBES = {
    "host_process_metrics": ProbeDefinition(
        probe_id="host_process_metrics",
        name="主机与进程系统指标",
        purpose="低开销确认 CPU、内存、线程、FD、网络和 I/O 等待趋势",
        runner_task_kind="sys_metrics",
        supported_platforms=["linux"],
        required_capabilities=["sys_metrics"],
        risk_level="R1",
        requires_approval=False,
        default_duration_seconds=15,
        max_duration_seconds=30,
        default_sample_rate=11,
        estimated_overhead={"cpu_percent": "<2", "disk_mb": "<10"},
        applicable_hypotheses=[
            "CPU_SATURATION", "HOST_MEMORY_PRESSURE", "HOST_DISK_CONTENTION",
            "SAME_HOST_NOISY_NEIGHBOR", "NETWORK_DEGRADATION",
        ],
    ),
    "process_cpu_profile": ProbeDefinition(
        probe_id="process_cpu_profile",
        name="进程 CPU Profile",
        purpose="识别 CPU 热点、锁竞争和异常调用栈",
        runner_task_kind="perf_cpu",
        supported_platforms=["linux"],
        required_capabilities=["perf_cpu"],
        risk_level="R2",
        requires_approval=True,
        default_duration_seconds=15,
        max_duration_seconds=60,
        default_sample_rate=49,
        estimated_overhead={"cpu_percent": "2-8", "disk_mb": "20-200"},
        applicable_hypotheses=["SELF_CODE_REGRESSION", "CPU_SATURATION", "LOCK_CONTENTION"],
    ),
    "process_io_latency": ProbeDefinition(
        probe_id="process_io_latency",
        name="块设备 I/O 延迟",
        purpose="确认宿主机块设备延迟和 I/O 争抢",
        runner_task_kind="ebpf_io",
        supported_platforms=["linux"],
        required_capabilities=["ebpf_io"],
        risk_level="R2",
        requires_approval=True,
        default_duration_seconds=15,
        max_duration_seconds=60,
        default_sample_rate=11,
        estimated_overhead={"cpu_percent": "1-5", "disk_mb": "<50"},
        applicable_hypotheses=["HOST_DISK_CONTENTION", "SAME_HOST_NOISY_NEIGHBOR"],
    ),
    "process_memory_map": ProbeDefinition(
        probe_id="process_memory_map",
        name="进程内存映射摘要",
        purpose="确认 RSS/PSS/Swap 趋势和内存压力",
        runner_task_kind="memory_smaps",
        supported_platforms=["linux"],
        required_capabilities=["memory_smaps"],
        risk_level="R1",
        requires_approval=False,
        default_duration_seconds=15,
        max_duration_seconds=30,
        default_sample_rate=11,
        estimated_overhead={"cpu_percent": "<2", "disk_mb": "<20"},
        applicable_hypotheses=["HOST_MEMORY_PRESSURE", "MEMORY_LEAK"],
    ),
    "process_log_scan": ProbeDefinition(
        probe_id="process_log_scan",
        name="进程日志扫描",
        purpose="从进程日志尾部提取错误模式与时间戳，定位报错/超时/连接类根因",
        runner_task_kind="log_scan",
        supported_platforms=["linux"],
        required_capabilities=["log_scan"],
        risk_level="R1",
        requires_approval=False,
        default_duration_seconds=2,
        max_duration_seconds=5,
        default_sample_rate=1,
        estimated_overhead={"cpu_percent": "<1", "disk_mb": "<1"},
        applicable_hypotheses=[
            "SELF_CODE_REGRESSION", "DOWNSTREAM_LATENCY", "NETWORK_DEGRADATION",
            "SHARED_DEPENDENCY_FAILURE", "CONNECTION_POOL_EXHAUSTION",
        ],
    ),
}


def get_probe(probe_id: str) -> ProbeDefinition:
    try:
        return _PROBES[probe_id]
    except KeyError as exc:
        raise ValueError(f"未注册探针: {probe_id}") from exc


def list_probes() -> list[ProbeDefinition]:
    return list(_PROBES.values())


def choose_probe_ids(symptom: str) -> list[str]:
    """确定性策略先查低风险指标，再选择一个可区分假设的深度探针。"""
    mapping = {
        "cpu_saturation": ["host_process_metrics", "process_cpu_profile"],
        "latency_increase": ["host_process_metrics", "process_cpu_profile"],
        "io_degradation": ["host_process_metrics", "process_io_latency"],
        "noisy_neighbor": ["host_process_metrics", "process_io_latency"],
        "memory_pressure": ["process_memory_map", "host_process_metrics"],
        "error_increase": ["process_log_scan", "host_process_metrics"],
        "connection_failure": ["process_log_scan", "host_process_metrics"],
    }
    return mapping.get(symptom, ["host_process_metrics", "process_log_scan", "process_cpu_profile"])


# 已注册 TaskKind 采集器白名单（模型只能在白名单内提案候选外采集，永不自创命令）。
# 见 docs/ai_diagnosis_agent_design.md §5.2 候选缺失兜底。
COLLECTOR_WHITELIST = [item["key"] for item in TASK_KINDS]

# 证据域 → 默认候选探针；映射为空即"候选缺失"，进入兜底路径。
_DOMAIN_PROBE_MAP = {
    "host": ["host_process_metrics"],
    "process": ["process_cpu_profile"],
    "container": ["host_process_metrics"],
    "dependency": ["process_log_scan"],
    "database": [],        # 无注册探针 → 候选缺失
    "runtime": ["process_memory_map"],
    "network": [],         # 无注册探针 → 候选缺失
}

# 兜底时按证据域倾向选择的白名单采集器（确定性，避免模型自由发散）。
_DOMAIN_COLLECTOR_HINT = {
    "database": "log_scan",      # 白名单内最接近（正式 mysql_lock 探针待注册后替换）
    "network": "sys_metrics",    # 白名单内最接近（正式 tcp_retransmit 探针待注册后替换）
}


def fallback_candidates_for_gap(
    missing_domains: list[str],
    *,
    existing_collectors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """候选缺失兜底：无注册探针覆盖的证据域 → 从白名单提案候选外采集器。

    每条兜底候选强制：
    - ``candidate_gap=True``（前端标"兜底路径"）；
    - ``requires_approval=True`` + ``approval_policy="single_execution"``（始终 USER_APPROVAL）；
    - 采集器来自 ``COLLECTOR_WHITELIST``，绝不越出白名单。
    """
    existing = set(existing_collectors or [])
    proposals: list[dict[str, Any]] = []
    for domain in missing_domains:
        if _DOMAIN_PROBE_MAP.get(domain):
            continue  # 有默认候选，不走兜底
        hint = _DOMAIN_COLLECTOR_HINT.get(domain)
        # 没有经过设计映射的证据域不能拿任意采集器“凑数”；这类缺口应明确
        # 暴露给用户，等待注册真正有区分度的探针。
        candidate = hint if hint and hint not in existing else None
        if candidate is None:
            continue  # 白名单已全部用过，不再发散
        existing.add(candidate)
        proposals.append({
            "collector_type": candidate,
            "evidence_domain": domain,
            "candidate_gap": True,
            "requires_approval": True,
            "approval_policy": "single_execution",
        })
    return proposals
