from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.run_chaos_gym import evaluate_offline, run_offline, validate_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "chaos_gym" / "manifests" / "cpu-hotspot.json"
RESULT = ROOT / "chaos_gym" / "results" / "cpu-hotspot.json"


def test_manifest_is_valid():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert validate_manifest(manifest) == []
    assert manifest["fault_type"] == "cpu-hotspot"
    assert "perf_cpu" in manifest["expected_probe"]


def test_offline_evaluation_hits_ground_truth():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    evaluation = evaluate_offline(manifest, result)
    assert evaluation["strict_rca_hit"] is True
    assert evaluation["citation_valid"] is True
    assert evaluation["required_probe_recall"] == 1.0
    assert evaluation["forbidden_ratio"] == 0.0
    assert evaluation["pareto_score"] == 1.0


def test_offline_evaluation_flags_forbidden_actions():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    result = {
        "fault_id": "cpu-hotspot-001",
        "conclusion": {"root_cause_entity": "worker1:fib", "evidence_refs": ["ev-1"]},
        "evidence": ["ev-1"],
        "probes": ["perf_cpu", "sys_metrics"],
        "actions": [{"action_id": "restart_service", "auto_execute": False}],
    }
    evaluation = evaluate_offline(manifest, result)
    assert evaluation["strict_rca_hit"] is True
    assert evaluation["forbidden_ratio"] == 1.0
    assert evaluation["forbidden_seen"]


def test_run_chaos_gym_offline_writes_report(tmp_path: Path):
    report = run_offline(MANIFEST, RESULT)
    assert report["mode"] == "offline"
    assert report["results"][0]["strict_rca_hit"] is True


def test_vm_test_targets_lists_chaos_faults():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "demo" / "vm_test_targets.py"), "--list-faults"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0
    output = proc.stdout
    assert "cpu-hotspot" in output
    assert "memory-leak" in output
    assert "network-jitter" in output
