"""显式诊断流水线、结构化 Action 与 golden scenarios 回归。"""

from server.app.diagnosis.eval_harness import load_scenarios, run_evaluation


def test_golden_scenarios_cover_required_domains():
    scenarios = load_scenarios()
    ids = {item["scenario_id"] for item in scenarios}
    assert {
        "self_code_hotspot",
        "same_host_cpu_noise",
        "shared_io_contention",
        "downstream_cpu_hotspot",
        "memory_leak",
        "network_packet_loss",
        "mysql_lock_wait",
    }.issubset(ids)


def test_golden_scenarios_all_pass_with_safe_actions():
    report = run_evaluation()
    assert report["total"] >= 7
    assert report["failed"] == 0
    assert report["metrics"]["classification_accuracy"] == 1.0
    assert report["metrics"]["evidence_reference_integrity"] == 1.0
    assert report["metrics"]["unsafe_auto_execute_count"] == 0

