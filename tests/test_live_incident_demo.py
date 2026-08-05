from __future__ import annotations

from demo.presentation.incident_service import DemoState, calculate_discount_matrix, serialize_catalog
from demo.presentation.live_incident_demo import validate_diagnosis


def test_incident_workload_is_deterministic_and_nonempty():
    assert calculate_discount_matrix(17) == calculate_discount_matrix(17)
    assert serialize_catalog(rounds=1) > 1000


def test_demo_state_switches_between_normal_and_anomaly():
    state = DemoState()
    assert state.snapshot()["mode"] == "normal"
    state.set_anomaly(True)
    assert state.snapshot()["mode"] == "anomaly"
    state.set_anomaly(False)
    assert state.snapshot()["mode"] == "normal"


def test_validate_diagnosis_requires_real_flamegraph_evidence():
    detail = {
        "run": {"status": "DONE", "validated": True},
        "report": {
            "report": {
                "summary": "热点位于 serialize_catalog",
                "not_enough_evidence": False,
                "ranked_causes": [{"cause_id": "cpu_hotspot_recursive", "confidence": 0.8}],
            }
        },
        "tool_results": [
            {"tool_name": "get_flamegraph_top", "status": "success"},
            {"tool_name": "get_ebpf_latency_summary", "status": "not_applicable"},
        ],
    }

    passed, summary = validate_diagnosis(detail)

    assert passed is True
    assert summary["checks"]["flamegraph_used"] is True
    assert summary["causes"][0]["cause_id"] == "cpu_hotspot_recursive"


def test_validate_diagnosis_rejects_missing_hotspot_evidence():
    detail = {
        "run": {"status": "DONE", "validated": True},
        "report": {
            "report": {
                "summary": "证据不足",
                "not_enough_evidence": True,
                "ranked_causes": [],
            }
        },
        "tool_results": [{"tool_name": "get_flamegraph_top", "status": "missing"}],
    }

    passed, summary = validate_diagnosis(detail)

    assert passed is False
    assert summary["checks"]["enough_evidence"] is False
    assert summary["checks"]["flamegraph_used"] is False
