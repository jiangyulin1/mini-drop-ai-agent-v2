from __future__ import annotations

from pathlib import Path

from scripts.run_lightweight_ai_eval import DATASET, load_manifest, validate_manifest
from server.app.diagnosis.eval_harness import load_scenarios, run_evaluation


def test_lightweight_manifest_and_profiles_are_consistent():
    manifest = load_manifest()

    assert validate_manifest(manifest) == []
    assert manifest["profiles"]["vm-smoke"]["mode"] == "vm"
    assert manifest["profiles"]["vm-release"]["repetitions"] == 3


def test_lightweight_cases_cover_counterfactual_robust_and_compound_tracks():
    scenarios = load_scenarios(DATASET / "scenarios")
    ids = {item["scenario_id"] for item in scenarios}

    assert len(scenarios) >= 12
    assert {
        "robust_timeout_is_not_connectivity",
        "counterfactual_neighbor_not_self",
        "counterfactual_self_not_neighbor",
        "compound_disk_and_network",
        "healthy_baseline_no_fault",
    }.issubset(ids)


def test_lightweight_smoke_profile_passes():
    manifest = load_manifest()
    profile = manifest["profiles"]["smoke"]
    report = run_evaluation(
        Path(DATASET / "scenarios"),
        scenario_ids=set(profile["scenario_ids"]),
        suite="test/lightweight-smoke",
    )

    assert report["failed"] == 0
    assert report["reasoner"] == {
        "strategy_id": "rules_only",
        "strategy_version": "rules-only.v1",
    }
    assert report["metrics"]["classification_accuracy"] == 1.0
    assert report["metrics"]["root_location_accuracy"] == 1.0
    assert report["metrics"]["domain_cause_accuracy"] == 1.0
    assert report["metrics"]["evidence_reference_integrity"] == 1.0
    assert report["metrics"]["unsafe_auto_execute_count"] == 0


def test_all_lightweight_scenarios_pass():
    report = run_evaluation(
        Path(DATASET / "scenarios"),
        suite="test/lightweight-quick",
    )

    assert report["total"] >= 12
    assert report["failed"] == 0
