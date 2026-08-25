#!/usr/bin/env python3
"""Build an anonymous judge-AI package for the 30-case suite.

Only bounded public case material and candidate event views enter ``input``.
Oracle truth, identity mapping and machine-gate reports stay under ``private``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_AGENT_SOURCE = "651c450867c4d6db26cc78de5928bb14f7b3c3b9"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def anonymize_text(value: str) -> str:
    value = value.replace("mini-drop", "benchmark-agent")
    value = value.replace("Mini-Drop", "Benchmark Agent")
    value = value.replace("deepseek-v4-flash", "model-redacted")
    value = re.sub(r"651c450867c4d6db26cc78de5928bb14f7b3c3b9", "SOURCE_REDACTED", value)
    return value


def redact(value: Any, internal_case_id: str) -> Any:
    if isinstance(value, dict):
        return {k: redact(v, internal_case_id) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, internal_case_id) for v in value]
    if isinstance(value, str):
        value = value.replace(internal_case_id, "CASE_INTERNAL_REDACTED")
        value = re.sub(r"case_[0-9]{8}_[a-z0-9]+", "CASE_INTERNAL_REDACTED", value)
        return anonymize_text(value)
    return value


def get_events(base_url: str, api_key: str, case_id: str) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/v1/cases/{case_id}/events"
    request = urllib.request.Request(url, headers={"X-API-Key": api_key})
    context = ssl._create_unverified_context() if base_url.startswith("https://") else None
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    items = ((payload.get("data") or {}).get("items") or []) if isinstance(payload, dict) else []
    return [redact(item, case_id) for item in items if isinstance(item, dict)]


def candidate_id(source_sha: str) -> str:
    return "CAND-" + hashlib.sha256(source_sha.encode()).hexdigest()[:8].upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "blind-review")
    parser.add_argument("--injection-aggregate", type=Path, default=ROOT.parent.parent / "reports" / "evaluation" / "expert-intervention-fair-30" / "injection-aggregate.json")
    parser.add_argument("--control-url", default=os.getenv("MINI_DROP_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("MINI_DROP_API_KEY", ""))
    parser.add_argument("--source-sha", default=DEFAULT_AGENT_SOURCE)
    args = parser.parse_args()
    if not args.control_url or not args.api_key:
        raise SystemExit("MINI_DROP_BASE_URL and MINI_DROP_API_KEY are required to rebuild candidate event views")

    output = args.output.resolve()
    input_root = output / "input"
    private_root = output / "private"
    for path in (input_root, private_root):
        path.mkdir(parents=True, exist_ok=True)

    # Public case material is shared across candidates and has no private truth.
    for case_id in (f"case-{i:02d}" for i in range(1, 31)):
        case_input = input_root / "cases" / case_id
        case_input.mkdir(parents=True, exist_ok=True)
        for name in ("public", "replay"):
            source = (ROOT / "cases" / name / f"{case_id}.json").read_text(encoding="utf-8")
            (case_input / f"{name}.json").write_text(anonymize_text(source), encoding="utf-8")
        source = (ROOT / "interventions" / f"{case_id}.json").read_text(encoding="utf-8")
        (case_input / "intervention.json").write_text(anonymize_text(source), encoding="utf-8")
    (input_root / "JUDGE_METHOD.md").write_text(anonymize_text((ROOT / "BLIND_JUDGE_METHOD.md").read_text(encoding="utf-8")), encoding="utf-8")
    (input_root / "common").mkdir(parents=True, exist_ok=True)
    contract_source = (ROOT.parent / "multi-agent-native-comparison-20260821" / "benchmark" / "agent-contract-v1.json").read_text(encoding="utf-8")
    (input_root / "common" / "agent-contract-v1.json").write_text(anonymize_text(contract_source), encoding="utf-8")
    prompt_source = (ROOT.parent / "multi-agent-native-comparison-20260821" / "prompts" / "system-prompt-common.md").read_text(encoding="utf-8")
    (input_root / "common" / "system-prompt-common.md").write_text(anonymize_text(prompt_source), encoding="utf-8")

    aggregate = load(args.injection_aggregate)
    rows = aggregate.get("runs") or []
    mapping = {(str(item.get("track")), str(item.get("case_id"))): str((item.get("records") or [{}])[0].get("canonical_evidence_id", "")).split(":", 2)[1] for item in rows if item.get("records")}
    cid = candidate_id(args.source_sha)
    candidate_root = input_root / "candidates" / cid
    run_index = []
    for track in ("fair_same_data", "expert_intervention_tuning"):
        for case_id in (f"case-{i:02d}" for i in range(1, 31)):
            internal_id = mapping.get((track, case_id))
            if not internal_id:
                continue
            events = get_events(args.control_url, args.api_key, internal_id)
            run_root = candidate_root / track / case_id / "repeat-1"
            dump(run_root / "event-view.json", {"case_id": case_id, "track": track, "events": events})
            injection_view = json.dumps(next((item for item in rows if item.get("track") == track and item.get("case_id") == case_id), {}), ensure_ascii=False, indent=2)
            (run_root / "injection-view.json").write_text(anonymize_text(injection_view) + "\n", encoding="utf-8")
            run_index.append({"candidate_id": cid, "track": track, "case_id": case_id, "repeat": "repeat-1", "event_count": len(events), "execution_status": "completed" if any(e.get("event_type") == "turn.completed" for e in events) else "unclear"})
    # Packet manifests contain only allowed paths; judges read the referenced
    # anonymous views in fresh contexts. No score or private oracle is copied.
    for case_id in (f"case-{i:02d}" for i in range(1, 31)):
        for track, source_track in (("A", "fair_same_data"), ("B1", "expert_intervention_tuning"), ("C", "fair_same_data")):
            for jury_no in range(1, 4):
                packet = {
                    "schema": "blind-review.jury-packet.v1",
                    "jury_id": f"{track}-{case_id}-jury-{jury_no}",
                    "track": track,
                    "case_id": case_id,
                    "candidate_id": cid,
                    "repeat_order": ["repeat-1"],
                    "allowed_paths": [
                        f"cases/{case_id}/public.json",
                        f"cases/{case_id}/replay.json",
                        f"cases/{case_id}/intervention.json",
                        f"candidates/{cid}/{source_track}/{case_id}/repeat-1/event-view.json",
                        f"candidates/{cid}/{source_track}/{case_id}/repeat-1/injection-view.json",
                    ],
                    "oracle_access": False,
                    "prior_scores_access": False,
                }
                dump(output / "jury-packets" / track / case_id / cid / f"jury-{jury_no}.json", packet)
    for jury_no in range(1, 4):
        dump(output / "jury-packets" / "product" / cid / f"jury-{jury_no}.json", {
            "schema": "blind-review.product-jury-packet.v1",
            "jury_id": f"product-{cid}-jury-{jury_no}",
            "track": "product",
            "candidate_id": cid,
            "allowed_root": f"candidates/{cid}",
            "allowed_case_material": "cases",
            "oracle_access": False,
            "prior_scores_access": False,
            "protocol_note": "B2 interaction types must be described and not forced into one cross-type ranking.",
        })
    dump(output / "case-audit" / "STATUS.json", {
        "schema": "blind-review.case-evidence-ceilings-status.v1",
        "status": "PENDING",
        "case_count": 30,
        "required_output": "case-evidence-ceilings.json",
        "rule": "Oracle claims not derivable from public replay evidence must become null/not_comparable, not a failure.",
    })
    dump(private_root / "candidate-registry.json", {"schema": "blind-review.private-candidate-registry.v1", "candidates": [{"candidate_id": cid, "agent_id": "mini-drop", "source_sha": args.source_sha}]})
    shutil.copy2(ROOT / "private" / "oracles.json", private_root / "oracles.json")
    dump(private_root / "run-index.json", {"schema": "blind-review.run-index.v1", "runs": run_index})
    dump(output / "STATUS.json", {"status": "PARTIAL_PENDING_CASE_AUDIT_AND_BALLOTS", "reason": "Anonymous packets are prepared; independent judge-AI ballots and arbitration have not been run."})
    dump(output / "manifest.json", {"schema": "mini-drop.blind-review-30.v1", "suite_id": "expert-intervention-fair-30", "candidate_count": 1, "candidate_ids": [cid], "tracks": ["fair_same_data", "expert_intervention_tuning"], "jury_tracks": ["A", "B1", "C", "product"], "cases": 30, "repeats": 1, "packet_counts": {"A": 90, "B1": 90, "C": 90, "product": 3}, "scoring": "judge-ai-0-to-4-with-null-and-arbitration", "machine_gate_is_not_capability_score": True, "missing_30_case_candidates": "Other agents have no 30-case native run material in the current workspace; no placeholder scores were created."})
    print(json.dumps({"output": str(output), "candidate_id": cid, "runs": len(run_index), "cases": 30}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
