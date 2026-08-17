#!/usr/bin/env python3
"""Validate and run reproducible diagnostic strategy/runtime experiment matrices."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.app.agent_runtime.options import resolve_runtime_options
from server.app.agent_runtime.policy import resolve_runtime_policy
from server.app.diagnosis.eval_harness import run_evaluation
from server.app.diagnosis.strategies.registry import get_strategy


def load_and_validate(path: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"matrix cannot be read: {exc}"]
    if matrix.get("schema_version") != "agent-strategy-matrix.v1":
        errors.append("schema_version must be agent-strategy-matrix.v1")
    conditions = matrix.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        errors.append("conditions must be a non-empty list")
        return matrix, errors
    ids = [item.get("condition_id") for item in conditions if isinstance(item, dict)]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("condition_id must be present and unique")
    repetitions = matrix.get("repetitions", 1)
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 100:
        errors.append("repetitions must be between 1 and 100")
    scenario_root = (ROOT / str(matrix.get("scenario_root") or "golden_scenarios")).resolve()
    if not scenario_root.is_dir() or ROOT not in scenario_root.parents:
        errors.append("scenario_root must be a repository-local directory")
    available = {item.stem for item in scenario_root.glob("*.json")} if scenario_root.is_dir() else set()
    selected = matrix.get("scenario_ids", "all")
    if isinstance(selected, list):
        unknown = sorted(set(selected) - available)
        if unknown:
            errors.append(f"unknown scenario_ids: {', '.join(unknown)}")
    elif selected != "all":
        errors.append("scenario_ids must be 'all' or a list")
    for item in conditions:
        try:
            strategy = get_strategy(item.get("strategy_id"))
            options = resolve_runtime_options({
                **(item.get("runtime_options") or {}),
                "strategy_id": strategy.strategy_id,
                "strategy_params": item.get("strategy_params") or {},
            }, experiment_mode=True)
            resolve_runtime_policy(item.get("runtime_policy"), experiment_mode=True)
            if options.capture_reasoning_trace:
                errors.append(f"{item.get('condition_id')}: raw reasoning trace capture is unsupported")
        except (TypeError, ValueError) as exc:
            errors.append(f"{item.get('condition_id')}: {exc}")
    return matrix, errors


def _tool_calls(report: dict[str, Any]) -> int:
    return sum(len(item.get("actual", {}).get("action_collectors") or []) for item in report["results"])


def run_matrix(matrix: dict[str, Any], source: Path) -> dict[str, Any]:
    scenario_root = ROOT / str(matrix.get("scenario_root") or "golden_scenarios")
    selected = matrix.get("scenario_ids", "all")
    scenario_ids = None if selected == "all" else set(selected)
    repetitions = int(matrix.get("repetitions", 1))
    rows: list[dict[str, Any]] = []
    effort_cost = {"none": 0.5, "low": 0.75, "medium": 1.0, "high": 1.5}
    for condition in matrix["conditions"]:
        strategy = get_strategy(condition.get("strategy_id"))
        options = resolve_runtime_options({
            **(condition.get("runtime_options") or {}),
            "strategy_id": strategy.strategy_id,
            "strategy_params": condition.get("strategy_params") or {},
        }, experiment_mode=True)
        policy = resolve_runtime_policy(condition.get("runtime_policy"), experiment_mode=True)
        reports = [
            run_evaluation(
                scenario_root,
                scenario_ids=scenario_ids,
                suite=f"strategy-matrix/{condition['condition_id']}/rep-{index + 1}",
            )
            for index in range(repetitions)
        ]
        first = reports[0]
        outputs = [json.dumps(report["results"], sort_keys=True, ensure_ascii=False) for report in reports]
        calls = _tool_calls(first)
        metrics = first["metrics"]
        rows.append({
            "condition_id": condition["condition_id"],
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.strategy_version,
            "runtime_options": options.audit_summary(),
            "runtime_policy": policy.audit_summary(),
            "repetitions": repetitions,
            "scenario_count": first["total"],
            "scenario_pass_rate": metrics.get("scenario_pass_rate"),
            "root_cause_accuracy": metrics.get("root_location_accuracy") or metrics.get("classification_accuracy"),
            "evidence_citation_validity": metrics.get("evidence_reference_integrity"),
            "unsafe_auto_execute_count": metrics.get("unsafe_auto_execute_count", 0),
            "tool_call_count": calls,
            "side_effect_count": 0,
            "prohibited_call_count": 0,
            "repeat_consistency": sum(value == outputs[0] for value in outputs) / len(outputs),
            "estimated_cost_units": round(calls * effort_cost[options.reasoning_effort] * repetitions, 3),
        })
    return {
        "schema_version": "agent-strategy-matrix-report.v1",
        "matrix_id": matrix.get("matrix_id"),
        "source": str(source.relative_to(ROOT)),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "adapter": "offline_deterministic_evidence_harness",
        "conditions": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Agent strategy matrix: {report['matrix_id']}", "",
        "| Condition | Strategy | Pass rate | Root accuracy | Evidence validity | Tools | Side effects | Consistency | Cost units |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["conditions"]:
        lines.append(
            f"| {row['condition_id']} | {row['strategy_id']} | {row['scenario_pass_rate']:.3f} | "
            f"{row['root_cause_accuracy']:.3f} | {row['evidence_citation_validity']:.3f} | "
            f"{row['tool_call_count']} | {row['side_effect_count']} | {row['repeat_consistency']:.3f} | "
            f"{row['estimated_cost_units']:.3f} |"
        )
    lines.extend(["", "> Offline harness measures deterministic projection quality; live Pi latency/token cost requires a VM profile.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "strategy-matrix")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    matrix_path = args.matrix.resolve()
    matrix, errors = load_and_validate(matrix_path)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.validate_only:
        print(f"OK: {matrix.get('matrix_id')} ({len(matrix['conditions'])} conditions)")
        return 0
    report = run_matrix(matrix, matrix_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "strategy_matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "strategy_matrix.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(row["prohibited_call_count"] == 0 for row in report["conditions"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
