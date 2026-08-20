#!/usr/bin/env python3
"""Run the pinned smolagents ToolCallingAgent on Collector replay cases."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/opt/agent-eval/open_source/smolagents")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_replay_agent import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DIRECT_BASELINE_PROMPT,
    ReplayEnvironment,
    case_prompt,
    load_json,
    public_cases,
)


def _install_source_path(source: Path) -> None:
    package_root = source.resolve() / "src"
    if not package_root.is_dir():
        raise RuntimeError(f"smolagents source is missing: {package_root}")
    sys.path.insert(0, str(package_root))


def _normalize_final(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_raw"):
        value = value.to_raw()
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = {}
    if not isinstance(value, dict):
        value = {}
    status = str(value.get("status") or "INSUFFICIENT_EVIDENCE")
    if status not in {"SUFFICIENT", "INSUFFICIENT_EVIDENCE"}:
        status = "INSUFFICIENT_EVIDENCE"
    certainty = str(value.get("certainty") or "LOW").upper()
    if certainty not in {"LOW", "MEDIUM", "HIGH"}:
        certainty = "LOW"
    claims = value.get("claims") if isinstance(value.get("claims"), list) else []
    return {
        "status": status,
        "certainty": certainty,
        "summary": str(value.get("summary") or "No structured conclusion was returned."),
        "claims": claims,
    }


def run_case(case: dict[str, Any], replay: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, int]]:
    from smolagents import OpenAIModel, Tool, ToolCallingAgent
    from smolagents.monitoring import LogLevel

    env = ReplayEnvironment(case, replay)
    available = list(replay.get("available_collectors") or [])

    class CollectTool(Tool):
        name = "collect"
        description = "Run one registered read-only collector and return canonical Evidence. Select only one per step."
        inputs = {
            "collector_id": {
                "type": "string",
                "description": "Collector ID. Must be one of: " + ", ".join(available),
                "enum": available,
            },
        }
        output_type = "object"

        def forward(self, collector_id: str) -> dict[str, Any]:
            return env.collect(collector_id)

    class StructuredFinalAnswerTool(Tool):
        name = "final_answer"
        description = (
            "Stop the investigation. Claims must contain predicate and citations with evidence_id, "
            "projection_hash, and exact field_path."
        )
        inputs = {
            "status": {"type": "string", "description": "SUFFICIENT or INSUFFICIENT_EVIDENCE"},
            "certainty": {"type": "string", "description": "LOW, MEDIUM, or HIGH"},
            "summary": {"type": "string", "description": "Evidence-grounded conclusion"},
            "claims": {
                "type": "array",
                "description": (
                    "Claims shaped as [{predicate:{field_path,operator,value},citations:"
                    "[{evidence_id,projection_hash,field_path}]}]"
                ),
            },
        }
        output_type = "object"

        def forward(
            self,
            status: str,
            certainty: str,
            summary: str,
            claims: list,
        ) -> dict[str, Any]:
            decision = "ABSTAIN" if status == "INSUFFICIENT_EVIDENCE" else "STOP"
            env.actions.append({
                "state": "initial" if not env.selected else "after:" + ",".join(sorted(env.selected)),
                "decision": decision,
                "alternatives": [],
            })
            return {"status": status, "certainty": certainty, "summary": summary, "claims": claims}

    model = OpenAIModel(
        model_id=args.model,
        api_base=args.base_url.rstrip("/") + "/v1",
        api_key=os.environ[args.api_key_env],
        temperature=0,
        max_tokens=2400,
        extra_body={"thinking": {"type": "disabled"}},
    )
    agent = ToolCallingAgent(
        tools=[CollectTool(), StructuredFinalAnswerTool()],
        model=model,
        instructions=DIRECT_BASELINE_PROMPT,
        max_steps=8,
        max_tool_threads=1,
        verbosity_level=LogLevel.OFF,
        return_full_result=True,
    )
    started = time.monotonic()
    error = ""
    try:
        result = agent.run(case_prompt(case, replay))
        final = _normalize_final(result.output)
        token_usage = result.token_usage
        prompt_tokens = int(getattr(token_usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(token_usage, "output_tokens", 0) or 0)
    except Exception as exc:  # framework errors are recorded, never hidden
        error = type(exc).__name__
        final = _normalize_final({})
        prompt_tokens = completion_tokens = 0
    wall_time_ms = round((time.monotonic() - started) * 1000)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model_calls": max(0, len(env.actions)),
    }
    trace = {
        "case_id": case["case_id"],
        "actions": env.actions,
        "evidence": env.evidence,
        "final": final,
        "safety": {
            "unauthorized_execution": False,
            "approval_bypass": False,
            "scope_violation": False,
            "cleanup_failure": False,
        },
        "telemetry": {
            "wall_time_ms": wall_time_ms,
            "tool_calls": len(env.actions),
            "bytes": sum(len(json.dumps(item, ensure_ascii=False).encode()) for item in env.evidence),
            "tokens": usage["total_tokens"],
            "cost": sum(float(replay["branches"][item].get("cost") or 0) for item in env.selected),
            "model_calls": usage["model_calls"],
            "proposal_rejections": env.proposal_rejections,
            "error": error,
        },
    }
    return trace, usage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "benchmarks" / "collector_agent_v1")
    parser.add_argument("--source", type=Path, default=Path(os.getenv("SMOLAGENTS_SOURCE", DEFAULT_SOURCE)))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.getenv(args.api_key_env, "").strip():
        print(f"missing provider key in {args.api_key_env}", file=sys.stderr)
        return 2
    _install_source_path(args.source)
    manifest = load_json(args.suite / "manifest.json")
    traces = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model_calls": 0}
    for case, replay in public_cases(args.suite):
        trace, usage = run_case(case, replay, args)
        traces.append(trace)
        for key in total_usage:
            total_usage[key] += usage[key]
        print(f"{case['case_id']}: tools={trace['telemetry']['tool_calls']} tokens={trace['telemetry']['tokens']} status={trace['final']['status']}")
    completed = subprocess.run(
        ["git", "-C", str(args.source), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    source_sha = completed.stdout.strip() if completed.returncode == 0 else "unknown"
    total_usage["source_sha"] = source_sha
    payload = {
        "schema_version": "collector-agent-run-traces.v1",
        "run": {
            "run_id": args.run_id or f"smolagents-{uuid.uuid4().hex[:12]}",
            "arm": "S1",
            "model": args.model,
            "prompt_version": manifest["prompt_version"],
            "catalog_hash": manifest["catalog_hash"],
            "policy_version": manifest["policy_version"],
            "seed": args.seed,
            "provider_usage": total_usage,
        },
        "traces": traces,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not any(item["telemetry"]["error"] for item in traces) else 3


if __name__ == "__main__":
    raise SystemExit(main())
