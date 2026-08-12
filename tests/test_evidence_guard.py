from datetime import datetime, timezone

from server.app.diagnosis.evidence_guard import curate_observations, robust_outlier_flags


def _obs(task_id: str, collector: str, *, cpu: bool, facts=None):
    return {
        "task_id": task_id,
        "collector_type": collector,
        "target": {"instance_id": "svc-1", "agent_id": "a1", "pid": 42},
        "collection_status": "DONE",
        "observed_at": "2026-08-11T00:00:00+00:00",
        "duration_sec": 15,
        "facts": facts or {"process_cpu_core_usage": 1.0 if cpu else 0.01},
        "pressure": {"cpu": cpu},
        "evidence_refs": [f"ev-{task_id}"],
    }


def test_curate_suppresses_correlated_duplicate_and_keeps_audit():
    first = _obs("t1", "sys_metrics", cpu=True)
    duplicate = _obs("t2", "memory_smaps", cpu=True)
    kept, review = curate_observations(
        [first, duplicate],
        incident_end=datetime(2026, 8, 11, 0, 0, 30, tzinfo=timezone.utc),
    )
    assert len(kept) == 1
    assert review["suppressed_observation_count"] == 1
    assert review["suppressed"][0]["reason"] == "DUPLICATE_OBSERVATION"


def test_curate_marks_independent_high_quality_conflict():
    metric = _obs("t1", "sys_metrics", cpu=True)
    profile = _obs("t2", "perf_cpu", cpu=False, facts={"top_function_pct": 0})
    kept, review = curate_observations(
        [metric, profile],
        incident_end="2026-08-11T00:00:30+00:00",
    )
    assert len(kept) == 2
    assert len(review["conflicts"]) == 1
    assert all("HIGH_QUALITY_SOURCE_CONFLICT" in item["evidence_warnings"] for item in kept)
    assert review["source_independence_count"] == 2


def test_unobservable_default_does_not_contradict_runtime_lock_evidence():
    metrics = _obs("metrics", "sys_metrics", cpu=False)
    metrics["pressure"]["runtime_lock"] = False
    runtime = _obs("runtime", "runtime_snapshot", cpu=False, facts={
        "runtime_type": "python",
        "lock_waiter_count_max": 8,
        "blocked_thread_ratio_max": 0.8,
    })
    runtime["pressure"] = {"runtime_lock": True, "cpu": False}

    kept, review = curate_observations(
        [metrics, runtime],
        incident_end="2026-08-11T00:00:30+00:00",
    )

    assert review["conflicts"] == []
    assert all(item["evidence_weight"] == 0.85 for item in kept)


def test_empty_log_tail_does_not_contradict_cgroup_oom_metrics():
    metrics = _obs("metrics", "sys_metrics", cpu=False, facts={
        "container_oom_delta": 2,
        "container_oom_kill_delta": 2,
    })
    metrics["pressure"] = {"oom": True, "memory": True}
    logs = _obs("logs", "log_scan", cpu=False, facts={"log_file_count": 0})
    logs["pressure"] = {"oom": False, "memory": False}

    kept, review = curate_observations(
        [metrics, logs],
        incident_end="2026-08-11T00:00:30+00:00",
    )

    assert review["conflicts"] == []
    assert all(item["evidence_weight"] == 0.85 for item in kept)


def test_robust_outlier_requires_population_and_flags_extreme_value():
    assert robust_outlier_flags([1, 1, 1]) == [False, False, False]
    flags = robust_outlier_flags([10, 11, 9, 10, 1000])
    assert flags == [False, False, False, False, True]
