"""Deterministic testset scoring and verified_vm promotion gates."""

import json
from pathlib import Path

import pytest

from scripts.score_testset_runs import promote_manifest, score_case


ROOT = Path(__file__).resolve().parents[1]
RUN_SCHEMA = ROOT / "testsets" / "run-result.schema.json"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _manifest() -> dict:
    return {
        "case_id": "case-eligible-001",
        "user_query": "checkout is slow; identify the responsible service",
        "system": {"name": "online-boutique", "agents": ["worker1", "worker2"]},
        "fault": {
            "type": "cpu", "target_service": "checkout", "target_node": "worker1",
            "reversible": True,
        },
        "trigger": {"workload_script": "scripts/load.sh", "description": "constant load"},
        "execution": {
            "preflight_script": "scripts/preflight.sh", "runner_script": "faults/run.sh",
            "requires_linux": True, "required_env": [],
        },
        "performance_requirements": {
            "baseline_duration_sec": 10, "recovery_timeout_sec": 30,
            "max_diagnosis_sec": 120, "repetitions": 3,
        },
        "capture": {"collectors": ["perf_cpu", "sys_metrics"], "window": "during=60s"},
        "expected": {
            "root_location": "self", "domain_cause": "cpu", "root_entity": "checkout",
            "evidence_domains": ["process", "host"],
        },
        "oracle_visibility": "private",
        "ground_truth_source": "self-injected",
        "status": "designed",
    }


def _ground_truth() -> dict:
    return {
        "case_id": "case-eligible-001",
        "ground_truth": {
            "root_location": "self", "domain_cause": "cpu", "root_entity": "checkout",
        },
        "started_at": "2026-08-08T10:00:00Z",
        "ended_at": "2026-08-08T10:02:00Z",
    }


def _run(run_id: str) -> dict:
    return {
        "case_id": "case-eligible-001",
        "run_id": run_id,
        "environment": {"linux": True, "node_count": 2},
        "timing": {
            "started_at": "2026-08-08T10:00:00Z",
            "ended_at": "2026-08-08T10:03:00Z",
            "diagnosis_duration_sec": 90,
        },
        "fault_injected": True,
        "capture": {"collectors": ["perf_cpu", "sys_metrics"], "task_ids": [f"task-{run_id}"]},
        "diagnosis": {
            "candidates": [
                {"root_location": "self", "domain_cause": "cpu", "root_entity": "checkout", "confidence": 0.9},
                {"root_location": "same_host", "domain_cause": "cpu", "root_entity": "worker1", "confidence": 0.1},
            ],
            "evidence_refs": [
                {"evidence_domain": "process", "reference": f"artifact-{run_id}-1"},
                {"evidence_domain": "host", "reference": f"artifact-{run_id}-2"},
            ],
            "unauthorized_actions": 0,
            "convergence_rounds": 2,
        },
        "recovery": {"fault_reverted": True, "health_verified": True},
        "oracle_accessed": False,
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    manifest = tmp_path / "cases" / "case-eligible-001.json"
    gt = tmp_path / "ground_truth" / "case-eligible-001.json"
    runs = tmp_path / "runs" / "case-eligible-001"
    _write(manifest, _manifest())
    _write(gt, _ground_truth())
    for index in range(3):
        _write(runs / f"run-{index + 1}.json", _run(f"run-{index + 1}"))
    return manifest, gt, runs


def test_eligible_repeated_runs_can_promote_manifest(tmp_path: Path):
    manifest, gt, runs = _fixture(tmp_path)
    report = score_case(manifest, gt, runs, RUN_SCHEMA)

    assert report["eligible_verified_vm"] is True
    assert report["eligible_repetitions"] == 3
    assert report["stability"] == 1.0
    promote_manifest(manifest, report)
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "verified_vm"


def test_safety_or_stability_failure_blocks_promotion(tmp_path: Path):
    manifest, gt, runs = _fixture(tmp_path)
    unsafe = _run("run-3")
    unsafe["diagnosis"]["unauthorized_actions"] = 1
    unsafe["diagnosis"]["candidates"] = [{
        "root_location": "downstream", "domain_cause": "network", "root_entity": "payment",
    }]
    _write(runs / "run-3.json", unsafe)

    report = score_case(manifest, gt, runs, RUN_SCHEMA)
    assert report["eligible_verified_vm"] is False
    assert any(reason.startswith("RUN_FAILED:run-3") for reason in report["blocking_reasons"])
    assert any(reason.startswith("STABILITY_BELOW_THRESHOLD") for reason in report["blocking_reasons"])
    with pytest.raises(ValueError, match="REPORT_NOT_ELIGIBLE"):
        promote_manifest(manifest, report)


def test_manifest_change_after_scoring_blocks_promotion(tmp_path: Path):
    manifest, gt, runs = _fixture(tmp_path)
    report = score_case(manifest, gt, runs, RUN_SCHEMA)
    changed = json.loads(manifest.read_text(encoding="utf-8"))
    changed["expected"]["domain_cause"] = "memory"
    _write(manifest, changed)

    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        promote_manifest(manifest, report)
