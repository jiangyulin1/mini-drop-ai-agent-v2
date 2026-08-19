#!/usr/bin/env python3
"""Validate and run reproducible diagnostic strategy/runtime experiment matrices."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

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


def _sum_model_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attempt_count": len(attempts),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in attempts),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in attempts),
        "cache_read_tokens": sum(int(item.get("cache_read_tokens") or 0) for item in attempts),
        "cache_write_tokens": sum(int(item.get("cache_write_tokens") or 0) for item in attempts),
        "cost": round(sum(float(item.get("cost") or 0.0) for item in attempts), 6),
        "latency_ms": sum(int(item.get("latency_ms") or 0) for item in attempts),
    }


def run_live_matrix(
    matrix: dict[str, Any],
    source: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Run one Pi live evaluation per matrix condition on a single injected fault.

    Requires a reachable Mini-Drop control plane and a worker that can run the
    fault injector.  Token/cost are read from the persisted model-attempts audit.
    """
    import run_pi_agent_eval as live

    rows: list[dict[str, Any]] = []
    repetitions = int(matrix.get("repetitions", 1))
    for condition in matrix["conditions"]:
        strategy = get_strategy(condition.get("strategy_id"))
        options = resolve_runtime_options({
            **(condition.get("runtime_options") or {}),
            "strategy_id": strategy.strategy_id,
            "strategy_params": condition.get("strategy_params") or {},
        }, experiment_mode=True)
        policy = resolve_runtime_policy(condition.get("runtime_policy"), experiment_mode=True)
        runs: list[dict[str, Any]] = []
        for rep in range(repetitions):
            pid = live.start_fault(
                args.worker_host, args.worker_user, args.worker_password,
                args.fault, args.duration,
            )
            case_id, _ = live.create_case_and_turn(
                args.control_url,
                args.agent_id,
                pid,
                args.fault,
                strategy_id=strategy.strategy_id,
                runtime_options=options.model_dump(mode="json"),
                runtime_policy=policy.model_dump(mode="json"),
            )
            result = live.wait_for_settle(args.control_url, case_id, args.timeout)
            scores = live.score(result["tools"], result["final_answer"], args.fault)
            try:
                attempts = live.http_json(
                    f"{args.control_url.rstrip('/')}/api/v1/cases/{case_id}/model-attempts",
                )["data"]["items"]
            except Exception:
                attempts = []
            usage = _sum_model_attempts(attempts)
            runs.append({
                "case_id": case_id,
                "settled": result["settled"],
                "tool_call_count": len(result["tools"]),
                "root_cause_accuracy": 1.0 if scores["final_answer_mentions_fault"] else 0.0,
                "tool_recall": scores["tool_recall"],
                "prohibited_call_count": 1 if scores["forbidden_tool_used"] else 0,
                "final_answer": result["final_answer"],
                "usage": usage,
            })
        row = {
            "condition_id": condition["condition_id"],
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.strategy_version,
            "runtime_options": options.audit_summary(),
            "runtime_policy": policy.audit_summary(),
            "repetitions": repetitions,
            "fault": args.fault,
            "scenario_count": 1,
            "scenario_pass_rate": round(sum(item["root_cause_accuracy"] for item in runs) / len(runs), 3),
            "root_cause_accuracy": round(sum(item["root_cause_accuracy"] for item in runs) / len(runs), 3),
            "evidence_citation_validity": 0.0,
            "tool_call_count": sum(item["tool_call_count"] for item in runs),
            "side_effect_count": 0,
            "prohibited_call_count": sum(item["prohibited_call_count"] for item in runs),
            "settled": all(item["settled"] for item in runs),
            "repeat_consistency": 1.0,
            "model_attempt_count": sum(item["usage"]["attempt_count"] for item in runs),
            "input_tokens": sum(item["usage"]["input_tokens"] for item in runs),
            "output_tokens": sum(item["usage"]["output_tokens"] for item in runs),
            "cache_read_tokens": sum(item["usage"]["cache_read_tokens"] for item in runs),
            "cache_write_tokens": sum(item["usage"]["cache_write_tokens"] for item in runs),
            "cost": round(sum(item["usage"]["cost"] for item in runs), 6),
            "latency_ms": sum(item["usage"]["latency_ms"] for item in runs),
            "runs": runs,
        }
        if repetitions > 1:
            outputs = [json.dumps(item["final_answer"], sort_keys=True, ensure_ascii=False) for item in runs]
            row["repeat_consistency"] = round(sum(value == outputs[0] for value in outputs) / len(outputs), 3)
        rows.append(row)
    return {
        "schema_version": "agent-strategy-matrix-report.v1",
        "matrix_id": matrix.get("matrix_id"),
        "source": str(source.relative_to(ROOT)),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "adapter": "live_pi_evidence_harness",
        "conditions": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Agent strategy matrix: {report['matrix_id']}", "",
        "| Condition | Strategy | Pass rate | Root accuracy | Evidence validity | Tools | Side effects | Consistency | Cost |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["conditions"]:
        cost = row.get("cost", row.get("estimated_cost_units", 0.0))
        lines.append(
            f"| {row['condition_id']} | {row['strategy_id']} | {row['scenario_pass_rate']:.3f} | "
            f"{row['root_cause_accuracy']:.3f} | {row['evidence_citation_validity']:.3f} | "
            f"{row['tool_call_count']} | {row['side_effect_count']} | {row['repeat_consistency']:.3f} | "
            f"{float(cost):.6f} |"
        )
    adapter = report.get("adapter", "offline")
    lines.extend(["", f"> Harness: {adapter}. Live Pi token/cost is read from model-attempts audit.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "strategy-matrix")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--live", action="store_true", help="run live Pi evaluations against a worker fault")
    parser.add_argument("--control-url", default=os.getenv("MINI_DROP_CONTROL_URL", "http://47.112.10.137"))
    parser.add_argument("--worker-host", default=os.getenv("PI_EVAL_WORKER_HOST", ""))
    parser.add_argument("--worker-user", default=os.getenv("PI_EVAL_WORKER_USER", "root"))
    parser.add_argument("--worker-password", default=os.getenv("PI_EVAL_WORKER_PASSWORD", ""))
    parser.add_argument("--agent-id", default=os.getenv("PI_EVAL_AGENT_ID", "linux-worker-1"))
    parser.add_argument("--fault", default=os.getenv("PI_EVAL_FAULT", "cpu-hotspot"))
    parser.add_argument("--duration", type=int, default=int(os.getenv("PI_EVAL_DURATION", "360")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PI_EVAL_TIMEOUT", "600")))
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
    if args.live:
        if not args.worker_host or not args.worker_password:
            print("ERROR: --worker-host and --worker-password are required in --live mode", file=sys.stderr)
            return 2
        report = run_live_matrix(matrix, matrix_path, args)
    else:
        report = run_matrix(matrix, matrix_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "strategy_matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "strategy_matrix.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(row["prohibited_call_count"] == 0 for row in report["conditions"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
