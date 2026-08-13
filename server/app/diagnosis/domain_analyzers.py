"""只基于结构化观测做判断的确定性领域分析器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from server.app.diagnosis.schemas import DomainFinding
from server.app.diagnosis.evidence_guard import independent_source_count


ANALYZER_CONTRACTS = {
    "os_cpu_analyzer.v2": {"required_facts": ["host.cpu"], "optional_facts": ["process.cpu", "profile.topn"], "minimum_quality": "medium", "scope": "host|process"},
    "io_wait_analyzer.v2": {"required_facts": ["host.cpu.iowait_ratio|io.block_latency_high"], "optional_facts": ["process.io"], "minimum_quality": "medium", "scope": "host|process"},
    "memory_pressure_analyzer.v2": {"required_facts": ["process.memory"], "optional_facts": ["container.memory"], "minimum_quality": "medium", "scope": "process|container"},
    "network_latency_analyzer.v1": {"required_facts": ["host.network"], "optional_facts": ["dependency.peer"], "minimum_quality": "medium", "scope": "host|dependency"},
    "mysql_lock_analyzer.v1": {"required_facts": ["dependency.mysql_lock_wait"], "optional_facts": ["dependency.blocking_session"], "minimum_quality": "medium", "scope": "dependency"},
    "jvm_gc_analyzer.v1": {"required_facts": ["runtime.jvm_gc"], "optional_facts": ["process.memory"], "minimum_quality": "medium", "scope": "process"},
    "filesystem_capacity_analyzer.v1": {"required_facts": ["host.filesystem"], "optional_facts": ["log.enospc"], "minimum_quality": "medium", "scope": "host|container"},
    "runtime_blocking_analyzer.v1": {"required_facts": ["runtime.thread_states"], "optional_facts": ["profile.topn"], "minimum_quality": "medium", "scope": "process"},
    "log_analyzer.v1": {"required_facts": ["log.error_count|log.patterns"], "optional_facts": ["log.top_errors"], "minimum_quality": "low", "scope": "process"},
    "connection_probe.v1": {"required_facts": ["dependency.endpoint_reachability"], "optional_facts": ["dependency.container_state"], "minimum_quality": "medium", "scope": "dependency"},
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
        _analyze_disk,
        _analyze_runtime,
        _analyze_log,
        _analyze_connectivity,
    )
    for observation in observations:
        for analyzer in analyzers:
            findings.extend(analyzer(observation))
    return _merge_findings([item.model_dump(mode="json") for item in findings])


def _merge_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge the same deterministic finding emitted by independent sources."""
    merged: dict[str, dict[str, Any]] = {}
    severity_rank = {"info": 0, "warning": 1, "critical": 2}
    confidence_rank = {"不可判断": 0, "低": 1, "中": 2, "高": 3}
    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        current = merged.get(finding_id)
        if current is None:
            merged[finding_id] = dict(finding)
            continue
        current["evidence_refs"] = list(dict.fromkeys(
            list(current.get("evidence_refs") or []) + list(finding.get("evidence_refs") or [])
        ))
        current["contradicting_evidence_refs"] = list(dict.fromkeys(
            list(current.get("contradicting_evidence_refs") or [])
            + list(finding.get("contradicting_evidence_refs") or [])
        ))
        current_missing = set(current.get("missing_evidence") or [])
        incoming_missing = set(finding.get("missing_evidence") or [])
        current["missing_evidence"] = sorted(current_missing.intersection(incoming_missing))
        current["facts"] = {**(current.get("facts") or {}), **(finding.get("facts") or {})}
        if severity_rank.get(str(finding.get("severity")), 0) > severity_rank.get(str(current.get("severity")), 0):
            current["severity"] = finding.get("severity")
        if confidence_rank.get(str(finding.get("confidence_level")), 0) > confidence_rank.get(str(current.get("confidence_level")), 0):
            current["confidence_level"] = finding.get("confidence_level")
            current["summary"] = finding.get("summary")
        current["knowledge_ids"] = list(dict.fromkeys(
            list(current.get("knowledge_ids") or []) + list(finding.get("knowledge_ids") or [])
        ))
    return list(merged.values())


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
    endpoint_downstream = _endpoint_downstream_unreachable(target_obs)
    # 噪声邻居更常见于 CPU/线程这类按进程可分流的资源；宿主 I/O 等待/内存/磁盘
    # 是共享池，目标与邻居同时出现共享资源压力时应归宿主资源争抢而不是某个邻居。
    neighbor_cpu_pressure = any(
        bool({"cpu", "thread"} & _pressure_kinds(obs))
        for obs in same_host_obs
    )
    # 目标自身不是内存来源、但同宿主有内存压力且目标也感受到 → 共享宿主内存争抢。
    shared_memory = (
        bool(neighbor_pressure)
        and any(obs.get("pressure", {}).get("memory") for obs in target_obs)
        and not any(_memory_source(obs) for obs in target_obs)
    )
    contributing_causes = _contributing_causes(
        scope, observations, target_obs, same_host_obs, downstream_obs,
    )
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
    elif target_connectivity or endpoint_downstream:
        # 目标进程日志出现连接类错误（refused/reset/unreachable/denied），
        # 或受控连接探针显示下游端点不可达/容器停摆，都是下游依赖故障的直接
        # 信号；即使同时出现 CPU 压力（错误风暴导致），也应优先归因下游而不是
        # 目标自身代码热点。
        classification = "downstream_dependency"
        confidence_level = "高" if (target_connectivity >= 5 or endpoint_downstream) else "中"
        sources = []
        if target_connectivity:
            sources.append(f"日志 {target_connectivity} 次连接类错误")
        if endpoint_downstream:
            sources.append("受控连接探针显示下游不可达")
        summary = (
            "；".join(sources)
            + "；优先怀疑下游依赖或网络策略，CPU 压力更可能是错误风暴的后果而非根因。"
        )
        confidence_factors = {"scope_coverage": "high", "source_independence": "medium", "discriminating_evidence": "high"}
        ruled_out.append({
            "hypothesis": "self_code_regression",
            "reason": "连接类证据指向下游依赖失败，而非代码热点或内存压力。",
            "evidence_refs": _unique_refs(target_obs),
        })
    elif (
        (shared_iowait or host_iowait or host_system_high or shared_memory)
        and not target_hot
        and not neighbor_cpu_pressure
    ):
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
    elif target_obs and same_host_obs and neighbor_cpu_pressure and not target_hot and not target_pressure:
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

    strong_causes = [item for item in contributing_causes if item["score"] >= 0.8]
    distinct_domains = {item["domain"] for item in strong_causes}
    # 多个不同实体同时各自成立强原因（如 cart 与 checkout 同时各自下游失败）同样
    # 构成复合事故。但这只对下游/网络类原因成立：同宿主两个实例共享 IO/内存压力
    # 是宿主资源争抢（同一原因），不能因 target_ref 不同而误判为复合。
    # 多实体下游复合对分数要求略低（≥0.6）：短时长 log_scan 的 evidence_weight
    # 会被质量扣分拉低到 0.5 左右，但"两个目标各自独立出现下游失败"本身即复合。
    multi_entity_downstream = [
        item for item in contributing_causes
        if item["score"] >= 0.6
        and item["classification"] in {"downstream_dependency", "network_degradation"}
    ]
    if len(strong_causes) >= 2 and len(distinct_domains) >= 2:
        primary = strong_causes[0]
        classification = "compound_incident"
        confidence_level = "高" if independent_source_count(observations) >= 2 else "中"
        summary = "检测到多个同时成立的故障域：" + "；".join(
            item["summary"] for item in strong_causes[:3]
        )
        confidence_factors = {
            "scope_coverage": "high" if len(compared_by_instance) >= 2 else "medium",
            "source_independence": "high" if independent_source_count(observations) >= 2 else "medium",
            "discriminating_evidence": "high",
        }
    elif (
        len(multi_entity_downstream) >= 2
        and len({str(item["target_ref"]) for item in multi_entity_downstream}) >= 2
    ):
        primary = multi_entity_downstream[0]
        classification = "compound_incident"
        confidence_level = "高" if independent_source_count(observations) >= 2 else "中"
        summary = "多个目标同时各自下游失败：" + "；".join(
            item["summary"] for item in multi_entity_downstream[:3]
        )
        confidence_factors = {
            "scope_coverage": "high" if len(compared_by_instance) >= 2 else "medium",
            "source_independence": "high" if independent_source_count(observations) >= 2 else "medium",
            "discriminating_evidence": "high",
        }
    elif strong_causes and (
        classification in {"insufficient_evidence", "self_code_or_process_pressure"}
        or (
            classification == "downstream_dependency"
            and strong_causes[0]["classification"] == "network_degradation"
        )
    ):
        primary = strong_causes[0]
        classification = primary["classification"]
        confidence_level = primary["confidence_level"]
        summary = primary["summary"]
    elif classification in {"insufficient_evidence", "self_code_or_process_pressure"}:
        # 运行时锁/停顿信号本身决定性（锁 0.96/27、停止 33/33），质量折扣会把它
        # 压到 0.8 强原因阈值以下；健康进程无此信号，不会误触，单独放宽。
        runtime_strong = [
            item for item in contributing_causes
            if item["score"] >= 0.6
            and item["classification"] in {"runtime_lock_contention", "runtime_stall"}
        ]
        if runtime_strong:
            primary = runtime_strong[0]
            classification = primary["classification"]
            confidence_level = primary["confidence_level"]
            summary = primary["summary"]

    location_type = {
        "host_resource_contention": "shared_resource",
        "same_host_noisy_neighbor": "same_host",
        "downstream_dependency": "downstream",
        "self_code_or_process_pressure": "self",
        "single_instance_storage_path_failure": "self",
        "process_oom": "self",
        "filesystem_exhaustion": "shared_resource",
        "network_degradation": "downstream",
        "runtime_lock_contention": "self",
        "runtime_stall": "self",
        "compound_incident": strong_causes[0]["location_type"] if strong_causes else "unknown",
    }.get(classification, "unknown")
    selected = {
        "same_host": same_host_obs,
        "downstream": downstream_obs,
        "self": target_obs,
        "shared_resource": target_obs + same_host_obs,
    }.get(location_type, [])
    if classification == "downstream_dependency" and (target_connectivity or endpoint_downstream):
        # 下游依赖故障的连接类证据来自目标进程（log_scan / 连接探针），
        # 而非下游实例自身；仅当归因由目标日志连接错误或端点探针驱动时绑定目标观察。
        selected = target_obs
    if classification == "single_instance_storage_path_failure":
        selected = upload_failed_obs
    if classification in {
        "process_oom", "filesystem_exhaustion", "network_degradation",
        "runtime_lock_contention", "runtime_stall", "compound_incident",
    } and strong_causes:
        primary_refs = set(strong_causes[0]["evidence_refs"])
        selected = [
            obs for obs in observations
            if primary_refs.intersection(obs.get("evidence_refs", []))
        ]
    selected_refs = _unique_refs(selected)
    target_ref = None
    if selected:
        target_ref = selected[0].get("target", {}).get("instance_id") or selected[0].get("target", {}).get("host_id")
    domain_type, subtype = _domain_cause(selected)
    if classification == "host_resource_contention":
        # 宿主资源争抢归因（iowait/块延迟/宿主 system 开销）统一判为 IO 领域，
        # 避免进程 CPU 表象（等待 IO 时的短暂 tick）覆盖宿主 IO 争抢的结论。
        domain_type, subtype = "io", "host_io_contention"
    if strong_causes and classification in {
        "process_oom", "filesystem_exhaustion", "network_degradation",
        "runtime_lock_contention", "runtime_stall", "compound_incident",
    }:
        domain_type = strong_causes[0]["domain"]
        subtype = strong_causes[0]["subtype"]
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
        "contributing_causes": contributing_causes,
        "is_compound": classification == "compound_incident",
    }


def cluster_finding(assessment: dict[str, Any]) -> dict[str, Any]:
    knowledge_map = {
        "host_resource_contention": ["linux.iowait.shared_block_device"],
        "same_host_noisy_neighbor": ["distributed.same_host_noisy_neighbor"],
        "downstream_dependency": ["distributed.downstream_pressure"],
        "self_code_or_process_pressure": ["linux.cpu.process_pressure"],
        "single_instance_storage_path_failure": ["linux.network.retransmit"],
        "process_oom": ["linux.memory.process_growth"],
        "filesystem_exhaustion": ["linux.iowait.shared_block_device"],
        "network_degradation": ["linux.network.retransmit"],
        "runtime_lock_contention": ["runtime.lock_contention"],
        "runtime_stall": ["runtime.process_stall"],
        "compound_incident": ["distributed.compound_incident"],
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
    slope = _num(facts.get("vmrss_slope_bytes_per_second"))
    usage_ratio = _num(facts.get("container_memory_usage_ratio"))
    oom = _num(facts.get("container_oom_delta"))
    oom_kill = _num(facts.get("container_oom_kill_delta"))
    host_available = facts.get("host_memory_available_ratio")
    host_available = _num(host_available) if host_available is not None else 1.0
    meaningful_growth = (
        slope >= 1024 * 1024
        or (trend in {"increasing", "growing"} and rss >= 256 and slope >= 256 * 1024)
    )
    if oom_kill > 0 or oom > 0:
        return [_finding(obs, "memory_pressure_analyzer.v2", "memory", "cgroup_oom",
            f"目标 cgroup 在采集窗口内出现 OOM 事件 {int(oom)} 次、OOM Kill {int(oom_kill)} 次。",
            severity="critical", confidence="高",
            facts={"oom_delta": oom, "oom_kill_delta": oom_kill, "memory_usage_ratio": usage_ratio},
            knowledge_ids=["linux.memory.cgroup_oom"])]
    if not meaningful_growth and rss_max < 2048 and usage_ratio < 0.9 and host_available > 0.05:
        return []
    finding_type = "rss_growth" if meaningful_growth else "high_rss"
    return [_finding(obs, "memory_pressure_analyzer.v2", "memory", finding_type,
        "进程 RSS 呈增长趋势或已达到较高水位，应结合限制、回收行为和对象分配继续确认。",
        facts={"vmrss_mb": rss, "vmrss_mb_max": rss_max, "trend": trend or "unknown",
               "rss_slope_bytes_per_second": slope, "memory_usage_ratio": usage_ratio,
               "host_memory_available_ratio": host_available},
        knowledge_ids=["linux.memory.process_growth"],
        missing=["容器/进程内存限制", "分配热点或 GC 证据"])]


def _analyze_network(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    loss = max(_num(facts.get("packet_loss_pct")), _num(facts.get("tcp_retransmit_pct")))
    p95 = _num(facts.get("network_latency_p95_ms"))
    timeouts = _num(facts.get("tcp_timeout_delta"))
    listen_drops = _num(facts.get("tcp_listen_drop_delta"))
    if loss < 1 and p95 < 200 and timeouts <= 0 and listen_drops <= 0:
        return []
    return [_finding(obs, "network_latency_analyzer.v1", "network", "packet_loss_or_latency",
        "网络丢包/重传或 P95 延迟异常，需要结合链路两端和路径证据定位。",
        severity="critical" if loss >= 5 else "warning",
        facts={"loss_or_retransmit_pct": loss, "latency_p95_ms": p95,
               "tcp_timeout_delta": timeouts, "listen_drop_delta": listen_drops},
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


def _analyze_disk(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    root_used = _num(facts.get("root_fs_used_pct"))
    target_used = _num(facts.get("target_fs_used_pct"))
    available = min(
        value for value in (
            _num(facts.get("root_fs_available_bytes")),
            _num(facts.get("target_fs_available_bytes")),
        ) if value > 0
    ) if any(_num(facts.get(key)) > 0 for key in (
        "root_fs_available_bytes", "target_fs_available_bytes",
    )) else 0
    enospc = _num(facts.get("enospc_count")) + _num(facts.get("no_space_left_count"))
    used = max(root_used, target_used)
    target_zero_available = (
        facts.get("target_fs_available_bytes") is not None
        and target_used > 0
        and _num(facts.get("target_fs_available_bytes")) == 0
    )
    if used < 90 and enospc <= 0 and not target_zero_available:
        return []
    return [_finding(
        obs, "filesystem_capacity_analyzer.v1", "io", "filesystem_exhaustion",
        f"目标文件系统使用率达到 {used:.1f}%" + (f"，并出现 {int(enospc)} 次 ENOSPC" if enospc else "。"),
        severity="critical" if used >= 98 or enospc or target_zero_available else "warning",
        confidence="高" if used >= 95 or enospc or target_zero_available else "中",
        facts={"filesystem_used_pct": used, "available_bytes": available, "enospc_count": enospc},
        knowledge_ids=["linux.filesystem.exhaustion"],
    )]


def _analyze_runtime(obs: dict[str, Any]) -> list[DomainFinding]:
    facts = _facts(obs)
    runtime_type = str(facts.get("runtime_type") or "unknown")
    blocked_ratio = _num(facts.get("blocked_thread_ratio_max"))
    waiters = _num(facts.get("lock_waiter_count_max"))
    uninterruptible = _num(facts.get("uninterruptible_thread_count_max"))
    # Go/Python 运行时的线程会常规性地 park 在 futex 上，健康服务也能到 0.9/9；
    # 只有绝大多数线程持续阻塞（≥0.9）且等待线程数足够多（≥15）才是真锁竞争。
    if waiters >= 15 and blocked_ratio >= 0.9:
        return [_finding(
            obs, "runtime_blocking_analyzer.v1", "runtime", "lock_contention",
            f"{runtime_type} 进程中锁等待线程峰值 {int(waiters)}，占线程数 {blocked_ratio:.0%}。",
            severity="critical" if blocked_ratio >= 0.95 else "warning",
            confidence="高" if blocked_ratio >= 0.95 else "中",
            facts={"runtime_type": runtime_type, "blocked_thread_ratio": blocked_ratio,
                   "lock_waiter_count": waiters, "uninterruptible_threads": uninterruptible},
            knowledge_ids=[f"runtime.{runtime_type}.lock_contention"],
            missing=[] if blocked_ratio >= 0.4 else ["语言级锁/线程 Dump 或锁事件 Profile"],
        )]
    if uninterruptible >= 2:
        return [_finding(
            obs, "runtime_blocking_analyzer.v1", "runtime", "uninterruptible_stall",
            f"{runtime_type} 进程存在 {int(uninterruptible)} 个不可中断睡眠线程，需检查 I/O 或内核等待。",
            confidence="中",
            facts={"runtime_type": runtime_type, "uninterruptible_threads": uninterruptible},
            knowledge_ids=["runtime.process_stall"],
        )]
    # T 态（stopped）线程 / 零 CPU 前进 = 进程被挂起（SIGSTOP / 端口卡死）。
    # 这是"业务请求没有继续推进"类停顿的确定性信号，uninterruptible 无法覆盖。
    stopped = _num(facts.get("stopped_thread_count_max"))
    cpu_delta = _num(facts.get("cpu_tick_delta"))
    thread_count = _num(facts.get("thread_count_max"))
    if stopped >= 2 or (thread_count >= 2 and cpu_delta == 0):
        return [_finding(
            obs, "runtime_blocking_analyzer.v1", "runtime", "stopped_stall",
            f"{runtime_type} 进程 {int(stopped)} 个线程处于停止态（T/SIGSTOP），"
            f"且采样窗口 CPU 前进为 0，业务无推进。",
            severity="critical" if stopped >= 2 else "warning",
            confidence="高" if stopped >= 2 else "中",
            facts={"runtime_type": runtime_type, "stopped_threads": stopped,
                   "cpu_tick_delta": cpu_delta, "thread_count": thread_count},
            knowledge_ids=["runtime.process_stall"],
        )]
    return []


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
    oom_count = int(patterns.get("out_of_memory", 0))
    if oom_count > 0:
        result.append(_finding(
            obs, "log_analyzer.v1", "memory", "oom_log",
            f"日志中出现 {oom_count} 次 OOM/Out of memory。",
            severity="critical", confidence="高",
            facts={"oom_log_count": oom_count},
            knowledge_ids=["linux.memory.cgroup_oom"],
        ))
    enospc_count = int(patterns.get("enospc", 0))
    if enospc_count > 0:
        result.append(_finding(
            obs, "log_analyzer.v1", "io", "enospc_log",
            f"日志中出现 {enospc_count} 次 ENOSPC/磁盘空间不足。",
            severity="critical", confidence="高",
            facts={"enospc_count": enospc_count},
            knowledge_ids=["linux.filesystem.exhaustion"],
        ))
    lock_count = int(patterns.get("deadlock", 0)) + int(patterns.get("lock_timeout", 0))
    if lock_count > 0:
        result.append(_finding(
            obs, "log_analyzer.v1", "runtime", "lock_failure_log",
            f"日志中出现 {lock_count} 次死锁或锁等待超时。",
            severity="critical", confidence="高",
            facts={"lock_failure_count": lock_count},
            knowledge_ids=["runtime.lock_contention"],
        ))
    return result


def _analyze_connectivity(obs: dict[str, Any]) -> list[DomainFinding]:
    """基于 connection_probe 产物输出下游端点连通性 Finding（确定性）。"""
    facts = _facts(obs)
    reachable = facts.get("endpoint.reachable")
    container_state = facts.get("endpoint.container_state")
    unreachable = _num(facts.get("endpoint.unreachable_count"))
    service = str(facts.get("endpoint.downstream_service") or "")
    if not unreachable and "endpoint.reachable" not in facts and not container_state:
        return []
    result: list[DomainFinding] = []
    if reachable is False or unreachable > 0:
        detail = f"，下游容器状态为 {container_state}" if container_state else ""
        result.append(_finding(
            obs, "connection_probe.v1", "network", "endpoint_unreachable",
            f"受控连接探针显示下游端点（{service or '未知'}）不可达{detail}，"
            "目标自身症状更可能是等待下游导致。",
            severity="warning", confidence="中",
            facts={"downstream_service": service, "unreachable_count": unreachable},
            knowledge_ids=["network.downstream_reachability"],
        ))
    elif container_state in {"paused", "exited", "restarting"}:
        result.append(_finding(
            obs, "connection_probe.v1", "network", "downstream_container_unhealthy",
            f"下游容器（{service or '未知'}）状态为 {container_state}，业务已停摆。",
            severity="critical" if container_state == "paused" else "warning",
            confidence="高",
            facts={"downstream_service": service, "container_state": container_state},
            knowledge_ids=["runtime.container_pause"],
        ))
    elif reachable is True:
        result.append(_finding(
            obs, "connection_probe.v1", "network", "endpoint_reachable",
            f"受控连接探针确认下游端点（{service or '未知'}）可达，弱化下游依赖故障假设。",
            severity="info", confidence="中",
            facts={"downstream_service": service, "unreachable_count": 0},
            knowledge_ids=["network.downstream_reachability"],
        ))
    return result


def _endpoint_downstream_unreachable(observations: list[dict[str, Any]]) -> bool:
    """受控连接探针是否显示任一下游端点不可达或容器停摆。"""
    for obs in observations:
        facts = obs.get("facts") or {}
        if facts.get("endpoint.reachable") is False:
            return True
        if facts.get("endpoint.container_state") in {"paused", "exited", "restarting"}:
            return True
    return False


def _has_self_hotspot(obs: dict[str, Any]) -> bool:
    return _num(obs.get("top_function", {}).get("percent")) >= 40


def _has_pressure(obs: dict[str, Any]) -> bool:
    return any(bool(value) for value in obs.get("pressure", {}).values())


def _pressure_kinds(obs: dict[str, Any]) -> set[str]:
    return {name for name, value in (obs.get("pressure") or {}).items() if bool(value)}


def _memory_source(obs: dict[str, Any]) -> bool:
    """该观察是否为内存压力来源（RSS 持续增长或已很高）。"""
    facts = _facts(obs)
    return (
        _num(facts.get("vmrss_slope_bytes_per_second")) >= 256 * 1024
        or _num(facts.get("vmrss_mb")) >= 1024
    )


def _unique_refs(observations: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for obs in observations:
        for ref in obs.get("evidence_refs", []):
            if ref not in result:
                result.append(ref)
    return result


def _log_connectivity_count(observations: list[dict[str, Any]]) -> int:
    """统计观察集合中日志摘要的连接类错误数量（refused/reset/unreachable/denied）。

    只统计明确的连接失败信号。超时（timeout）本身是模糊信号——健康系统的例行
    慢调用也会在日志里留下 timeout + downstream_endpoint 字样，把这种超时计入
    下游连通性会让健康系统（NEG 案例）被误判为下游依赖故障。
    """
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
    if _endpoint_downstream_unreachable(observations):
        return "network", "downstream_unreachable"
    if _log_connectivity_count(observations) > 0:
        return "network", "connectivity_errors"
    facts = [_facts(obs) for obs in observations]
    # fd/句柄耗尽：socket 持有、连接泄漏等通常表现为 fd 持续增长或突破阈值，
    # 属于 network 域（与 connection_probe/进程日志的连接类错误并列）。
    if any(
        _num(item.get("fd_count")) >= 1000
        or (
            str(item.get("fd_trend") or item.get("fd_growth") or "").lower()
            in {"increasing", "growing"}
            and _num(item.get("fd_count")) >= 200
        )
        for item in facts
    ):
        return "network", "fd_or_socket_exhaustion"
    if any(_num(item.get("container_oom_kill_delta")) > 0 for item in facts):
        return "memory", "cgroup_oom_kill"
    # 与 memory_pressure_analyzer.v2 的 rss_growth 判定对齐：斜率 >= 1MB/s，
    # 或趋势递增且 RSS >= 256MB。慢速泄漏在诊断窗口内 RSS 可能未到 256MB，
    # 仅靠 pressure.memory 会漏判为 unknown；增长趋势本身就是内存域强信号。
    if any(
        _num(item.get("vmrss_slope_bytes_per_second")) >= 1024 * 1024
        or (
            str(item.get("vmrss_trend") or item.get("memory_trend") or "").lower()
            in {"increasing", "growing"}
            and _num(item.get("vmrss_mb")) >= 256
        )
        for item in facts
    ):
        return "memory", "process_memory_growth"
    if any(max(_num(item.get("root_fs_used_pct")), _num(item.get("target_fs_used_pct"))) >= 95 for item in facts):
        return "io", "filesystem_exhaustion"
    if any(_num(item.get("lock_waiter_count_max")) >= 15 and _num(item.get("blocked_thread_ratio_max")) >= 0.9 for item in facts):
        return "runtime", "lock_contention"
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


def _contributing_causes(
    scope: dict[str, Any],
    observations: list[dict[str, Any]],
    target_obs: list[dict[str, Any]],
    same_host_obs: list[dict[str, Any]],
    downstream_obs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build independent, ranked causes so compound incidents are not collapsed."""
    groups = {
        id(obs): (
            "downstream" if obs in downstream_obs
            else "same_host" if obs in same_host_obs
            else "self" if obs in target_obs
            else "unknown"
        )
        for obs in observations
    }
    candidates: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(obs: dict[str, Any], domain: str, subtype: str, classification: str,
            score: float, summary: str, location: str | None = None) -> None:
        target = obs.get("target") or {}
        target_ref = str(target.get("instance_id") or target.get("host_id") or target.get("agent_id") or "unknown")
        location_type = location or groups.get(id(obs), "unknown")
        key = (domain, subtype, target_ref)
        item = candidates.setdefault(key, {
            "domain": domain,
            "subtype": subtype,
            "classification": classification,
            "location_type": location_type,
            "target_ref": target_ref,
            "score": 0.0,
            "confidence_level": "低",
            "summary": summary,
            "evidence_refs": [],
            "source_families": [],
        })
        quality = float(obs.get("evidence_weight", 1.0))
        # Evidence quality moderates a deterministic signal without erasing
        # it. A completed structured collector starts at 0.85 quality, so a
        # direct OOM/ENOSPC/retransmit/lock fact must remain actionable.
        weighted = score * (0.5 + 0.5 * quality)
        item["score"] = max(float(item["score"]), weighted)
        item["evidence_refs"] = list(dict.fromkeys(item["evidence_refs"] + obs.get("evidence_refs", [])))
        family = str(obs.get("source_family") or obs.get("collector_type") or "unknown")
        if family not in item["source_families"]:
            item["source_families"].append(family)

    for obs in observations:
        if float(obs.get("evidence_weight", 1.0)) < 0.4:
            continue
        facts = _facts(obs)
        pressure = obs.get("pressure") or {}
        oom_kill = _num(facts.get("container_oom_kill_delta"))
        oom = _num(facts.get("container_oom_delta"))
        if oom_kill > 0 or oom > 0:
            add(obs, "memory", "cgroup_oom_kill", "process_oom", 1.0,
                f"{(obs.get('target') or {}).get('service_id') or '目标进程'} 出现 cgroup OOM/OOM Kill。")
        elif (
            _num(facts.get("vmrss_slope_bytes_per_second")) >= 1024 * 1024
            or (
                str(facts.get("vmrss_trend") or facts.get("memory_trend") or "").lower()
                in {"increasing", "growing"}
                and _num(facts.get("vmrss_mb")) >= 256
                and _num(facts.get("vmrss_slope_bytes_per_second")) >= 256 * 1024
            )
        ):
            add(obs, "memory", "memory_growth", "self_code_or_process_pressure", 0.9,
                "进程 RSS 在采集窗口内持续增长，符合内存泄漏或未受控缓存增长特征。")
        elif pressure.get("memory") or _num(facts.get("container_memory_usage_ratio")) >= 0.9:
            add(obs, "memory", "memory_pressure_or_leak", "self_code_or_process_pressure", 0.84,
                "进程或容器内存持续增长并接近限制。")

        disk_used = max(_num(facts.get("root_fs_used_pct")), _num(facts.get("target_fs_used_pct")))
        target_zero_available = (
            facts.get("target_fs_available_bytes") is not None
            and _num(facts.get("target_fs_used_pct")) > 0
            and _num(facts.get("target_fs_available_bytes")) == 0
        )
        if disk_used >= 95 or _num(facts.get("enospc_count")) > 0 or target_zero_available:
            add(obs, "io", "filesystem_exhaustion", "filesystem_exhaustion", 0.98,
                f"文件系统使用率达到 {disk_used:.1f}% 或已经返回 ENOSPC。", "shared_resource")
        elif pressure.get("io_wait") or pressure.get("block_latency_high"):
            add(obs, "io", "host_or_shared_io_pressure", "host_resource_contention", 0.82,
                "宿主或共享设备存在 I/O 等待/块延迟。", "shared_resource")

        retransmit = _num(facts.get("tcp_retransmit_pct"))
        timeouts = _num(facts.get("tcp_timeout_delta"))
        if retransmit >= 5 or timeouts >= 3:
            add(obs, "network", "packet_loss_or_timeout", "network_degradation", 0.92,
                f"TCP 重传 {retransmit:.1f}% 或超时增量 {int(timeouts)}。",
                "downstream" if groups.get(id(obs)) == "self" else groups.get(id(obs)))
        if _log_connectivity_count([obs]) > 0:
            add(obs, "network", "downstream_connectivity", "downstream_dependency", 0.9,
                "目标日志持续出现下游连接失败或超时。", "downstream")

        blocked = _num(facts.get("blocked_thread_ratio_max"))
        waiters = _num(facts.get("lock_waiter_count_max"))
        if waiters >= 15 and blocked >= 0.9:
            runtime = str(facts.get("runtime_type") or "unknown")
            add(obs, "runtime", f"{runtime}_lock_contention", "runtime_lock_contention", 0.94,
                f"{runtime} 运行时锁等待线程占比达到 {blocked:.0%}。")
        elif _num(facts.get("deadlock_count")) > 0 or _num(facts.get("lock_timeout_count")) > 0:
            add(obs, "runtime", "lock_failure_log", "runtime_lock_contention", 0.92,
                "应用日志明确出现死锁或锁等待超时。")
        elif _num(facts.get("uninterruptible_thread_count_max")) >= 2:
            add(obs, "runtime", "uninterruptible_stall", "runtime_stall", 0.85,
                "多个线程持续处于不可中断等待。")
        elif _num(facts.get("stopped_thread_count_max")) >= 2 or (
            _num(facts.get("thread_count_max")) >= 2 and _num(facts.get("cpu_tick_delta")) == 0
        ):
            add(obs, "runtime", "stopped_stall", "runtime_stall", 0.9,
                "进程线程处于停止态或采样窗口内 CPU 无前进，业务停滞。")

        if _has_self_hotspot(obs) or _num(facts.get("process_cpu_core_usage")) >= 0.8:
            add(obs, "cpu", "process_cpu_hotspot", "self_code_or_process_pressure", 0.88,
                "目标进程存在可复现的 CPU 热点。")

    result = list(candidates.values())
    for item in result:
        if len(item["source_families"]) >= 2:
            item["score"] = min(1.0, float(item["score"]) + 0.08)
        item["score"] = round(float(item["score"]), 3)
        item["confidence_level"] = (
            "高" if item["score"] >= 0.85
            else "中" if item["score"] >= 0.6
            else "低"
        )
    return sorted(result, key=lambda item: (-float(item["score"]), item["domain"], item["target_ref"]))


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
