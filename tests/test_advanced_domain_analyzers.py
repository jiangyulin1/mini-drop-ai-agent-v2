from server.app.diagnosis.domain_analyzers import analyze_observations, assess_cluster, _domain_cause


def _obs(task_id, facts, pressure=None, collector="sys_metrics"):
    return {
        "task_id": task_id,
        "collector_type": collector,
        "source_family": "procfs_metrics" if collector == "sys_metrics" else collector,
        "evidence_weight": 0.95,
        "target": {
            "service_id": "paymentservice",
            "instance_id": "payment-1",
            "host_id": "worker-1",
            "agent_id": "a1",
            "pid": 100,
        },
        "collection_status": "DONE",
        "facts": facts,
        "summary": facts,
        "pressure": pressure or {},
        "evidence_refs": [f"ev-{task_id}"],
        "top_function": {"name": "", "percent": 0},
    }


def _scope():
    return {
        "target_service": "paymentservice",
        "instances": [{"service_id": "paymentservice", "instance_id": "payment-1"}],
        "same_host_instance_ids": [],
        "downstream_service_ids": [],
    }


def test_oom_is_critical_and_becomes_specific_cluster_cause():
    observation = _obs("oom", {
        "container_oom_delta": 1,
        "container_oom_kill_delta": 1,
        "container_memory_usage_ratio": 1.0,
    }, {"memory": True, "oom": True})
    findings = analyze_observations([observation])
    assert any(item["finding_type"] == "cgroup_oom" and item["severity"] == "critical" for item in findings)
    result = assess_cluster(_scope(), [observation])
    assert result["classification"] == "process_oom"
    assert result["domain_cause"]["type"] == "memory"


def test_disk_full_and_network_loss_form_compound_incident():
    disk = _obs("disk", {
        "root_fs_used_pct": 99.2,
        "root_fs_available_bytes": 1024,
    }, {"disk_full": True})
    network = _obs("net", {
        "tcp_retransmit_pct": 18.0,
        "tcp_timeout_delta": 9,
    }, {"network_loss": True}, collector="ebpf_io")
    result = assess_cluster(_scope(), [disk, network])
    assert result["classification"] == "compound_incident"
    assert result["is_compound"] is True
    assert {item["domain"] for item in result["contributing_causes"][:2]} == {"io", "network"}


def test_zero_available_bytes_is_disk_exhaustion_despite_reserved_blocks():
    observation = _obs("disk-zero", {
        "root_fs_used_pct": 20.0,
        "target_fs_used_pct": 92.0,
        "target_fs_available_bytes": 0,
    }, {"disk_full": True})

    findings = analyze_observations([observation])
    result = assess_cluster(_scope(), [observation])

    assert any(item["finding_type"] == "filesystem_exhaustion" for item in findings)
    assert result["classification"] == "filesystem_exhaustion"


def test_duplicate_findings_merge_independent_evidence_before_verification():
    metrics = _obs("disk-metrics", {
        "target_fs_used_pct": 92.0,
        "target_fs_available_bytes": 0,
    }, {"disk_full": True})
    log = _obs("disk-log", {
        "enospc_count": 1,
    }, {"disk_full": True}, collector="log_scan")

    findings = analyze_observations([metrics, log])
    disk_findings = [item for item in findings if item["finding_type"] == "filesystem_exhaustion"]

    assert len(disk_findings) == 1
    assert set(disk_findings[0]["evidence_refs"]) == {"ev-disk-metrics", "ev-disk-log"}


def test_runtime_stall_from_stopped_threads():
    """进程被 SIGSTOP（T 态线程 + 零 CPU 前进）→ runtime_stall，不是锁也不是 D 态。"""
    from server.app.diagnosis.domain_analyzers import analyze_observations
    obs = _obs("stall", {
        "runtime_type": "python",
        "thread_count_max": 33,
        "lock_waiter_count_max": 0,
        "blocked_thread_ratio_max": 0.0,
        "uninterruptible_thread_count_max": 0,
        "stopped_thread_count_max": 33,
        "cpu_tick_delta": 0,
    }, {"runtime_stall": True}, collector="runtime_snapshot")
    findings = analyze_observations([obs])
    assert any(item["finding_type"] == "stopped_stall" for item in findings)
    result = assess_cluster(_scope(), [obs])
    assert result["classification"] == "runtime_stall"
    assert result["domain_cause"]["type"] == "runtime"


def test_runtime_lock_contention_is_runtime_root_cause():
    # 真实锁竞争信号（VM 实测 GO-LOCK 0.96/28）：绝大多数线程持续阻塞且等待数足够多。
    observation = _obs("lock", {
        "runtime_type": "java",
        "lock_waiter_count_max": 28,
        "blocked_thread_ratio_max": 0.96,
        "uninterruptible_thread_count_max": 0,
    }, {"runtime_lock": True}, collector="runtime_snapshot")
    findings = analyze_observations([observation])
    assert any(item["finding_type"] == "lock_contention" for item in findings)
    result = assess_cluster(_scope(), [observation])
    assert result["classification"] == "runtime_lock_contention"
    assert result["domain_cause"]["type"] == "runtime"
    assert result["domain_cause"]["subtype"] == "java_lock_contention"


def test_memory_growth_and_runtime_lock_form_compound_incident_at_normal_quality():
    memory = _obs("memory-growth", {
        "vmrss_trend": "increasing",
        "vmrss_slope_bytes_per_second": 4 * 1024 * 1024,
        "container_memory_usage_ratio": 0.45,
    }, collector="sys_metrics")
    runtime = _obs("runtime-lock", {
        "runtime_type": "python",
        "lock_waiter_count_max": 16,
        "blocked_thread_ratio_max": 0.94,
    }, {"runtime_lock": True}, collector="runtime_snapshot")
    memory["evidence_weight"] = runtime["evidence_weight"] = 0.85

    result = assess_cluster(_scope(), [memory, runtime])

    assert result["classification"] == "compound_incident"
    assert {item["domain"] for item in result["contributing_causes"][:2]} == {
        "memory", "runtime",
    }


def test_slow_memory_growth_is_classified_as_memory_domain():
    # A slow leak (slope >= 1MB/s) but RSS still below 256MB: the memory
    # pressure flag does not fire (needs rss >= 256), yet the growth slope is a
    # strong memory-domain signal. domain_cause must not fall through to unknown.
    observation = _obs("slow-leak", {
        "vmrss_mb": 96.0,
        "vmrss_trend": "increasing",
        "vmrss_slope_bytes_per_second": 2 * 1024 * 1024,
        "container_memory_usage_ratio": 0.3,
    })

    domain, subtype = _domain_cause([observation])

    assert domain == "memory"
    assert subtype == "process_memory_growth"


def test_small_rss_drift_does_not_create_a_memory_cause():
    observation = _obs("tiny-rss-drift", {
        "vmrss_mb": 22.0,
        "vmrss_trend": "increasing",
        "vmrss_slope_bytes_per_second": 585,
        "container_memory_usage_ratio": 0.0,
        "host_memory_available_ratio": 0.78,
        "tcp_retransmit_pct": 34.0,
        "tcp_timeout_delta": 10,
    }, {"network_loss": True})

    findings = analyze_observations([observation])
    result = assess_cluster(_scope(), [observation])

    assert not any(item["category"] == "memory" for item in findings)
    assert result["classification"] == "network_degradation"


def test_structured_retransmit_signal_outranks_generic_connectivity_log():
    metrics = _obs("net-metrics", {
        "tcp_retransmit_pct": 22.0,
        "tcp_timeout_delta": 9,
    }, {"network_loss": True})
    log = _obs("net-log", {
        "connection_refused_count": 1,
        "timeout_count": 4,
    }, {"network_loss": True}, collector="log_scan")
    log["log"] = {
        "log_files": 1,
        "error_count": 1,
        "patterns": {"connection_refused": 1, "timeout": 4},
    }

    result = assess_cluster(_scope(), [metrics, log])

    assert result["classification"] == "network_degradation"
    assert result["domain_cause"]["type"] == "network"


def test_weak_network_loss_is_network_degradation_not_self_pressure():
    # 弱网络故障（重传 3.2% < 5%）也必须归因 network_degradation，不能落到
    # self_code_or_process_pressure（V9 NETLOSS r03 漏检根因）。
    obs = _obs("weak-net", {
        "tcp_retransmit_pct": 3.2,
        "tcp_timeout_delta": 1,
    }, {"network_loss": True})
    result = assess_cluster(_scope(), [obs])
    assert result["classification"] == "network_degradation"
    assert result["domain_cause"]["type"] == "network"


def _scope_with_neighbor(neighbor_instance_id="noise-1"):
    return {
        "target_service": "paymentservice",
        "instances": [{"service_id": "paymentservice", "instance_id": "payment-1"}],
        "same_host_instance_ids": [neighbor_instance_id],
        "downstream_service_ids": [],
    }


def _same_host_obs(task_id, facts, pressure, neighbor_instance_id="noise-1",
                   service_id="noise-generator", collector="sys_metrics"):
    obs = _obs(task_id, facts, pressure, collector=collector)
    obs["target"]["instance_id"] = neighbor_instance_id
    obs["target"]["service_id"] = service_id
    obs["target"]["host_id"] = "worker-1"
    return obs


def test_host_memory_contention_when_same_host_is_memory_source():
    # 同宿主内存生成器耗尽宿主内存 + 目标非内存来源但降级 → 宿主内存争抢(memory 域)。
    target = _obs("target", {"process_cpu_core_usage": 0.1}, {"network_loss": True})
    neighbor = _same_host_obs("neighbor", {
        "vmrss_mb": 4200.0,
        "vmrss_trend": "increasing",
    }, {"memory": True})
    result = assess_cluster(_scope_with_neighbor(), [target, neighbor])
    assert result["classification"] == "host_resource_contention"
    assert result["domain_cause"]["type"] == "memory"
    assert result["domain_cause"]["subtype"] == "host_memory_contention"
    assert result["root_location"]["type"] == "shared_resource"


def test_noisy_neighbor_takes_precedence_over_connectivity_errors():
    # 同宿主 CPU 噪声源把目标打停（runtime_stall）+ 连接类错误 → 归噪声邻居
    # （连接错误是后果，且目标停顿 + 网络 = 单根因，不拆复合）。
    target = _obs("target", {
        "process_cpu_core_usage": 0.9,
        "runtime_type": "python",
        "stopped_thread_count_max": 3,
        "thread_count_max": 10,
        "cpu_tick_delta": 0,
    }, {"cpu": True, "network_loss": True, "runtime_stall": True})
    target["log"] = {"log_files": 1, "error_count": 5, "patterns": {"connection_refused": 5}}
    neighbor = _same_host_obs("neighbor", {"process_cpu_core_usage": 1.6}, {"cpu": True})
    result = assess_cluster(_scope_with_neighbor(), [target, neighbor])
    assert result["classification"] == "same_host_noisy_neighbor"
    assert result["root_location"]["type"] == "same_host"
    assert result["domain_cause"]["type"] == "cpu"


def test_single_host_disk_full_is_self_location():
    obs = _obs("disk", {
        "target_fs_used_pct": 99.0,
        "target_fs_available_bytes": 0,
    }, {"disk_full": True})
    result = assess_cluster(_scope(), [obs])
    assert result["classification"] == "filesystem_exhaustion"
    assert result["root_location"]["type"] == "self"


def test_multi_host_disk_full_is_shared_resource_location():
    o1 = _obs("d1", {"target_fs_used_pct": 99.0}, {"disk_full": True})
    o2 = _obs("d2", {"root_fs_used_pct": 98.0}, {"disk_full": True})
    o2["target"]["host_id"] = "worker-2"
    o2["target"]["instance_id"] = "other-2"
    result = assess_cluster(_scope(), [o1, o2])
    assert result["classification"] == "filesystem_exhaustion"
    assert result["root_location"]["type"] == "shared_resource"


def test_log_driven_network_loss_with_refused_stays_downstream_dependency():
    # 下游服务停摆（connection refused 日志）触发 network_loss 标记，但无重传事实：
    # 归 downstream_dependency，不能被压成 network_degradation（SINGLE-REDIS 回归保护）。
    obs = _obs("redis-net", {
        "tcp_retransmit_pct": 0.0,
        "tcp_timeout_delta": 0,
    }, {"network_loss": True}, collector="log_scan")
    obs["log"] = {"log_files": 1, "error_count": 5, "patterns": {"connection_refused": 5, "timeout": 2}}
    result = assess_cluster(_scope(), [obs])
    assert result["classification"] == "downstream_dependency"
    assert result["domain_cause"]["type"] == "network"


def test_single_host_downstream_chain_is_not_multi_entity_compound():
    # 同一宿主内的依赖链（cartservice→redis-cart 停摆）是一个根因的下游传导，
    # 不能因多个实体都有网络信号而误判成 compound（SINGLE-REDIS 回归保护）。
    scope = {
        "target_service": "cartservice",
        "instances": [{"service_id": "cartservice", "instance_id": "cart-1"},
                      {"service_id": "redis-cart", "instance_id": "redis-cart-1"}],
        "same_host_instance_ids": [],
        "downstream_service_ids": ["redis-cart"],
    }
    cart_metrics = _obs("cart-metrics", {}, {"network_loss": True}, collector="sys_metrics")
    cart_metrics["evidence_weight"] = 0.5
    cart_metrics["target"]["service_id"] = "cartservice"
    cart_metrics["target"]["instance_id"] = "cart-1"
    cart_metrics["target"]["host_id"] = "worker-2"
    cart_log = _obs("cart-log", {}, {"network_loss": True}, collector="log_scan")
    cart_log["evidence_weight"] = 0.5
    cart_log["target"]["service_id"] = "cartservice"
    cart_log["target"]["instance_id"] = "cart-1"
    cart_log["target"]["host_id"] = "worker-2"
    cart_log["log"] = {"log_files": 1, "error_count": 8, "patterns": {"connection_refused": 4, "timeout": 4}}
    redis = _obs("redis", {}, {"network_loss": True}, collector="sys_metrics")
    redis["evidence_weight"] = 0.5
    redis["target"]["service_id"] = "redis-cart"
    redis["target"]["instance_id"] = "redis-cart-1"
    redis["target"]["host_id"] = "worker-2"
    result = assess_cluster(scope, [cart_metrics, cart_log, redis])
    assert result["classification"] == "downstream_dependency"
    assert result["is_compound"] is False


def test_timeout_only_network_loss_is_network_degradation():
    # 纯超时（无 refused/reset）的 network_loss 标记 + 无重传事实 = 链路/分区劣化
    # （PARTITION 模式），归 network_degradation 而非 downstream_dependency。
    obs = _obs("partition-net", {
        "tcp_retransmit_pct": 0.0,
        "tcp_timeout_delta": 0,
    }, {"network_loss": True}, collector="log_scan")
    obs["log"] = {"log_files": 1, "error_count": 8, "patterns": {"timeout": 8}}
    result = assess_cluster(_scope(), [obs])
    assert result["classification"] == "network_degradation"
    assert result["domain_cause"]["type"] == "network"


def test_target_network_degradation_is_self_location():
    # 网络劣化只发生在目标自身单点 → location self（NETLOSS/STALE-REAL）。
    obs = _obs("net", {"tcp_retransmit_pct": 6.0}, {"network_loss": True})
    result = assess_cluster(_scope(), [obs])
    assert result["classification"] == "network_degradation"
    assert result["root_location"]["type"] == "self"


def test_multi_entity_network_loss_is_downstream_location():
    # 多实体网络劣化（overlay partition）→ downstream，且 target 停顿 + 网络 = 单根因，
    # 不能被拆成 runtime+network 复合。
    target = _obs("front", {
        "tcp_retransmit_pct": 4.0,
        "stopped_thread_count_max": 33,
        "thread_count_max": 33,
        "cpu_tick_delta": 0,
        "runtime_type": "python",
    }, {"network_loss": True, "runtime_stall": True})
    cart = _obs("cart", {"tcp_retransmit_pct": 3.5}, {"network_loss": True})
    cart["target"]["service_id"] = "cartservice"
    cart["target"]["instance_id"] = "cart-1"
    cart["target"]["host_id"] = "worker-2"
    scope = {
        "target_service": "paymentservice",
        "instances": [{"service_id": "paymentservice", "instance_id": "payment-1"}],
        "same_host_instance_ids": [],
        "downstream_service_ids": ["cartservice"],
    }
    result = assess_cluster(scope, [target, cart])
    assert result["classification"] == "network_degradation"
    assert result["root_location"]["type"] == "downstream"
    assert result["domain_cause"]["type"] == "network"
