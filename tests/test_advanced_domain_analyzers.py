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
