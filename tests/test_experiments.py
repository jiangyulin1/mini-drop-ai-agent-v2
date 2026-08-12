from __future__ import annotations

import json
from pathlib import Path

from scripts.run_lightweight_ai_eval import load_manifest, write_experiment_manifest
from server.app.diagnosis.experiments import (
    ExperimentSpec,
    build_run_manifest,
    manifest_fingerprint,
)


def test_run_manifest_hashes_inputs_without_environment_secrets(tmp_path: Path, monkeypatch):
    source = tmp_path / "input.json"
    source.write_text('{"case":"safe"}\n', encoding="utf-8")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "must-not-appear")
    spec = ExperimentSpec(
        experiment_id="exp-1",
        dataset="dataset",
        dataset_version="1.0",
        profile="smoke",
        reasoner_id="rules_only",
        reasoner_version="rules-only.v1",
        rule_version="rules.v1",
        feature_version="features.v1",
        planner_version="planner.v1",
        toolset_version="tools.v1",
    )

    first = build_run_manifest(spec, files=[source], repository_root=tmp_path)
    second = build_run_manifest(spec, files=[source], repository_root=tmp_path)

    assert first["inputs"]["input.json"]
    assert "must-not-appear" not in json.dumps(first)
    assert manifest_fingerprint(first) == manifest_fingerprint(second)


def test_lightweight_runner_writes_immutable_experiment_manifest(tmp_path: Path):
    manifest = load_manifest()
    profile = manifest["profiles"]["smoke"]

    path = write_experiment_manifest(
        manifest,
        profile_name="smoke",
        profile=profile,
        output_dir=tmp_path,
        run_id="test-run",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["experiment"]["reasoner_id"] == "rules_only"
    assert payload["experiment"]["dataset_version"] == manifest["version"]
    assert payload["fingerprint"]
    assert (tmp_path / "run_manifest.json").exists()
