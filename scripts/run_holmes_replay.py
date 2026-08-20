#!/usr/bin/env python3
"""Run the pinned HolmesGPT ToolCallingLLM on Collector replay cases."""

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
DEFAULT_SOURCE = Path("/opt/agent-eval/open_source/holmesgpt")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_replay_agent import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ReplayEnvironment,
    case_prompt,
    load_json,
    public_cases,
)

HOLMES_SYSTEM_PROMPT = """You are HolmesGPT, an SRE incident investigation agent.
Use only the registered collect tool and the supplied Case data. Gather the
minimum evidence needed to explain what is observed, distinguish confirmed
facts from hypotheses, avoid duplicate queries, and do not execute remediation.
The Case target is already resolved; do not use process_scan when target_pid is
present. Respect the visible budget. When finished, return only one JSON object:
{"status":"SUFFICIENT|INSUFFICIENT_EVIDENCE","certainty":"LOW|MEDIUM|HIGH",
"summary":"...","claims":[{"predicate":{"field_path":"...","operator":
"eq|gte|gt|lte|lt|length_eq","value":0},"citations":[{"evidence_id":"...",
"projection_hash":"...","field_path":"..."}]}]}. Never invent Evidence.
"""


def _install_source_path(source: Path) -> None:
    if not (source / "holmes").is_dir():
        raise RuntimeError(f"HolmesGPT source is missing: {source}")
    sys.path.insert(0, str(source.resolve()))


def _normalize_final(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        try:
            parsed = json.loads(text[start:end + 1]) if start >= 0 and end > start else {}
        except json.JSONDecodeError:
            parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    status = str(parsed.get("status") or "INSUFFICIENT_EVIDENCE")
    if status not in {"SUFFICIENT", "INSUFFICIENT_EVIDENCE"}:
        status = "INSUFFICIENT_EVIDENCE"
    certainty = str(parsed.get("certainty") or "LOW").upper()
    if certainty not in {"LOW", "MEDIUM", "HIGH"}:
        certainty = "LOW"
    return {
        "status": status,
        "certainty": certainty,
        "summary": str(parsed.get("summary") or "No structured conclusion was returned."),
        "claims": parsed.get("claims") if isinstance(parsed.get("claims"), list) else [],
    }


def run_case(case: dict[str, Any], replay: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, int]]:
    from pydantic import PrivateAttr

    from holmes.core.llm import DefaultLLM
    from holmes.core.tool_calling_llm import ToolCallingLLM
    from holmes.core.tools import (
        StructuredToolResult,
        StructuredToolResultStatus,
        Tool,
        ToolInvokeContext,
        ToolParameter,
        Toolset,
        ToolsetStatusEnum,
    )
    from holmes.core.tools_utils.tool_executor import ToolExecutor

    env = ReplayEnvironment(case, replay)
    available = list(replay.get("available_collectors") or [])

    class CollectTool(Tool):
        _environment: ReplayEnvironment = PrivateAttr()

        def __init__(self, environment: ReplayEnvironment):
            super().__init__(
                name="collect",
                description="Run one registered read-only collector and return canonical Evidence.",
                parameters={
                    "collector_id": ToolParameter(
                        type="string",
                        description="Collector ID",
                        enum=available,
                    ),
                },
            )
            self._environment = environment

        def _invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
            result = self._environment.collect(str(params.get("collector_id") or ""))
            status = StructuredToolResultStatus.SUCCESS
            if result.get("status") == "REJECTED":
                status = StructuredToolResultStatus.ERROR
            return StructuredToolResult(status=status, data=result, params=params)

        def get_parameterized_one_liner(self, params: dict) -> str:
            return f"collect {params.get('collector_id', '')}"

    toolset = Toolset(
        enabled=True,
        name="mini-drop-replay",
        description="Read-only Mini-Drop Collector replay",
        tools=[CollectTool(env)],
        status=ToolsetStatusEnum.ENABLED,
    )
    executor = ToolExecutor([toolset])
    llm = DefaultLLM(
        model=f"openai/{args.model}",
        api_key=os.environ[args.api_key_env],
        api_base=args.base_url.rstrip("/") + "/v1",
        args={
            "max_tokens": 2400,
            "thinking": {"type": "disabled"},
            "custom_args": {"max_context_size": 65536},
        },
    )
    agent = ToolCallingLLM(
        tool_executor=executor,
        max_steps=8,
        llm=llm,
        tool_results_dir=None,
    )
    messages = [
        {"role": "system", "content": HOLMES_SYSTEM_PROMPT},
        {"role": "user", "content": case_prompt(case, replay)},
    ]
    started = time.monotonic()
    error = ""
    try:
        result = agent.call(messages, request_context={"user_id": "collector-eval"})
        final = _normalize_final(result.result)
        prompt_tokens = int(result.prompt_tokens or 0)
        completion_tokens = int(result.completion_tokens or 0)
        model_calls = int(result.num_llm_calls or 0)
    except Exception as exc:
        error = type(exc).__name__
        final = _normalize_final("")
        prompt_tokens = completion_tokens = model_calls = 0
    env.actions.append({
        "state": "initial" if not env.selected else "after:" + ",".join(sorted(env.selected)),
        "decision": "ABSTAIN" if final["status"] == "INSUFFICIENT_EVIDENCE" else "STOP",
        "alternatives": [],
    })
    wall_time_ms = round((time.monotonic() - started) * 1000)
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model_calls": model_calls,
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
            "model_calls": model_calls,
            "proposal_rejections": env.proposal_rejections,
            "error": error,
        },
    }
    return trace, usage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "benchmarks" / "collector_agent_v1")
    parser.add_argument("--source", type=Path, default=Path(os.getenv("HOLMESGPT_SOURCE", DEFAULT_SOURCE)))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--run-id")
    return parser


def _source_sha(source: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.getenv(args.api_key_env, "").strip():
        print(f"missing provider key in {args.api_key_env}", file=sys.stderr)
        return 2
    # Holmes reserves a 64k fallback output window for unknown model aliases.
    # Keep its compaction budget aligned with the actual provider request.
    os.environ.setdefault("OVERRIDE_MAX_OUTPUT_TOKEN", "2400")
    os.environ.setdefault("OVERRIDE_MAX_CONTENT_SIZE", "65536")
    _install_source_path(args.source)
    manifest = load_json(args.suite / "manifest.json")
    traces = []
    total_usage: dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model_calls": 0}
    for case, replay in public_cases(args.suite):
        trace, usage = run_case(case, replay, args)
        traces.append(trace)
        for key in list(total_usage):
            total_usage[key] += usage[key]
        print(f"{case['case_id']}: tools={trace['telemetry']['tool_calls']} tokens={trace['telemetry']['tokens']} status={trace['final']['status']}")
    total_usage["source_sha"] = _source_sha(args.source)
    payload = {
        "schema_version": "collector-agent-run-traces.v1",
        "run": {
            "run_id": args.run_id or f"holmesgpt-{uuid.uuid4().hex[:12]}",
            "arm": "H1",
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
