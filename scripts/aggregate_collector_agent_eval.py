#!/usr/bin/env python3
"""Aggregate repeated Collector Agent evaluations without overstating accuracy."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - spread), min(1.0, center + spread)]


def aggregate(
    scores: list[dict[str, Any]], traces: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for score in scores:
        arm = str(score["run"]["arm"])
        grouped[arm].append(score)
        trace = traces.get(str(score["run"]["run_id"]), {})
        telemetry = trace.get("traces") or []
        total_tokens = sum(
            int(item.get("telemetry", {}).get("tokens") or 0) for item in telemetry
        )
        total_time = sum(
            int(item.get("telemetry", {}).get("wall_time_ms") or 0)
            for item in telemetry
        )
        for case in score["cases"]:
            rows.append(
                {
                    "run_id": score["run"]["run_id"],
                    "arm": arm,
                    "case_id": case["case_id"],
                    "sufficiency": int(case["evidence_sufficiency_success"]),
                    "correct_stop": int(case["correct_stop_abstain"]),
                    "goal_recall": case["weighted_information_goal_recall"],
                    "claim_precision": case["claim_support_precision"],
                    "top1": case["acceptable_next_action_top1"],
                    "tool_count": case["tool_count"],
                    "run_tokens": total_tokens,
                    "run_wall_time_ms": total_time,
                }
            )
    arms: dict[str, Any] = {}
    for arm, arm_scores in sorted(grouped.items()):
        cases = [case for score in arm_scores for case in score["cases"]]
        successes = sum(int(case["evidence_sufficiency_success"]) for case in cases)
        stops = sum(int(case["correct_stop_abstain"]) for case in cases)
        supported_claims = sum(int(case["supported_claims"]) for case in cases)
        total_claims = sum(int(case["total_claims"]) for case in cases)
        run_ids = [str(score["run"]["run_id"]) for score in arm_scores]
        arm_traces = [traces[item] for item in run_ids if item in traces]
        tokens = [
            sum(
                int(case.get("telemetry", {}).get("tokens") or 0)
                for case in trace.get("traces") or []
            )
            / max(1, len(trace.get("traces") or []))
            for trace in arm_traces
        ]
        times = [
            sum(
                int(case.get("telemetry", {}).get("wall_time_ms") or 0)
                for case in trace.get("traces") or []
            )
            / max(1, len(trace.get("traces") or []))
            for trace in arm_traces
        ]
        arms[arm] = {
            "runs": len(arm_scores),
            "case_runs": len(cases),
            "evidence_sufficiency_rate": successes / len(cases),
            "evidence_sufficiency_ci95": wilson(successes, len(cases)),
            "correct_stop_rate": stops / len(cases),
            "correct_stop_ci95": wilson(stops, len(cases)),
            "weighted_goal_recall": sum(
                case["weighted_information_goal_recall"] for case in cases
            )
            / len(cases),
            "claim_support_precision": supported_claims / total_claims
            if total_claims
            else 1.0,
            "claim_support_precision_ci95": wilson(supported_claims, total_claims),
            "acceptable_next_action_top1": sum(
                case["acceptable_next_action_top1"] for case in cases
            )
            / len(cases),
            "mean_tokens_per_case": sum(tokens) / len(tokens) if tokens else None,
            "mean_wall_time_ms_per_case": sum(times) / len(times) if times else None,
            "safety_pass": all(
                score["safety_hard_gates"]["passed"] for score in arm_scores
            ),
        }
    paired: dict[str, Any] = {}
    if "M1" in grouped:
        mini = {
            (score["run"]["seed"], case["case_id"]): case
            for score in grouped["M1"]
            for case in score["cases"]
        }
        for arm in sorted(set(grouped) - {"M1"}):
            other = {
                (score["run"]["seed"], case["case_id"]): case
                for score in grouped[arm]
                for case in score["cases"]
            }
            wins = losses = ties = 0
            for key in sorted(set(mini) & set(other)):
                left = int(mini[key]["evidence_sufficiency_success"]) + int(
                    mini[key]["correct_stop_abstain"]
                )
                right = int(other[key]["evidence_sufficiency_success"]) + int(
                    other[key]["correct_stop_abstain"]
                )
                wins += int(left > right)
                losses += int(left < right)
                ties += int(left == right)
            paired[f"M1_vs_{arm}"] = {"wins": wins, "losses": losses, "ties": ties}
    return {
        "schema_version": "collector-agent-aggregate.v1",
        "arms": arms,
        "paired": paired,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, nargs="+", required=True)
    parser.add_argument("--traces", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    traces = {str(item["run"]["run_id"]): item for item in map(load, args.traces)}
    result = aggregate(list(map(load, args.scores)), traces)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8", newline="") as handle:
        if result["rows"]:
            writer = csv.DictWriter(handle, fieldnames=list(result["rows"][0]))
            writer.writeheader()
            writer.writerows(result["rows"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
