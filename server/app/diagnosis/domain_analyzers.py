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
    "log_analyzer.v1": {"required_facts": ["log.error_count|log.patterns"], "optional_facts": ["log.top_errors"], "minimum_quality": "low", "scope": "process"},
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
        _analyze_log,
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
            "collection_statuses": [],
            "failure_kinds": [],
            "observation_count": 0,
        })
        for name, flagged in obs.get("pressure", {}).items():
            item["pressure"][name] = item["pressure"].get(name, False) or bool(flagged)
        item["evidence_refs"] = list(dict.fromkeys(item["evidence_refs"] + obs.get("evidence_refs", [])))
        if obs.get("collector_type") not in item["collector_types"]:
            item["collector_types"].append(obs.get("collector_type"))
        if (
            obs.get("collection_status")
            and obs.get("collection_status") not in item["collection_statuses"]
        ):
            item["collection_statuses"].append(obs.get("collection_status"))
        if obs.get("failure_kind") and obs.get("failure_kind") not in item["failure_kinds"]:
            item["failure_kinds"].append(obs.get("failure_kind"))
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
    target_connectivity = _log_connectivity_count(target_obs)
    # 宿主 IO/内核开销信号：iowait 高、块延迟高，或宿主 system CPU 高（内核 IO 处理）
    # 且目标进程 CPU 未饱和（进程在等待 IO 而非跑热点）。
    host_iowait = any(
        obs.get("pressure", {}).get("host_iowait_high")
        or obs.get("pressure", {}).get("block_latency_high")
        for obs in target_obs
    )
    host_system_high = any(
        _num((obs.get("summary") or {}).get("avg_cpu_sys_pct")) >= 25
        and _num((obs.get("summary") or {}).get("process_cpu_core_usage")) < 1.5
        for obs in target_obs
    )
    shared_iowait = (
        any(obs.get("pressure", {}).get("io_wait") for obs in target_obs)
        and any(obs.get("pressure", {}).get("io_wait") for obs in same_host_obs)
    )
    target_instance_count = sum(
        1 for item in scope.get("instances", [])
        if item.get("service_id") == target_service
    )
    upload_failed_obs = [
        obs for obs in target_obs
        if obs.get("failure_kind") == "artifact_upload_failed"
    ]
    healthy_target_obs = [
        obs for obs in target_obs
        if obs.get("collection_status") == "DONE"
        and obs.get("failure_kind") != "artifact_upload_failed"
    ]
    has_distinct_healthy_peer = any(
        healthy.get("target", {}).get("instance_id")
        != failed.get("target", {}).get("instance_id")
        for failed in upload_failed_obs
        for healthy in healthy_target_obs
    )

    if target_instance_count >= 2 and upload_failed_obs and has_distinct_healthy_peer:
        classification = "single_instance_storage_path_failure"
        confidence_level = "高"
        failed_target = upload_failed_obs[0].get("target", {})
        summary = (
            f"同一服务的双 Worker 对照采集显示："
            f"{failed_target.get('instance_id') or failed_target.get('agent_id')} 在证据上传阶段失败，"
            "另一实例使用同一对象存储成功，优先定位为故障 Worker 到对象存储的定向网络、"
            "防火墙或 endpoint 链路问题，而不是对象存储整体不可用。"
        )
        confidence_factors = {
            "scope_coverage": "high",
            "source_independence": "high",
            "discriminating_evidence": "high",
        }
        ruled_out.extend([
            {
                "hypothesis": "shared_storage_outage",
                "reason": "健康 Worker 在同一时间窗成功产生并上传了结构化证据。",
                "evidence_refs": all_refs,
            },
            {
                "hypothesis": "agent_offline",
                "reason": "故障 Worker 已领取并执行任务，失败发生在产物上传阶段。",
                "evidence_refs": _unique_refs(upload_failed_obs),
            },
        ])
    elif target_connectivity:
        # 目标进程日志出现连接类错误（refused/reset/unreachable/denied）
        # 是下游依赖故障的直接信号；即使同时出现 CPU 压力（错误风暴导致），
        # 也应优先归因下游而不是目标自身代码热点。
        classification = "downstream_dependency"
        confidence_level = "高" if target_connectivity >= 5 else "中"
        summary = (
            f"目标进程日志出现 {target_connectivity} 次连接类错误（refused/reset/denied），"
            "优先怀疑下游依赖或网络策略；CPU 压力更可能是错误风暴的后果而非根因。"
        )
        confidence_factors = {"scope_coverage": "high", "source_independence": "medium", "discriminating_evidence": "high"}
        ruled_out.append({
            "hypothesis": "self_code_regression",
            "reason": "日志证据指向连接类失败，而非代码热点或内存压力。",
            "evidence_refs": _unique_refs(target_obs),
        })
    elif (shared_iowait or host_iowait or host_system_high) and not target_hot:
        classification = "host_resource_contention"
        confidence_level = "高" if len(all_refs) >= 4 else "中"
        summary = (
            "目标实例和/或同宿主实例表现出 I/O 等待（宿主 iowait 高），"
            "倾向于宿主机或共享块设备争抢。"
            if shared_iowait
            else (
                "目标实例所在宿主机的 I/O 等待（iowait）或块延迟显著偏高，优先怀疑宿主磁盘或共享块设备争抢。"
                if host_iowait
                else "宿主内核 CPU（system）占比显著偏高且目标进程 CPU 未饱和，优先怀疑宿主机 I/O 争抢或内核开销（如磁盘写入风暴）。"
            )
        )
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
        "single_instance_storage_path_failure": "self",
    }.get(classification, "unknown")
    selected = {
        "same_host": same_host_obs,
        "downstream": downstream_obs,
        "self": target_obs,
        "shared_resource": target_obs + same_host_obs,
    }.get(location_type, [])
    if classification == "downstream_dependency" and target_connectivity:
        # 下游依赖故障的连接类证据来自目标进程日志（log_scan），
        # 而非下游实例自身；仅当归因由目标日志连接错误驱动时绑定目标观察。
        selected = target_obs
    if classification == "single_instance_storage_path_failure":
        selected = upload_failed_obs
    selected_refs = _unique_refs(selected)
    target_ref = None
    if selected:
        target_ref = selected[0].get("target", {}).get("instance_id") or selected[0].get("target", {}).get("host_id")
    domain_type, subtype = _domain_cause(selected)
    if classification == "host_resource_contention":
        # 宿主资源争抢归因（iowait/块延迟/宿主 system 开销）统一判为 IO 领域，
        # 避免进程 CPU 表象（等待 IO 时的短暂 tick）覆盖宿主 IO 争抢的结论。
        domain_type, subtype = "io", "host_io_contention"
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
        "single_instance_storage_path_failure": ["linux.network.retransmit"],
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


def _analyze_log(obs: dict[str, Any]) -> list[DomainFinding]:
    """基于 process_log_scan 产物输出日志级 Finding（确定性，不读模型）。"""
    log = obs.get("log") or {}
    if not log.get("log_files"):
        return []
    result: list[DomainFinding] = []
    error_count = int(log.get("error_count", 0))
    patterns = log.get("patterns") or {}
    top_errors = log.get("top_errors") or []

    if error_count > 0:
        top_patterns = sorted(patterns.items(), key=lambda item: int(item[1]), reverse=True)[:4]
        pattern_text = "；".join(f"{key}×{count}" for key, count in top_patterns) or "未知模式"
        sample = top_errors[0].get("text", "")[:200] if top_errors else ""
        missing: list[str] = []
        if not top_errors:
            missing.append("错误行原文（当前仅统计到关键词计数）")
        result.append(_finding(
            obs, "log_analyzer.v1", "log", "error_pattern",
            f"进程日志尾部出现 {error_count} 条错误行，主要模式：{pattern_text}。"
            + (f" 示例：{sample}" if sample else ""),
            severity="warning",
            confidence="高" if error_count >= 5 else "中",
            facts={
                "error_count": error_count,
                "patterns": dict(top_patterns),
                "log_files": int(log.get("log_files", 0)),
                "scope": "process",
            },
            knowledge_ids=["log.common_error_patterns"],
            missing=missing,
        ))

    # 连接类错误往往指向下游依赖，单独成一条便于集群归因
    connectivity = sum(int(patterns.get(key, 0)) for key in (
        "connection_refused", "connection_reset", "refused", "econnrefused", "unreachable", "denied",
    ))
    if connectivity > 0:
        result.append(_finding(
            obs, "log_analyzer.v1", "network", "connectivity_errors",
            f"日志中出现 {connectivity} 次连接类错误（refused/reset/denied），优先怀疑下游依赖或网络策略。",
            severity="warning", confidence="中",
            facts={"connectivity_error_count": connectivity},
            knowledge_ids=["log.connectivity_errors"],
        ))

    timeout_count = int(patterns.get("timeout", 0)) + int(patterns.get("timed_out", 0))
    if timeout_count > 0:
        result.append(_finding(
            obs, "log_analyzer.v1", "network", "timeout_errors",
            f"日志中出现 {timeout_count} 次超时模式，需结合调用耗时与下游延迟验证。",
            severity="info", confidence="中",
            facts={"timeout_error_count": timeout_count},
            knowledge_ids=["log.timeout_errors"],
        ))
    return result


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


def _log_connectivity_count(observations: list[dict[str, Any]]) -> int:
    """统计观察集合中日志摘要的连接类错误数量（refused/reset/unreachable/denied）。"""
    total = 0
    keys = ("connection_refused", "connection_reset", "refused", "econnrefused", "unreachable", "denied")
    for obs in observations:
        patterns = (obs.get("log") or {}).get("patterns") or {}
        for key in keys:
            total += int(patterns.get(key, 0) or 0)
    return total


def _domain_cause(observations: list[dict[str, Any]]) -> tuple[str, str]:
    if any(obs.get("failure_kind") == "artifact_upload_failed" for obs in observations):
        return "network", "agent_to_object_storage_connectivity"
    if _log_connectivity_count(observations) > 0:
        return "network", "connectivity_errors"
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
