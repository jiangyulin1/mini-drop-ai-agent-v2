from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.run_chaos_gym import (
    evaluate_offline,
    run_offline,
    target_service_id,
    validate_manifest,
)

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


def test_scope_and_ground_truth_share_a_namespace():
    """The live runner must register the scope under the ground-truth id.

    It previously hardcoded ``chaos-gym-target`` while manifests asserted
    ``worker1:fib``.  Self-inflicted faults resolve to the target service id,
    so that comparison could never be true regardless of model quality.
    """
    for path in sorted((ROOT / "chaos_gym" / "manifests").glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert target_service_id(manifest) == manifest["root_cause_entity"]


def test_code_path_hit_reads_the_report_text():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = {
        "conclusion": {"root_cause_entity": "worker1:fib", "evidence_refs": ["ev-1"]},
        "evidence": ["ev-1"],
        "probes": ["perf_cpu", "sys_metrics"],
    }
    named = dict(base)
    named["conclusion"] = {
        **base["conclusion"],
        "report_text": "热点为递归 fib(27) 循环，纯用户态计算。",
    }
    assert evaluate_offline(manifest, named)["code_path_hit"] is True
    # Locating the service without naming the code path is a partial result,
    # and must not be silently credited as one.
    assert evaluate_offline(manifest, base)["code_path_hit"] is False


def test_missing_attribution_is_not_a_hit():
    """Guard the scorer itself: an empty conclusion must never score."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evaluation = evaluate_offline(manifest, {"conclusion": {}, "evidence": [], "probes": []})
    assert evaluation["strict_rca_hit"] is False
    assert evaluation["citation_valid"] is False
    assert evaluation["required_probe_recall"] == 0.0
    assert evaluation["pareto_score"] == 0.0


def test_citations_must_reference_collected_evidence():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fabricated = {
        "conclusion": {"root_cause_entity": "worker1:fib", "evidence_refs": ["ev-does-not-exist"]},
        "evidence": ["ev-1"],
        "probes": ["perf_cpu", "sys_metrics"],
    }
    assert evaluate_offline(manifest, fabricated)["citation_valid"] is False


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
