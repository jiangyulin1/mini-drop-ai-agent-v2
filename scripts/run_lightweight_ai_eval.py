#!/usr/bin/env python3
"""Run the packaged lightweight AI evaluation profiles."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATASET = ROOT / "benchmarks" / "lightweight_ai_eval"
MANIFEST = DATASET / "manifest.json"
SCENARIOS = DATASET / "scenarios"
VM_RUNNER = ROOT / "scripts" / "run_ai_ops_v2_vm.py"
AI_OPS_MANIFEST = ROOT / "benchmarks" / "ai_ops_v2" / "manifest.json"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scenario_payload = json.loads((DATASET / manifest["scenario_file"]).read_text(encoding="utf-8"))
    ids = [item.get("scenario_id") for item in scenario_payload]
    if len(ids) != len(set(ids)):
        errors.append("scenario_id must be unique")
    vm_case_ids = {
        item["case_id"]
        for item in json.loads(AI_OPS_MANIFEST.read_text(encoding="utf-8"))["cases"]
    }
    for profile_name, profile in manifest["profiles"].items():
        selected = profile.get("scenario_ids")
        if isinstance(selected, list):
            unknown = sorted(set(selected) - set(ids))
            if unknown:
                errors.append(f"{profile_name}: unknown scenario ids: {', '.join(unknown)}")
        case_ids = profile.get("case_ids")
        if isinstance(case_ids, list) and len(case_ids) != len(set(case_ids)):
            errors.append(f"{profile_name}: case_ids must be unique")
        if isinstance(case_ids, list):
            unknown = sorted(set(case_ids) - vm_case_ids)
            if unknown:
                errors.append(f"{profile_name}: unknown ai_ops_v2 case ids: {', '.join(unknown)}")
    return errors


def run_offline(profile_name: str, profile: dict[str, Any], output_dir: Path) -> int:
    from server.app.diagnosis.eval_harness import render_markdown, run_evaluation

    selected = profile.get("scenario_ids")
    scenario_ids = None if selected == "all" else set(selected or [])
    report = run_evaluation(
        SCENARIOS,
        scenario_ids=scenario_ids,
        suite=f"mini-drop-lightweight-ai-eval/{profile_name}",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "diagnosis_eval.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (output_dir / "diagnosis_eval.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "suite": report["suite"],
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "metrics": report["metrics"],
        "failed_scenarios": [
            item["scenario_id"] for item in report["results"] if not item["passed"]
        ],
        "report_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))
    if report["failed"]:
        return 2
    targets = list(profile.get("pytest_targets") or [])
    if not targets:
        return 0
    command = [sys.executable, "-m", "pytest", "-q", *targets]
    print("\nGuardrail tests:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def write_experiment_manifest(
    manifest: dict[str, Any],
    *,
    profile_name: str,
    profile: dict[str, Any],
    output_dir: Path,
    run_id: str | None,
) -> Path:
    from server.app.diagnosis.experiments import (
        ExperimentSpec,
        build_run_manifest,
        manifest_fingerprint,
    )
    from server.app.diagnosis.reasoner import DEFAULT_REASONER

    experiment_id = run_id or (
        f"{profile_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    spec = ExperimentSpec(
        experiment_id=experiment_id,
        dataset=manifest["dataset"],
        dataset_version=manifest["version"],
        profile=profile_name,
        reasoner_id=DEFAULT_REASONER.strategy_id,
        reasoner_version=DEFAULT_REASONER.strategy_version,
        repetitions=int(profile.get("repetitions", 1)),
        seed=int(profile.get("seed", 0)),
        rule_version="domain-analyzers.v2",
        feature_version="normalized-observation.v1",
        planner_version="diagnosis-orchestrator-v1",
        toolset_version="registered-tools.v1",
    )
    input_files = [MANIFEST, DATASET / manifest["scenario_file"]]
    if profile["mode"] == "vm":
        input_files.append(AI_OPS_MANIFEST)
    run_manifest = build_run_manifest(spec, files=input_files, repository_root=ROOT)
    run_manifest["fingerprint"] = manifest_fingerprint(run_manifest)
    registry_dir = output_dir / "run_manifests"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / f"{experiment_id}.json"
    serialized = json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    except FileExistsError as exc:
        raise SystemExit(f"experiment manifest already exists: {path}") from exc
    (output_dir / "run_manifest.json").write_text(serialized, encoding="utf-8")
    return path


def run_vm(profile_name: str, profile: dict[str, Any], output_dir: Path, *, resume: bool) -> int:
    if not os.getenv("MINI_DROP_VM_PASSWORD"):
        raise SystemExit("MINI_DROP_VM_PASSWORD is required for VM profiles")
    selected = profile.get("case_ids")
    command = [
        sys.executable,
        str(VM_RUNNER),
        "--repetitions", str(profile["repetitions"]),
        "--seed", str(profile["seed"]),
        "--output-dir", str(output_dir),
    ]
    if isinstance(selected, list):
        command.extend(["--cases", ",".join(selected)])
    if resume:
        command.append("--resume")
    print(f"VM profile {profile_name}:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    manifest = load_manifest()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(manifest["profiles"]), default="smoke")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    errors = validate_manifest(manifest)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.validate_only:
        print(f"OK: {manifest['dataset']} v{manifest['version']}")
        return 0

    profile = manifest["profiles"][args.profile]
    output_dir = args.output_dir or ROOT / "reports" / "eval" / "lightweight" / args.profile
    run_manifest_path = write_experiment_manifest(
        manifest,
        profile_name=args.profile,
        profile=profile,
        output_dir=output_dir,
        run_id=args.run_id,
    )
    print(f"Experiment manifest: {run_manifest_path}", flush=True)
    if profile["mode"] == "offline":
        return run_offline(args.profile, profile, output_dir)
    return run_vm(args.profile, profile, output_dir, resume=args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
