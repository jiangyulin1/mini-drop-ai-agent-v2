"""只基于结构化观测做判断的确定性领域分析器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server.app.diagnosis.schemas import DomainFinding


ANALYZER_CONTRACTS = {
    "os_cpu_analyzer.v2": {"required_facts": ["host.cpu"], "optional_facts": ["process.cpu", "profile.topn"], "minimum_quality": "medium", "scope": "host|process"},
    "io_wait_analyzer.v2": {"required_facts": ["host.cpu.iowait_ratio|io.block_latency_high"], "optional_facts": ["process.io"], "minimum_quality": "medium", "scope": "host|process"},
    "memory_pressure_analyzer.v2": {"required_facts": ["process.memory"], "optional_facts": ["container.memory"], "minimum_quality": "medium", "scope": "process|container"},
    "network_latency_analyzer.v1": {"required_facts": ["host.network"], "optional_facts": ["dependency.peer"], "minimum_quality": "medium", "scope": "host|dependency"},
    "mysql_lock_analyzer.v1": {"required_facts": ["dependency.mysql_lock_wait"], "optional_facts": ["dependency.blocking_session"], "minimum_quality": "medium", "scope": "dependency"},
    "jvm_gc_analyzer.v1": {"required_facts": ["runtime.jvm_gc"], "optional_facts": ["process.memory"], "minimum_quality": "medium", "scope": "process"},
}


def analyze_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[DomainFinding] = []
    analyzers: tuple[Callable[[dict[str, Any]], list[DomainFinding]], ...] = (
        _analyze_cpu,
        _analyze_io,
        _analyze_memory,
        _analyze_network,
        _analyze_mysql,
        _analyze_jvm,
    )
    for observation in observations:
        for analyzer in analyzers:
            findings.extend(analyzer(observation))
    return [item.model_dump(mode="json") for item in findings]


def assess_cluster(scope: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """区分目标自身、同宿主与一跳下游；不使用模型生成事实。"""

    target_service = scope.get("target_service")
    same_host_ids = set(scope.get("same_host_instance_ids", []))
    downstream_services = set(scope.get("downstream_service_ids", []))
    target_obs = [obs for obs in observations if obs["target"].get("service_id") == target_service]
    same_host_obs = [obs for obs in observations if obs["target"].get("instance_id") in same_host_ids]
    downstream_obs = [obs for obs in observations if obs["target"].get("service_id") in downstream_services]
    all_refs = _unique_refs(observations)

    compared_by_instance: dict[tuple[Any, ...], dict[str, Any]] = {}
    for obs in observations:
        target = obs["target"]
        key = (target.get("instance_id"), target.get("agent_id"), target.get("pid"))
        item = compared_by_instance.setdefault(key, {
            "instance_id": target.get("instance_id"),
            "service_id": target.get("service_id"),
            "host_id": target.get("host_id"),
            "agent_id": target.get("agent_id"),
            "pid": target.get("pid"),
            "pressure": {name: False for name in obs.get("pressure", {})},
            "evidence_refs": [],
            "collector_types": [],
            "observation_count": 0,
        })
        for name, flagged in obs.get("pressure", {}).items():
            item["pressure"][name] = item["pressure"].get(name, False) or bool(flagged)
        item["evidence_refs"] = list(dict.fromkeys(item["evidence_refs"] + obs.get("evidence_refs", [])))
        if obs.get("collector_type") not in item["collector_types"]:
            item["collector_types"].append(obs.get("collector_type"))
        item["observation_count"] += 1

    classification = "insufficient_evidence"
    confidence_level = "低"
    summary = "已有证据不足以区分自身代码、同宿主噪声邻居或下游依赖问题。"
    confidence_factors = {"scope_coverage": "low", "source_independence": "low", "discriminating_evidence": "low"}
    ruled_out: list[dict[str, Any]] = []

    target_hot = any(_has_self_hotspot(obs) for obs in target_obs)
    target_pressure = any(_has_pressure(obs) for obs in target_obs)
    neighbor_pressure = any(_has_pressure(obs) for obs in same_host_obs)
    downstream_pressure = any(_has_pressure(obs) for obs in downstream_obs)
    shared_iowait = (
        any(obs.get("pressure", {}).get("io_wait") for obs in target_obs)
        and any(obs.get("pressure", {}).get("io_wait") for obs in same_host_obs)
    )

    if shared_iowait:
        classification = "host_resource_contention"
        confidence_level = "高" if len(all_refs) >= 4 else "中"
        summary = "目标实例和同宿主实例同时表现出 I/O 等待，倾向于宿主机或共享块设备争抢。"
        confidence_factors = {"scope_coverage": "high", "source_independence": "medium", "discriminating_evidence": "high"}
    elif target_obs and same_host_obs and neighbor_pressure and not target_hot and not target_pressure:
        classification = "same_host_noisy_neighbor"
        confidence_level = "高" if target_obs else "中"
        summary = "同宿主其他实例存在明显资源压力，当前更像被噪声邻居或宿主机资源争抢拖累。"
        confidence_factors = {"scope_coverage": "high", "source_independence": "medium", "discriminating_evidence": "high"}
        ruled_out.append({
            "hypothesis": "self_code_regression",
            "reason": "目标实例缺少高占比代码热点，且同宿主实例压力更明显。",
            "evidence_refs": all_refs,
        })
    elif target_obs and downstream_obs and downstream_pressure and not target_hot and not target_pressure:
        classification = "downstream_dependency"
        confidence_level = "中"
        summary = "下游依赖实例出现资源压力，根因节点可能不在最先告警的服务上。"
        confidence_factors = {"scope_coverage": "high", "source_independence": "medium", "discriminating_evidence": "high"}
        ruled_out.append({
            "hypothesis": "same_host_noisy_neighbor",
            "reason": "当前证据更集中在一跳下游，而不是同宿主横向干扰。",
            "evidence_refs": all_refs,
        })
    elif target_hot or target_pressure:
        classification = "self_code_or_process_pressure"
        confidence_level = "中"
        summary = "证据主要集中在目标实例自身，优先检查代码热点、线程竞争或进程资源压力。"
        confidence_factors = {"scope_coverage": "medium", "source_independence": "medium", "discriminating_evidence": "medium"}
        if same_host_obs:
            ruled_out.append({
                "hypothesis": "same_host_noisy_neighbor",
                "reason": "同宿主观测未显示更强资源压力。",
                "evidence_refs": all_refs,
            })

    location_type = {
        "host_resource_contention": "shared_resource",
        "same_host_noisy_neighbor": "same_host",
        "downstream_dependency": "downstream",
        "self_code_or_process_pressure": "self",
    }.get(classification, "unknown")
    selected = {
        "same_host": same_host_obs,
        "downstream": downstream_obs,
        "self": target_obs,
        "shared_resource": target_obs + same_host_obs,
    }.get(location_type, [])
    selected_refs = _unique_refs(selected)
    target_ref = None
    if selected:
        target_ref = selected[0].get("target", {}).get("instance_id") or selected[0].get("target", {}).get("host_id")
    domain_type, subtype = _domain_cause(selected)
    legacy_confidence = {"不可判断": 0.0, "低": 0.3, "中": 0.65, "高": 0.82}[confidence_level]
    return {
        "classification": classification,
        "confidence": legacy_confidence,
        "confidence_level": confidence_level,
        "confidence_factors": confidence_factors,
        "summary": summary,
        "evidence_refs": all_refs,
        "compared_targets": list(compared_by_instance.values()),
        "ruled_out": ruled_out,
        "root_location": {"type": location_type, "target_ref": target_ref, "evidence_refs": selected_refs},
        "domain_cause": {"type": domain_type, "subtype": subtype, "evidence_refs": selected_refs},
    }


def cluster_finding(assessment: dict[str, Any]) -> dict[str, Any]:
    knowledge_map = {
        "host_resource_contention": ["linux.iowait.shared_block_device"],
        "same_host_noisy_neighbor": ["distributed.same_host_noisy_neighbor"],
        "downstream_dependency": ["distributed.downstream_pressure"],
        "self_code_or_process_pressure": ["linux.cpu.process_pressure"],
    }
    domain_knowledge = {
        "cpu": ["linux.cpu.process_pressure"],
        "io": ["linux.iowait.shared_block_device"],
        "memory": ["linux.memory.process_growth"],
        "network": ["linux.network.retransmit"],
        "database": ["mysql.lock_wait"],
        "runtime": ["jvm.gc.pressure"],
    }
    domain = assessment.get("domain_cause", {})
    location_knowledge = knowledge_map.get(assessment["classification"], [])
    cause_knowledge = domain_knowledge.get(domain.get("type"), [])
    if domain.get("type") == "cpu" and domain.get("subtype") != "process_cpu_hotspot":
        cause_knowledge = []
    finding = DomainFinding(
        finding_id=f"finding.cluster.{assessment['classification']}",
        analyzer_id="cluster_assessor.v2",
        category="cluster",
        finding_type=assessment["classification"],
        severity="warning" if assessment["classification"] != "insufficient_evidence" else "info",
        confidence_level=assessment["confidence_level"],
        summary=assessment["summary"],
        evidence_refs=assessment.get("evidence_refs", []),
        missing_evidence=[] if assessment["classification"] != "insufficient_evidence" else [
            "目标、同宿主或下游的区分性指标",
        ],
        facts={"confidence_factors": assessment.get("confidence_factors", {})},
        knowledge_ids=list(dict.fromkeys(location_knowledge + cause_knowledge)),
    )
    return finding.model_dump(mode="json")


def _finding(
    observation: dict[str, Any], analyzer_id: str, category: str, finding_type: str,
    summary: str, *, severity: str = "warning", confidence: str = "中",
    facts: dict[str, Any] | None = None, knowledge_ids: list[str] | None = None,
    missing: list[str] | None = None,
) -> DomainFinding:
    instance_id = observation.get("target", {}).get("instance_id") or observation.get("task_id", "unknown")
    return DomainFinding(
        finding_id=f"finding.{analyzer_id}.{instance_id}.{finding_type}",
        analyzer_id=analyzer_id,
        category=category,
        finding_type=finding_type,
        severity=severity,
        confidence_level=confidence,
        summary=summary,
        evidence_refs=observation.get("evidence_refs", []),
        missing_evidence=missing or [],
        facts=facts or {},
        knowledge_ids=knowledge_ids or [],
    )


def _facts(observation: dict[str, Any]) -> dict[str, Any]:
    return observation.get("facts") or observation.get("summary") or {}


def _analyze_cpu(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    result: list[DomainFinding] = []
    user = _num(facts.get("avg_cpu_user_pct"))
    system = _num(facts.get("avg_cpu_sys_pct"))
    process_cores = _num(facts.get("process_cpu_core_usage"))
    top = _num(obs.get("top_function", {}).get("percent"))
    if top >= 40 or (process_cores >= 0.8 and bool(obs.get("top_function", {}).get("name"))):
        result.append(_finding(obs, "os_cpu_analyzer.v2", "cpu", "userland_hotspot",
            "用户态 CPU 或单一函数热点明显，优先检查目标进程代码路径。",
            confidence="高" if top >= 40 and process_cores >= 0.5 else "中",
            facts={"process_cpu_core_usage": process_cores, "top_function_pct": top, "scope": "process"},
            knowledge_ids=["linux.cpu.process_pressure"]))
    elif process_cores >= 0.8:
        result.append(_finding(obs, "os_cpu_analyzer.v2", "cpu", "process_cpu_pressure",
            "目标进程 CPU tick 增量显示其持续占用核心，但缺少 Profile，不能进一步断言具体代码热点。",
            confidence="中",
            facts={"process_cpu_core_usage": process_cores, "scope": "process"},
            knowledge_ids=["linux.cpu.process_pressure"],
            missing=["目标 PID 对应的 Profile TopN"]))
    elif user + system >= 75:
        result.append(_finding(obs, "os_cpu_analyzer.v2", "cpu", "host_cpu_pressure",
            "宿主机 CPU 压力较高，但缺少目标进程贡献度与 Profile，不能判为进程代码热点。",
            facts={"host_cpu_user_pct": user, "host_cpu_system_pct": system, "scope": "host"},
            missing=["目标进程 CPU 核心使用量", "目标 PID 对应的 Profile TopN"]))
    if system >= 30:
        result.append(_finding(obs, "os_cpu_analyzer.v2", "cpu", "kernel_overhead",
            "系统态 CPU 占比显著，需结合系统调用、网络或内核探针继续区分。",
            facts={"cpu_system_pct": system}, knowledge_ids=["linux.cpu.kernel_overhead"],
            missing=["系统调用或内核栈证据"]))
    return result


def _analyze_io(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    iowait = _num(facts.get("avg_cpu_iowait_pct"))
    block_latency_high = bool(obs.get("pressure", {}).get("block_latency_high"))
    if iowait < 10 and not block_latency_high:
        return []
    return [_finding(obs, "io_wait_analyzer.v2", "io", "io_wait_high",
        "I/O 等待或块设备延迟偏高，需要区分单进程 I/O、同宿主竞争和设备异常。",
        facts={"cpu_iowait_pct": iowait}, knowledge_ids=["linux.iowait.shared_block_device"],
        missing=[] if block_latency_high else ["块设备延迟直方图"])]


def _analyze_memory(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    rss = _num(facts.get("vmrss_mb"))
    rss_max = _num(facts.get("vmrss_mb_max"))
    trend = str(facts.get("vmrss_trend") or facts.get("memory_trend") or "").lower()
    if trend not in {"increasing", "growing"} and rss_max < 2048:
        return []
    finding_type = "rss_growth" if trend in {"increasing", "growing"} else "high_rss"
    return [_finding(obs, "memory_pressure_analyzer.v2", "memory", finding_type,
        "进程 RSS 呈增长趋势或已达到较高水位，应结合限制、回收行为和对象分配继续确认。",
        facts={"vmrss_mb": rss, "vmrss_mb_max": rss_max, "trend": trend or "unknown"},
        knowledge_ids=["linux.memory.process_growth"],
        missing=["容器/进程内存限制", "分配热点或 GC 证据"])]


def _analyze_network(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    loss = max(_num(facts.get("packet_loss_pct")), _num(facts.get("tcp_retransmit_pct")))
    p95 = _num(facts.get("network_latency_p95_ms"))
    if loss < 1 and p95 < 200:
        return []
    return [_finding(obs, "network_latency_analyzer.v1", "network", "packet_loss_or_latency",
        "网络丢包/重传或 P95 延迟异常，需要结合链路两端和路径证据定位。",
        severity="critical" if loss >= 5 else "warning",
        facts={"loss_or_retransmit_pct": loss, "latency_p95_ms": p95},
        knowledge_ids=["linux.network.retransmit"],
        missing=["对端指标", "路径与连接级重传分布"])]


def _analyze_mysql(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    waits = _num(facts.get("mysql_lock_wait_count"))
    seconds = _num(facts.get("mysql_lock_wait_seconds"))
    if waits <= 0 and seconds < 1:
        return []
    return [_finding(obs, "mysql_lock_analyzer.v1", "database", "mysql_lock_wait",
        "MySQL 锁等待已形成可观测阻塞，应检查阻塞会话、事务持续时间和访问顺序。",
        facts={"lock_wait_count": waits, "lock_wait_seconds": seconds},
        knowledge_ids=["mysql.lock_wait"], missing=["阻塞会话与被阻塞事务映射"])]


def _analyze_jvm(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    pause = _num(facts.get("jvm_gc_pause_p95_ms"))
    ratio = _num(facts.get("jvm_gc_time_pct"))
    if pause < 200 and ratio < 10:
        return []
    return [_finding(obs, "jvm_gc_analyzer.v1", "runtime", "jvm_gc_pressure",
        "JVM GC 暂停或 GC 时间占比异常，需要结合堆占用、分配速率和 GC 原因分析。",
        facts={"gc_pause_p95_ms": pause, "gc_time_pct": ratio},
        knowledge_ids=["jvm.gc.pressure"], missing=["堆分代占用", "GC cause"])]


def _has_self_hotspot(obs: dict[str, Any]) -> bool:
    return _num(obs.get("top_function", {}).get("percent")) >= 40


def _has_pressure(obs: dict[str, Any]) -> bool:
    return any(bool(value) for value in obs.get("pressure", {}).values())


def _unique_refs(observations: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for obs in observations:
        for ref in obs.get("evidence_refs", []):
            if ref not in result:
                result.append(ref)
    return result


def _domain_cause(observations: list[dict[str, Any]]) -> tuple[str, str]:
    facts = [_facts(obs) for obs in observations]
    if any(_num(item.get("mysql_lock_wait_count")) > 0 or _num(item.get("mysql_lock_wait_seconds")) > 0 for item in facts):
        return "database", "mysql_lock_wait"
    if any(_num(item.get("jvm_gc_pause_p95_ms")) >= 200 or _num(item.get("jvm_gc_time_pct")) >= 10 for item in facts):
        return "runtime", "jvm_gc_pressure"
    if any(_num(item.get("packet_loss_pct")) >= 1 or _num(item.get("tcp_retransmit_pct")) >= 1 for item in facts):
        return "network", "packet_loss_or_retransmit"
    if any(obs.get("pressure", {}).get("block_latency_high") or obs.get("pressure", {}).get("io_wait") for obs in observations):
        return "io", "host_or_shared_io_pressure"
    if any(obs.get("pressure", {}).get("memory") for obs in observations):
        return "memory", "process_or_container_memory_pressure"
    if any(obs.get("pressure", {}).get("cpu") for obs in observations):
        process_hot = any(
            _has_self_hotspot(obs) or _num(_facts(obs).get("process_cpu_core_usage")) >= 0.8
            for obs in observations
        )
        return "cpu", "process_cpu_pressure" if process_hot else "host_cpu_saturation"
    return "unknown", "unknown"


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
