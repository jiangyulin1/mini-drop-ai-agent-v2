#!/usr/bin/env python3
"""Static acceptance gate for the AIOps benchmark workspace.

This script deliberately validates artifacts only. It never interprets a
missing trace as an agent failure and never claims a model result was run.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
COMPARISONS = ROOT / "comparisons"
sys.path.insert(0, str(ROOT))
from benchmark.native_audit import write_audit  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def check_json(path: Path, label: str, checks: list[dict]) -> dict | None:
    try:
        value = load(path)
    except (OSError, json.JSONDecodeError) as exc:
        checks.append({"name": label, "passed": False, "detail": str(exc)})
        return None
    checks.append({"name": label, "passed": True, "detail": digest(path)})
    return value


def main() -> int:
    checks: list[dict] = []
    testset = check_json(BENCHMARK / "testset-v1.json", "testset JSON", checks)
    contract = check_json(BENCHMARK / "agent-contract-v1.json", "contract JSON", checks)

    cases = testset.get("cases", []) if isinstance(testset, dict) else []
    expected_case_ids = [f"case-{index:02d}" for index in range(1, 10)]
    checks.append({
        "name": "nine cases and three repetitions",
        "passed": len(cases) == 9 and testset.get("repetitions") == 3 and [item.get("case_id") for item in cases] == expected_case_ids if isinstance(testset, dict) else False,
        "detail": {"case_count": len(cases), "repetitions": testset.get("repetitions") if isinstance(testset, dict) else None, "case_ids": [item.get("case_id") for item in cases]},
    })
    tools = contract.get("common_tools", []) if isinstance(contract, dict) else []
    checks.append({
        "name": "five common tools and prohibited raw export",
        "passed": len(tools) == 5 and contract.get("safety", {}).get("raw_export_allowed") is False if isinstance(contract, dict) else False,
        "detail": {"tool_count": len(tools)},
    })

    case_ids = [item.get("case_id") for item in cases if isinstance(item, dict)]
    public_dir = BENCHMARK / "cases" / "public"
    oracle_dir = BENCHMARK / "cases" / "private-oracles"
    intervention_dir = BENCHMARK / "interventions"
    public_files = [public_dir / f"{case_id}.json" for case_id in case_ids]
    oracle_files = [oracle_dir / f"{case_id}.json" for case_id in case_ids]
    interactive_ids = ["case-07", "case-08", "case-09"]
    intervention_files = [intervention_dir / f"{case_id}.json" for case_id in interactive_ids]
    checks.extend([
        {"name": "public case packs", "passed": bool(case_ids) and all(path.is_file() for path in public_files), "detail": [str(path.relative_to(ROOT)) for path in public_files if not path.is_file()]},
        {"name": "private oracles", "passed": bool(case_ids) and all(path.is_file() for path in oracle_files), "detail": [str(path.relative_to(ROOT)) for path in oracle_files if not path.is_file()]},
        {"name": "interactive intervention packs", "passed": len(intervention_files) == 3 and all(path.is_file() for path in intervention_files), "detail": [str(path.relative_to(ROOT)) for path in intervention_files if not path.is_file()]},
        {"name": "source lock", "passed": (BENCHMARK / "sources.lock.json").is_file(), "detail": "benchmark/sources.lock.json"},
        {"name": "common system prompt", "passed": (ROOT / "prompts" / "system-prompt-common.md").is_file(), "detail": "prompts/system-prompt-common.md"},
    ])

    # Public inputs must be anonymous and mechanism-neutral. This scan is
    # intentionally conservative: a leaked URL/commit makes the run invalid.
    public_files = list(public_dir.glob("case-*.json"))
    forbidden_public = re.compile(r"github\.com|/pull/|\b[0-9a-f]{40}\b|private-oracle|root_cause_text|fix_commit", re.I)
    leaked = []
    for path in public_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            leaked.append(str(path.relative_to(ROOT)))
            continue
        if forbidden_public.search(text):
            leaked.append(str(path.relative_to(ROOT)))
    checks.append({"name": "public case de-identification", "passed": len(public_files) == 9 and not leaked, "detail": leaked})

    source_lock = None
    try:
        source_lock = load(BENCHMARK / "sources.lock.json")
    except (OSError, json.JSONDecodeError):
        pass
    source_rows = source_lock.get("sources", []) if isinstance(source_lock, dict) else []
    patch_mismatches = []
    for row in source_rows:
        patch_path = BENCHMARK / "sources" / "patches" / f"{row.get('case_id')}.patch"
        if not patch_path.is_file() or digest(patch_path) != row.get("patch_sha256"):
            patch_mismatches.append(row.get("case_id"))
    checks.append({
        "name": "source lock provenance",
        "passed": len(source_rows) == 9 and not patch_mismatches and all(row.get("retrieval_status") == "fetched" and row.get("head_sha") and row.get("patch_sha256") and row.get("patch_bytes", 0) > 0 for row in source_rows),
        "detail": {"rows": len(source_rows), "unfetched": [row.get("case_id") for row in source_rows if row.get("retrieval_status") != "fetched"], "patch_mismatches": patch_mismatches},
    })

    try:
        sys.path.insert(0, str(ROOT))
        from benchmark.replay import ReplayService
        replay_ok = []
        for case_id in case_ids:
            ReplayService(BENCHMARK, case_id)
            replay_ok.append(case_id)
        replay_detail = replay_ok
    except Exception as exc:
        replay_detail = str(exc)
        replay_ok = []
    checks.append({"name": "replay packs and integrity hashes", "passed": len(replay_ok) == 9, "detail": replay_detail})

    adapters_dir = BENCHMARK / "adapters"
    adapter_ids = ["mini-drop", "holmesgpt", "smolagents", "itops-agent-platform", "k8sgpt"]
    missing_adapters = [agent_id for agent_id in adapter_ids if not (adapters_dir / agent_id / "adapter-manifest.json").is_file()]
    checks.append({"name": "adapter manifests", "passed": not missing_adapters, "detail": missing_adapters})

    frozen_path = BENCHMARK / "frozen-hashes.json"
    frozen_ok = False
    frozen_detail: object = "missing"
    try:
        frozen = load(frozen_path)
        frozen_ok = all(item.get("sha256") == digest(ROOT / item["path"]) for item in (frozen.get("files") or {}).values()) and len(frozen.get("files") or {}) >= 4
        frozen_detail = frozen.get("files", {})
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        pass
    checks.append({"name": "frozen prompt and schema hashes", "passed": frozen_ok, "detail": frozen_detail})

    protocol_passed = all(item["passed"] for item in checks)
    def collect_run_checks(root: Path, native: bool = False) -> dict:
        run_manifests = list(root.glob("*/*/*/repeat-*/manifest.json"))
        checks = {"run_manifest_count": len(run_manifests), "completed_runs": 0, "complete_run_artifacts": 0}
        required = {"manifest.json", "input-hashes.json", "tool-trace.jsonl", "raw-agent-output.txt", "normalized-answer.json", "resource-usage.json", "score.json"}
        if native:
            required |= {"native-runtime.json", "native-trace.jsonl"}
        interactive_ids = {"case-07", "case-08", "case-09"}
        for manifest in run_manifests:
            try:
                metadata = load(manifest)
            except (OSError, json.JSONDecodeError):
                continue
            if metadata.get("status") == "completed":
                checks["completed_runs"] += 1
            names = {item.name for item in manifest.parent.iterdir()}
            has_required = required <= names
            if has_required and str(metadata.get("case_id")) in interactive_ids:
                intervention_lines = (manifest.parent / "interventions.jsonl").read_text(encoding="utf-8").splitlines()
                has_required = bool([line for line in intervention_lines if line.strip()])
            if has_required:
                checks["complete_run_artifacts"] += 1
        return checks

    thin_run_checks = collect_run_checks(BENCHMARK / "runs", native=False)
    native_run_checks = collect_run_checks(BENCHMARK / "runs-native", native=True)
    native_audit = write_audit()
    run_checks = {
        "thin_adapter": thin_run_checks,
        "native": native_run_checks,
    }
    execution_passed = thin_run_checks["completed_runs"] > 0 and thin_run_checks["completed_runs"] == thin_run_checks["complete_run_artifacts"]

    if protocol_passed and execution_passed:
        status = "PARTIAL"
        reason = "Static protocol and at least one complete run are present. Use the final acceptance prompt to verify comparable coverage before declaring ACCEPTED."
    elif protocol_passed:
        status = "BLOCKED"
        reason = "Protocol artifacts are complete, but no complete measured Agent run is present."
    else:
        status = "BLOCKED"
        reason = "Required case, Oracle, intervention, source-lock, adapter, or run artifacts are incomplete. No comparison result may be claimed."

    payload = {
        "schema": "mini-drop.benchmark.static-acceptance.v1",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "reason": reason,
        "protocol_acceptance": protocol_passed,
        "execution_acceptance": execution_passed,
        "checks": checks,
        "run_checks": run_checks,
        "native_mainboard": {
            "status": "READY" if native_audit.get("mainboard_strict_comparable_runs") == 108 else "NOT_COMPARABLE",
            "artifact_runs": native_audit.get("mainboard_artifact_runs", 0),
            "strict_comparable_runs": native_audit.get("mainboard_strict_comparable_runs", 0),
            "measured_score_valid_runs": native_audit.get("mainboard_measured_score_valid_runs", 0),
            "reason": "Native artifacts exist, but strict comparability requires the native audit gate to pass.",
        },
        "native_audit": {
            "path": "comparisons/NATIVE_AUDIT.json",
            "cohort_reasons": native_audit.get("cohort_reasons", []),
            "k8s_real_cluster_runs": native_audit.get("k8s_strict_real_cluster_runs", 0),
        },
        "note": "This is a static artifact gate, not a measured Agent ability result. Thin-adapter runs are appendix only; native artifacts still require the strict audit before cross-agent comparison.",
    }
    COMPARISONS.mkdir(parents=True, exist_ok=True)
    output = COMPARISONS / "STATIC_ACCEPTANCE.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "protocol_acceptance": protocol_passed, "execution_acceptance": execution_passed, "thin_adapter": thin_run_checks, "native": native_run_checks}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
