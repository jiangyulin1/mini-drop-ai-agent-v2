#!/usr/bin/env python3
"""Score one compact native replay track without exposing private oracles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--track", required=True, choices=["fair_same_data", "expert_intervention_tuning"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    oracles = {item["case_id"]: item for item in load(root / "private/oracles.json")["cases"]}
    rows = []
    for case_id in [f"case-{i:02d}" for i in range(1, 31)]:
        run_dirs = list(args.run_root.glob(f"**/{case_id}/repeat-1"))
        row = {"case_id": case_id, "track": args.track, "status": "missing"}
        if not run_dirs:
            rows.append(row)
            continue
        run_dir = run_dirs[0]
        manifest = load(run_dir / "manifest.json")
        row["status"] = manifest.get("status", "unknown")
        row["run_id"] = manifest.get("run_id")
        row["exit_reason"] = manifest.get("exit_reason")
        try:
            answer = load(run_dir / "normalized-answer.json")
        except (OSError, json.JSONDecodeError):
            answer = {}
        truth = oracles[case_id]["accepted_answers"][0]
        mechanism = str(answer.get("mechanism") or "").lower()
        keywords = [str(k).lower() for k in truth.get("mechanism_keywords") or []]
        refs = set(answer.get("supporting_evidence") or [])
        required = set(truth.get("required_evidence") or [])
        row["root_location_match"] = answer.get("root_location") == truth.get("root_location")
        row["mechanism_keyword_match"] = sum(k in mechanism for k in keywords) >= max(1, min(2, len(keywords)))
        row["required_evidence_coverage"] = round(len(refs & required) / len(required), 3) if required else 1.0
        row["correct_abstention"] = bool(answer.get("abstain")) == bool(oracles[case_id].get("abstention", {}).get("required"))
        row["injection_manifest"] = (run_dir / "injection-manifest.json").is_file()
        row["injected_evidence_count"] = 0
        if row["injection_manifest"]:
            row["injected_evidence_count"] = len(load(run_dir / "injection-manifest.json").get("records") or [])
        row["intervention_event_count"] = sum(1 for line in (run_dir / "interventions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()) if (run_dir / "interventions.jsonl").is_file() else 0
        row["eligible"] = row["status"] == "completed" and row["root_location_match"] and row["mechanism_keyword_match"] and row["required_evidence_coverage"] == 1.0 and row["correct_abstention"] and row["injection_manifest"]
        if args.track == "expert_intervention_tuning":
            row["expert_intervention_observed"] = row["intervention_event_count"] >= 1
            row["eligible"] = row["eligible"] and row["expert_intervention_observed"]
        rows.append(row)
    summary = {
        "schema": "mini-drop.expert-intervention-fair-score.v1",
        "track": args.track,
        "case_count": 30,
        "completed_count": sum(r["status"] == "completed" for r in rows),
        "agent_error_count": sum(r["status"] == "agent_error" for r in rows),
        "eligible_count": sum(bool(r.get("eligible")) for r in rows),
        "injection_manifest_count": sum(bool(r.get("injection_manifest")) for r in rows),
        "intervention_observed_count": sum(bool(r.get("expert_intervention_observed")) for r in rows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("track", "case_count", "completed_count", "agent_error_count", "eligible_count", "injection_manifest_count", "intervention_observed_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
