"""Validate the local Agent Beta suite skeleton.

This validator intentionally does NOT score or certify blind evaluation.  It
only checks that the normative public contract, required conformance fixtures
and source-lock file are present and internally consistent before a run can
be attempted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reproducible_text import canonical_text_sha256

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "benchmarks" / "agent_beta"
CONTRACT = BETA / "contracts" / "public-contract-v1.json"
SCHEMA = BETA / "schemas" / "public-contract-v1.schema.json"
SOURCES = BETA / "sources.lock.json"
PROMPT = ROOT / "docs" / "ai_agent_feature_complete_demo_prompt_v6.md"
PUBLIC_V2 = BETA / "manifests" / "public-v2.yaml"
PUBLIC_V3 = BETA / "manifests" / "public-v3.json"
PUBLIC_V3_LOCK = BETA / "manifests" / "public-v3.lock.json"

REQUIRED_FIXTURES = [
    "wrong-primary",
    "fake-evidence",
    "no-native-task",
    "oracle-leak",
    "cleanup-failure",
    "answer-starts-investigation",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-verified-sources", action="store_true",
                        help="fail when sources.lock.json has unverified digests")
    parser.add_argument("--manifest", type=Path, default=PUBLIC_V2)
    parser.add_argument("--public-v3-manifest", type=Path, default=PUBLIC_V3)
    args = parser.parse_args()

    if not CONTRACT.exists():
        fail(f"missing {CONTRACT.relative_to(ROOT)}")
    if not PROMPT.exists():
        fail(f"missing prompt {PROMPT.relative_to(ROOT)}")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    expected_hash = canonical_text_sha256(PROMPT)
    if contract.get("prompt_sha256") != expected_hash:
        fail(f"contract prompt_sha256 mismatch: {contract.get('prompt_sha256')} != {expected_hash}")

    if SCHEMA.exists():
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        try:
            import jsonschema
        except ImportError:
            print("WARN: jsonschema not installed; schema validation skipped")
        else:
            jsonschema.validate(contract, schema)
            print("PASS: public-contract-v1 matches schema")
    else:
        fail(f"missing {SCHEMA.relative_to(ROOT)}")

    req_ids = {item["id"] for item in contract["requirements"]}
    expected_reqs = {f"D{i:02d}" for i in range(1, 21)}
    if req_ids != expected_reqs:
        fail(f"requirement set drift: missing={sorted(expected_reqs - req_ids)}, extra={sorted(req_ids - expected_reqs)}")
    covered = set()
    for case in contract["cases"]:
        covered.update(case["requirements"])
    if covered != expected_reqs:
        fail(f"public cases do not cover all requirements: missing={sorted(expected_reqs - covered)}")
    case_ids = {item["id"] for item in contract["cases"]}
    if case_ids != {f"P{i:02d}" for i in range(1, 13)}:
        fail(f"public case set drift: {sorted(case_ids)}")

    holdout = contract.get("holdout_slots") or []
    holdout_ids = [item["id"] for item in holdout]
    if len(holdout) < 20:
        fail(f"holdout has {len(holdout)} slots, expected at least 20")
    for required in ["H01", "H02", "H03", "H04", "H05", "H06", "H07", "H08",
                     "H09", "H10", "H11", "H12", "H13", "H14", "H15", "H16",
                     "H17", "H18a", "H18b", "H19"]:
        if required not in holdout_ids:
            fail(f"missing holdout slot {required}")
    if "H18a" not in holdout_ids or "H18b" not in holdout_ids:
        fail("H18a/H18b must be independent required slots")

    for name in REQUIRED_FIXTURES:
        path = BETA / "conformance" / f"{name}.json"
        if not path.exists():
            fail(f"missing conformance fixture {path.relative_to(ROOT)}")
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture.get("expected_verdict") != "MUST_FAIL":
            fail(f"conformance fixture {name} must be MUST_FAIL")
    print("PASS: 6 mandatory negative conformance fixtures present")

    if not SOURCES.exists():
        fail(f"missing {SOURCES.relative_to(ROOT)}")
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    for source in sources.get("sources") or []:
        if source.get("decision") == "REQUIRED" and not source.get("planned_commit"):
            fail(f"required source {source.get('id')} has no commit lock")
        if args.require_verified_sources:
            if source.get("decision") == "REQUIRED" and not source.get("digest_verified"):
                fail(f"required source {source.get('id')} digest not verified")
    if not args.manifest.exists():
        fail(f"missing public manifest {args.manifest.relative_to(ROOT)}")
    try:
        import yaml
    except ImportError:
        print("WARN: PyYAML not installed; public-v2 manifest content not validated")
    else:
        manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
        ids = {item["id"] for item in manifest["cases"]}
        if ids != {f"P{i:02d}" for i in range(1, 11)}:
            fail(f"public-v2 case set drift: {sorted(ids)}")
        ux = set(manifest.get("ux_assertions") or {})
        expected_ux = {f"UX{i:02d}" for i in range(1, 21)}
        if ux != expected_ux:
            fail(f"public-v2 UX assertion set drift: missing={sorted(expected_ux - ux)}")
        print("PASS: public-v2 P01-P10 and UX01-UX20 manifest validated")

    v3_path = args.public_v3_manifest.resolve()
    if not v3_path.exists():
        fail(f"missing public-v3 manifest {v3_path}")
    public_v3 = json.loads(v3_path.read_text(encoding="utf-8"))
    scenarios = public_v3.get("mandatory_scenarios") or []
    scenario_ids = {item.get("id") for item in scenarios}
    if scenario_ids != {f"M{i:02d}" for i in range(1, 14)}:
        fail(f"public-v3 mandatory scenario drift: {sorted(scenario_ids)}")
    expected_names = {
        "standalone_drop",
        "answer_only_zero_side_effects",
        "existing_artifact_explanation",
        "three_round_evidence_wakeup_decision",
        "pause_resume_stop_retarget",
        "sidecar_server_worker_restart",
        "homogeneous_and_heterogeneous_campaign",
        "collector_query_mcp_lineage",
        "compound_causal_with_distractor",
        "evidence_exclude_downgrades_conclusion",
        "provider_pi_mcp_independent_failures",
        "browser_refresh_sse_replay",
        "candidate_fault_cleanup_reality",
    }
    names = {item.get("name") for item in scenarios}
    if names != expected_names:
        fail(f"public-v3 mandatory names drift: missing={sorted(expected_names - names)}")
    for item in scenarios:
        runners = bool(item.get("pytest")) + bool(item.get("runner"))
        if runners != 1:
            fail(f"{item.get('id')} must define exactly one executable assertion runner")
    holdout_status = (public_v3.get("external_holdout") or {}).get("status")
    if holdout_status != "AWAITING_EXTERNAL_HOLDOUT":
        fail(f"external holdout status must be AWAITING_EXTERNAL_HOLDOUT, got {holdout_status!r}")

    if not PUBLIC_V3_LOCK.exists():
        fail(f"missing {PUBLIC_V3_LOCK.relative_to(ROOT)}")
    public_lock = json.loads(PUBLIC_V3_LOCK.read_text(encoding="utf-8"))
    expected_lock_paths = {
        "prompt": PROMPT,
        "source_lock": SOURCES,
        "manifest": v3_path,
        "runner": ROOT / public_v3["runner_file"],
    }
    for name, path in expected_lock_paths.items():
        expected = canonical_text_sha256(path)
        actual = (public_lock.get("canonical_text_sha256") or {}).get(name)
        if actual != expected:
            fail(f"public-v3 {name} canonical digest mismatch: {actual} != {expected}")
    print("PASS: public-v3 M01-M13 executable manifest and newline-stable lock validated")
    print("PASS: suite skeleton validated (no blind-eval claim)")
    print("PASS: EvaluationTrustLevel remains AWAITING_EXTERNAL_HOLDOUT")


if __name__ == "__main__":
    main()
