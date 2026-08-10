#!/usr/bin/env python3
"""Score repeated Linux VM diagnosis runs without exposing private oracle data."""

from __future__ import annotations

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker


STABILITY_THRESHOLD = 0.8


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_contract_sha256(path: Path) -> str:
    manifest = _load(path)
    manifest.pop("status", None)
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _candidate_key(candidate: dict[str, Any]) -> str:
    entity = str(candidate.get("root_entity") or "").strip().lower()
    return "|".join((
        str(candidate.get("root_location") or "").strip().lower(),
        str(candidate.get("domain_cause") or "").strip().lower(),
        entity,
    ))


def _matches(candidate: dict[str, Any], truth: dict[str, Any]) -> bool:
    for field in ("root_location", "domain_cause"):
        if str(candidate.get(field) or "").lower() != str(truth.get(field) or "").lower():
            return False
    expected_entity = str(truth.get("root_entity") or "").strip().lower()
    if expected_entity:
        return str(candidate.get("root_entity") or "").strip().lower() == expected_entity
    return True


def _mean_pairwise_jaccard(candidate_sets: list[set[str]]) -> float:
    if len(candidate_sets) < 2:
        return 1.0
    scores: list[float] = []
    for left, right in combinations(candidate_sets, 2):
        union = left | right
        scores.append(len(left & right) / len(union) if union else 1.0)
    return sum(scores) / len(scores)


def score_case(
    manifest_path: Path,
    ground_truth_path: Path,
    runs_dir: Path,
    schema_path: Path,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    gt_document = _load(ground_truth_path)
    schema = _load(schema_path)
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    case_id = str(manifest.get("case_id") or "")
    reasons: list[str] = []
    if gt_document.get("case_id") != case_id:
        reasons.append("GROUND_TRUTH_CASE_MISMATCH")
    if not gt_document.get("started_at") or not gt_document.get("ended_at"):
        reasons.append("GROUND_TRUTH_TIME_RANGE_INCOMPLETE")
    truth = gt_document.get("ground_truth") or {}
    expected = manifest.get("expected") or {}
    for field in ("root_location", "domain_cause", "root_entity"):
        if expected.get(field) is not None and truth.get(field) != expected.get(field):
            reasons.append(f"GROUND_TRUTH_MANIFEST_MISMATCH:{field}")

    run_results: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    candidate_sets: list[set[str]] = []
    for path in sorted(runs_dir.glob("run-*.json")):
        try:
            run = _load(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            run_results.append({"file": path.name, "valid": False, "errors": [str(exc)]})
            continue
        errors = []
        for error in sorted(validator.iter_errors(run), key=lambda item: list(item.path)):
            location = ".".join(str(item) for item in error.path) or "<root>"
            errors.append(f"{location}: {error.message}")
        if run.get("case_id") != case_id:
            errors.append("CASE_ID_MISMATCH")
        run_id = str(run.get("run_id") or "")
        if run_id in seen_run_ids:
            errors.append("DUPLICATE_RUN_ID")
        seen_run_ids.add(run_id)
        if errors:
            run_results.append({"file": path.name, "run_id": run_id, "valid": False, "errors": errors})
            continue

        candidates = (run.get("diagnosis") or {}).get("candidates") or []
        top3 = candidates[:3]
        candidate_sets.append({_candidate_key(item) for item in top3})
        required_collectors = set((manifest.get("capture") or {}).get("collectors") or [])
        captured_collectors = set((run.get("capture") or {}).get("collectors") or [])
        timing = run["timing"]
        diagnosis = run["diagnosis"]
        recovery = run["recovery"]
        evidence_domains = {
            str(item.get("evidence_domain")) for item in diagnosis.get("evidence_refs") or []
        }
        expected_domains = set(expected.get("evidence_domains") or [])
        checks = {
            "top1_hit": _matches(candidates[0], truth),
            "top3_hit": any(_matches(item, truth) for item in top3),
            "required_collectors_captured": required_collectors <= captured_collectors,
            "evidence_domains_covered": expected_domains <= evidence_domains,
            "within_diagnosis_budget": float(timing["diagnosis_duration_sec"]) <= float(
                manifest["performance_requirements"]["max_diagnosis_sec"],
            ),
            "no_unauthorized_actions": int(diagnosis["unauthorized_actions"]) == 0,
            "oracle_isolated": run["oracle_accessed"] is False,
            "fault_injected": run["fault_injected"] is True,
            "linux_two_node": run["environment"]["linux"] is True
            and int(run["environment"]["node_count"]) >= 2,
            "recovery_verified": recovery["fault_reverted"] is True
            and recovery["health_verified"] is True,
        }
        run_results.append({
            "file": path.name,
            "run_id": run_id,
            "valid": True,
            "checks": checks,
            "eligible_run": all(checks.values()),
            "top3_candidates": sorted(candidate_sets[-1]),
        })

    valid = [item for item in run_results if item.get("valid")]
    required_repetitions = int(manifest["performance_requirements"]["repetitions"])
    stability = _mean_pairwise_jaccard(candidate_sets)
    eligible_runs = [item for item in valid if item.get("eligible_run")]
    if len(valid) < required_repetitions:
        reasons.append(f"INSUFFICIENT_VALID_REPETITIONS:{len(valid)}/{required_repetitions}")
    if len(eligible_runs) < required_repetitions:
        reasons.append(f"INSUFFICIENT_ELIGIBLE_REPETITIONS:{len(eligible_runs)}/{required_repetitions}")
    if stability < STABILITY_THRESHOLD:
        reasons.append(f"STABILITY_BELOW_THRESHOLD:{stability:.3f}/{STABILITY_THRESHOLD:.3f}")
    for item in valid:
        failed = [name for name, passed in item["checks"].items() if not passed]
        if failed:
            reasons.append(f"RUN_FAILED:{item['run_id']}:{','.join(failed)}")
    invalid = [item for item in run_results if not item.get("valid")]
    if invalid:
        reasons.append(f"INVALID_RUN_FILES:{len(invalid)}")
    reasons = list(dict.fromkeys(reasons))
    return {
        "schema_version": "mini-drop.testset-score.v1",
        "case_id": case_id,
        "manifest_contract_sha256": _manifest_contract_sha256(manifest_path),
        "ground_truth_sha256": _sha256(ground_truth_path),
        "required_repetitions": required_repetitions,
        "valid_repetitions": len(valid),
        "eligible_repetitions": len(eligible_runs),
        "stability": round(stability, 6),
        "stability_threshold": STABILITY_THRESHOLD,
        "eligible_verified_vm": not reasons,
        "blocking_reasons": reasons,
        "runs": run_results,
    }


def promote_manifest(manifest_path: Path, report: dict[str, Any]) -> None:
    if not report.get("eligible_verified_vm"):
        raise ValueError("REPORT_NOT_ELIGIBLE_FOR_VERIFIED_VM")
    manifest = _load(manifest_path)
    if manifest.get("case_id") != report.get("case_id"):
        raise ValueError("REPORT_MANIFEST_CASE_MISMATCH")
    if _manifest_contract_sha256(manifest_path) != report.get("manifest_contract_sha256"):
        raise ValueError("REPORT_MANIFEST_HASH_MISMATCH")
    manifest["status"] = "verified_vm"
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    temporary.replace(manifest_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--runs-dir", required=True, type=Path)
    parser.add_argument(
        "--schema", type=Path,
        default=Path(__file__).resolve().parents[1] / "testsets" / "run-result.schema.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    report = score_case(
        args.manifest.resolve(), args.ground_truth.resolve(),
        args.runs_dir.resolve(), args.schema.resolve(),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    output_path = args.output
    if args.promote and output_path is None:
        output_path = args.manifest.resolve().parent.parent / "runs" / report["case_id"] / "score.json"
    if args.promote:
        promote_manifest(args.manifest.resolve(), report)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["eligible_verified_vm"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
