from benchmarks.online_boutique_vm.score_verified_vm import score


def _passing_report():
    return {
        "deployment": {
            "worker_nodes": 2,
            "running_services": 12,
            "offline_bundle_sha256_verified": True,
        },
        "fault_cases": [
            {
                "case_id": f"case-{index}",
                "repetitions": 2,
                "fault_observed": True,
                "rollback_verified": True,
            }
            for index in range(8)
        ],
        "destructive_tests": {
            "network_partition": {"recovered": True},
            "disk_enospc": {"recovered": True},
            "control_restart": {"recovered": True},
        },
        "diagnoses": [
            {"multi_host": True, "score_pct": 100, "status": "COMPLETED"}
        ],
        "artifact_downloads": {"all_hashes_match": True},
        "stability": {
            "duration_sec": 1800,
            "availability_pct": 100,
            "agent_offline_samples": 0,
            "replica_failure_samples": 0,
        },
    }


def test_score_promotes_complete_report_to_verified_vm():
    result = score(_passing_report())
    assert result["score"] == 100
    assert result["tier"] == "verified_vm"
    assert result["mandatory_gates_passed"] is True


def test_score_keeps_incomplete_soak_as_candidate():
    report = _passing_report()
    report["stability"]["duration_sec"] = 1799
    result = score(report)
    assert result["score"] == 90
    assert result["tier"] == "vm_candidate"
    assert result["mandatory_gates_passed"] is False
