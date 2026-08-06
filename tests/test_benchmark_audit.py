from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_diagnosis_dataset import main
from server.app.diagnosis.benchmark_audit import BenchmarkAuditError, audit_dataset, render_markdown


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _case(case_id: str = "T1-CPU-001") -> dict:
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "title": "CPU latency symptom",
        "source_id": "fixture",
        "fault_type": "CPU_HOTSPOT",
        "query": "Requests become slow after several minutes.",
        "trigger": {"adapter": "implementation_defined", "action": "cpu_hotspot"},
        "topology": {"minimum_hosts": 1},
        "evidence_plan": {
            "snapshot_roles": ["baseline", "incident", "verification"],
            "required_evidence": ["cpu_metric_change", "profile_hot_function"],
        },
        "oracle": {
            "expected_scope": "service",
            "expected_root_cause": "hot function",
            "expected_terminal_class": "PERFORMANCE",
            "expected_location_type": "self",
            "expected_domain_type": "cpu",
            "expected_classification": "self_code_or_process_pressure",
        },
        "execution": {"warmup_runs": 1, "repetitions": 3, "timeout_seconds": 60},
        "reference_ids": [],
    }


def _dataset(tmp_path: Path, case: dict | None = None) -> Path:
    root = tmp_path / "dataset"
    value = case or _case()
    _write_json(root / "manifest.json", {
        "dataset": "test-suite", "version": "1.0", "core_cases": [{"case_id": value["case_id"]}]
    })
    _write_json(root / "cases" / f"{value['case_id']}.json", value)
    return root


def _environment(tmp_path: Path, profile_hot_function: str = "SUPPORTED") -> Path:
    path = tmp_path / "environment.json"
    _write_json(path, {
        "environment_id": "test-lab",
        "topology": {"worker_hosts": 2},
        "evidence_capabilities": {
            "cpu_metric_change": "SUPPORTED",
            "profile_hot_function": profile_hot_function,
        },
    })
    return path


def test_audit_separates_readiness_from_ai_accuracy(tmp_path: Path) -> None:
    report = audit_dataset(_dataset(tmp_path), _environment(tmp_path))

    assert report["case_count"] == 1
    assert report["formal_ai_accuracy_measured"] is False
    assert report["cases"][0]["readiness"] == "RUNNABLE"
    codes = {item["code"] for item in report["cases"][0]["findings"]}
    assert "PUBLIC_PRIVATE_NOT_SEPARATED" in codes
    assert "EXECUTABLE_LIFECYCLE_INCOMPLETE" in codes
    assert report["score"] < 60


def test_partial_environment_is_not_reported_as_ai_failure(tmp_path: Path) -> None:
    report = audit_dataset(
        _dataset(tmp_path), _environment(tmp_path, profile_hot_function="UNSUPPORTED")
    )

    result = report["cases"][0]
    assert result["readiness"] == "PARTIAL"
    assert result["missing_capabilities"] == ["profile_hot_function"]
    assert any(item["code"] == "ENVIRONMENT_CAPABILITY_GAP" for item in result["findings"])


def test_complete_v12_contract_scores_higher(tmp_path: Path) -> None:
    legacy = _case()
    improved = _case("T1-CPU-002")
    improved["performance_requirements"] = {
        "workload": {"rps": 20},
        "baseline": {"p95_ms_max": 200},
        "incident": {"cpu_pct_min": 80},
        "measurement_window": {"duration_seconds": 60},
        "recovery": {"p95_ms_max": 200, "stable_seconds": 60},
        "slo": {"p95_ms_max": 200},
    }
    improved["oracle"].update({
        "incident_trigger": "fault flag",
        "root_mechanism": "busy loop",
        "root_entity": "service-a",
        "affected_entities": ["service-a"],
        "propagation_path": ["service-a"],
        "symptom": "latency",
        "recovery_criteria": {"cpu_pct_max": 50},
        "accepted_answers": ["busy loop"],
        "forbidden_claims": ["database lock"],
        "expected_actions": ["perf_cpu"],
    })
    improved["trigger"]["adapter"] = "fixture-v1"
    improved["lifecycle"] = {step: {"script": f"fixtures/{step}.sh"} for step in (
        "setup", "baseline", "inject", "incident", "recover", "verify", "cleanup"
    )}
    improved["session_protocol"] = {
        "investigation_steps": {}, "candidate_updates": {}, "policy_decisions": {},
        "human_interventions": {}, "fix_result": {}, "verification_result": {},
    }
    # Non-empty values are required by the contract.
    improved["session_protocol"] = {key: {"required": True} for key in improved["session_protocol"]}

    legacy_report = audit_dataset(_dataset(tmp_path / "old", legacy), _environment(tmp_path / "old"))
    improved_report = audit_dataset(
        _dataset(tmp_path / "new", improved), _environment(tmp_path / "new")
    )
    assert improved_report["score"] > legacy_report["score"] + 40


def test_manifest_case_mismatch_is_rejected(tmp_path: Path) -> None:
    root = _dataset(tmp_path)
    _write_json(root / "manifest.json", {"core_cases": [{"case_id": "T1-OTHER-001"}]})

    with pytest.raises(BenchmarkAuditError, match="Manifest/case mismatch"):
        audit_dataset(root, _environment(tmp_path))


def test_cli_writes_both_reports_and_can_enforce_gate(tmp_path: Path) -> None:
    output = tmp_path / "reports"
    result = main([
        str(_dataset(tmp_path)), "--environment", str(_environment(tmp_path)),
        "--output-dir", str(output), "--minimum-score", "90",
    ])

    assert result == 2
    assert (output / "audit-report.json").is_file()
    markdown = (output / "audit-report.md").read_text(encoding="utf-8")
    assert "不代表 AI 正确率" in markdown
    assert "T1-CPU-001" in markdown


def test_markdown_contains_readiness_summary(tmp_path: Path) -> None:
    markdown = render_markdown(audit_dataset(_dataset(tmp_path), _environment(tmp_path)))

    assert "六维评分" in markdown
    assert "RUNNABLE" in markdown
