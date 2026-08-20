#!/usr/bin/env python3
"""Run an LLM agent against the public Collector Agent replay environment.

The runner deliberately never opens ``private/oracles.json``.  It emits the
canonical run-trace contract consumed by ``run_collector_agent_eval.py``.
Secrets are accepted only through environment variables and are never written
to traces or diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "benchmarks" / "collector_agent_v1"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

MINI_DROP_PROMPT = """You are the Mini-Drop Evidence-native Collector Agent.
Investigate only the supplied Case, Collector Catalog and collected Evidence.
Never invent evidence or use hidden labels. Select one high-information
collector at a time, avoid duplicates, and stop when the evidence is sufficient,
the budget is exhausted, or another collection would add no information.
Treat the Case target as authoritative: do not use process_scan when target_pid is
already supplied, and do not re-discover a runtime already named by target.type.
After a low-cost baseline, prefer the specialized collector with the highest
information gain for the observed abnormal dimension. Reserve one cycle to stop.
For endpoint reachability, request failure, or intermittent availability reports,
prioritize sys_metrics and connection_probe, then log_scan when the budget allows;
runtime_snapshot cannot establish endpoint reachability. Abstention is not a
shortcut around collection: gather the minimum evidence needed to characterize
the observation before returning INSUFFICIENT_EVIDENCE.
Final claims must cite evidence_id, projection_hash and an exact field_path.
SUFFICIENT means the evidence is sufficient to answer the reported incident,
not merely sufficient to describe the current sample. For sporadic, brief, or
intermittent reports, a short healthy window cannot prove absence and conflicting
observations cannot establish a stable conclusion: abstain unless the Evidence
captures a representative window or a repeatable failure. If evidence remains
insufficient, abstain with INSUFFICIENT_EVIDENCE. Use only the collect and
final_answer tools. Never request shell commands or remediation.
"""

DIRECT_BASELINE_PROMPT = """You are a general tool-using operations assistant.
Use the available collectors to investigate the incident, then return a
structured evidence-grounded answer. Do not invent evidence and do not execute
remediation. Use only the collect and final_answer tools.
"""


class RunnerError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def api_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def public_cases(suite: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    payload = load_json(suite / "public" / "cases.json")
    result = []
    public_root = (suite / "public").resolve()
    for case in payload.get("cases") or []:
        replay_path = (public_root / str(case["replay"])).resolve()
        if public_root not in replay_path.parents:
            raise RunnerError(f"replay escapes public root: {case.get('case_id')}")
        replay = load_json(replay_path)
        if replay.get("case_id") != case.get("case_id"):
            raise RunnerError(f"case/replay mismatch: {case.get('case_id')}")
        result.append((case, replay))
    return result


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    provider_request_ids: list[str] = field(default_factory=list)

    def add(self, response: dict[str, Any]) -> None:
        item = response.get("usage") or {}
        self.prompt_tokens += int(item.get("prompt_tokens") or 0)
        self.completion_tokens += int(item.get("completion_tokens") or 0)
        self.total_tokens += int(item.get("total_tokens") or 0)
        self.model_calls += 1
        request_id = str(response.get("id") or "")
        if request_id:
            self.provider_request_ids.append(request_id)


@dataclass
class ReplayEnvironment:
    case: dict[str, Any]
    replay: dict[str, Any]
    actions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    selected: list[str] = field(default_factory=list)
    proposal_rejections: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def collect(self, collector_id: str) -> dict[str, Any]:
        with self._lock:
            return self._collect_locked(collector_id)

    def _collect_locked(self, collector_id: str) -> dict[str, Any]:
        state = (
            "initial"
            if not self.selected
            else "after:" + ",".join(sorted(self.selected))
        )
        action = {
            "state": state,
            "decision": collector_id,
            "alternatives": [],
            "accepted": False,
        }
        if collector_id not in self.replay.get("branches", {}):
            self.actions.append(action)
            self.proposal_rejections += 1
            return {"status": "REJECTED", "reason": "collector_not_available"}
        if collector_id in self.selected:
            self.actions.append(action)
            self.proposal_rejections += 1
            return {"status": "REUSED", "reason": "duplicate_collection"}
        branch = self.replay["branches"][collector_id]
        budget = self.case.get("budget") or {"max_cost": 8, "max_tool_calls": 8}
        current_cost = sum(
            float(self.replay["branches"][item].get("cost") or 0)
            for item in self.selected
        )
        if len(self.selected) >= int(budget.get("max_tool_calls") or 8):
            self.actions.append(action)
            self.proposal_rejections += 1
            return {"status": "REJECTED", "reason": "tool_call_budget_exhausted"}
        if current_cost + float(branch.get("cost") or 0) > float(
            budget.get("max_cost") or 8
        ):
            self.actions.append(action)
            self.proposal_rejections += 1
            return {"status": "REJECTED", "reason": "cost_budget_exhausted"}
        action["accepted"] = True
        self.actions.append(action)
        self.selected.append(collector_id)
        evidence = {
            "evidence_id": branch.get("evidence_id"),
            "collector_id": collector_id,
            "projection_hash": branch.get("projection_hash"),
            "projection": branch.get("projection"),
        }
        self.evidence.append(evidence)
        return {
            "status": branch.get("status"),
            "evidence": evidence,
            "cost": branch.get("cost"),
        }


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int):
        self.api_key = api_key
        self.url = api_url(base_url)
        self.model = model
        self.timeout = timeout

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "required",
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": 2400,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RunnerError(f"provider HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RunnerError(f"provider failure: {type(exc).__name__}") from exc


def tool_specs(available: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "collect",
                "description": "Run one registered read-only collector and return canonical Evidence.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "collector_id": {"type": "string", "enum": available}
                    },
                    "required": ["collector_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "final_answer",
                "description": "Stop and return only evidence-supported structured claims.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["SUFFICIENT", "INSUFFICIENT_EVIDENCE"],
                        },
                        "certainty": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH"],
                        },
                        "summary": {"type": "string"},
                        "claims": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "predicate": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            "field_path": {"type": "string"},
                                            "operator": {
                                                "type": "string",
                                                "enum": [
                                                    "eq",
                                                    "gte",
                                                    "gt",
                                                    "lte",
                                                    "lt",
                                                    "length_eq",
                                                ],
                                            },
                                            "value": {},
                                        },
                                        "required": ["field_path", "operator", "value"],
                                    },
                                    "citations": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "properties": {
                                                "evidence_id": {"type": "string"},
                                                "projection_hash": {"type": "string"},
                                                "field_path": {"type": "string"},
                                            },
                                            "required": [
                                                "evidence_id",
                                                "projection_hash",
                                                "field_path",
                                            ],
                                        },
                                    },
                                },
                                "required": ["predicate", "citations"],
                            },
                        },
                    },
                    "required": ["status", "certainty", "summary", "claims"],
                },
            },
        },
    ]


def case_prompt(case: dict[str, Any], replay: dict[str, Any]) -> str:
    available = replay.get("available_collectors") or []
    catalog = load_json(ROOT / "mini_drop_contracts" / "catalog" / "collectors.v1.json")
    by_id = {item["collector_id"]: item for item in catalog.get("collectors") or []}
    collector_catalog = []
    for collector_id in available:
        spec = by_id.get(collector_id) or {}
        collector_catalog.append(
            {
                "collector_id": collector_id,
                "description": spec.get("description", ""),
                "information_goals": spec.get("information_goals", []),
                "risk_level": spec.get("risk_level", ""),
                "cost": (replay.get("branches") or {})
                .get(collector_id, {})
                .get("cost"),
            }
        )
    return json.dumps(
        {
            "case_id": case["case_id"],
            "problem": case["prompt"],
            "target": case["target"],
            "collector_catalog": collector_catalog,
            "budget": case.get("budget") or {"max_tool_calls": 8, "max_cost": 8},
            "instruction": "Collect proportionate evidence, then call final_answer.",
        },
        ensure_ascii=False,
    )


def _tool_call_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    raw = (tool_call.get("function") or {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RunnerError("model returned invalid tool arguments") from exc
    if not isinstance(parsed, dict):
        raise RunnerError("model tool arguments must be an object")
    return parsed


def run_direct_case(
    client: DeepSeekClient,
    env: ReplayEnvironment,
    system_prompt: str,
    usage: Usage,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case_prompt(env.case, env.replay)},
    ]
    tools = tool_specs(env.replay.get("available_collectors") or [])
    for _ in range(9):
        response = client.complete(messages, tools)
        usage.add(response)
        choices = response.get("choices") or []
        if not choices:
            raise RunnerError("provider returned no choices")
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            messages.append(message)
            messages.append(
                {
                    "role": "user",
                    "content": "Continue by calling exactly one available tool. Do not answer with plain text.",
                }
            )
            continue
        messages.append(message)
        for index, tool_call in enumerate(tool_calls):
            name = str((tool_call.get("function") or {}).get("name") or "")
            arguments = _tool_call_arguments(tool_call)
            if index > 0:
                env.proposal_rejections += 1
                result = {"status": "REJECTED", "reason": "one_tool_per_cycle"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                continue
            if name == "final_answer":
                env.actions.append(
                    {
                        "state": "initial"
                        if not env.selected
                        else "after:" + ",".join(sorted(env.selected)),
                        "decision": "ABSTAIN"
                        if arguments.get("status") == "INSUFFICIENT_EVIDENCE"
                        else "STOP",
                        "alternatives": [],
                    }
                )
                return arguments
            if name != "collect":
                result = {"status": "REJECTED", "reason": "unknown_tool"}
            else:
                result = env.collect(str(arguments.get("collector_id") or ""))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    raise RunnerError("agent exceeded maximum cycles")


def trace_for_case(
    client: DeepSeekClient,
    case: dict[str, Any],
    replay: dict[str, Any],
    system_prompt: str,
) -> tuple[dict[str, Any], Usage]:
    env = ReplayEnvironment(case, replay)
    usage = Usage()
    started = time.monotonic()
    error = ""
    try:
        final = run_direct_case(client, env, system_prompt, usage)
    except RunnerError as exc:
        error = str(exc)
        final = {
            "status": "INSUFFICIENT_EVIDENCE",
            "certainty": "LOW",
            "summary": "Agent execution failed before a supported conclusion.",
            "claims": [],
        }
    wall_time_ms = round((time.monotonic() - started) * 1000)
    return (
        {
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
                "bytes": sum(
                    len(json.dumps(item, ensure_ascii=False).encode())
                    for item in env.evidence
                ),
                "tokens": usage.total_tokens,
                "cost": sum(
                    float(replay["branches"][item].get("cost") or 0)
                    for item in env.selected
                ),
                "model_calls": usage.model_calls,
                "proposal_rejections": env.proposal_rejections,
                "error": error,
            },
        },
        usage,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile", choices=("mini_drop", "direct_baseline"), default="mini_drop"
    )
    parser.add_argument("--arm", default="M1")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        print(f"missing provider key in {args.api_key_env}", file=sys.stderr)
        return 2
    manifest = load_json(args.suite / "manifest.json")
    client = DeepSeekClient(api_key, args.base_url, args.model, args.timeout)
    system_prompt = (
        MINI_DROP_PROMPT if args.profile == "mini_drop" else DIRECT_BASELINE_PROMPT
    )
    traces = []
    total_usage = Usage()
    for case, replay in public_cases(args.suite):
        trace, usage = trace_for_case(client, case, replay, system_prompt)
        traces.append(trace)
        total_usage.prompt_tokens += usage.prompt_tokens
        total_usage.completion_tokens += usage.completion_tokens
        total_usage.total_tokens += usage.total_tokens
        total_usage.model_calls += usage.model_calls
        total_usage.provider_request_ids.extend(usage.provider_request_ids)
        print(
            f"{case['case_id']}: tools={trace['telemetry']['tool_calls']} "
            f"tokens={trace['telemetry']['tokens']} status={trace['final']['status']}"
        )
    payload = {
        "schema_version": "collector-agent-run-traces.v1",
        "run": {
            "run_id": args.run_id or f"{args.profile}-{uuid.uuid4().hex[:12]}",
            "arm": args.arm,
            "model": args.model,
            "prompt_version": manifest["prompt_version"],
            "catalog_hash": manifest["catalog_hash"],
            "policy_version": manifest["policy_version"],
            "seed": args.seed,
            "provider_usage": {
                "prompt_tokens": total_usage.prompt_tokens,
                "completion_tokens": total_usage.completion_tokens,
                "total_tokens": total_usage.total_tokens,
                "model_calls": total_usage.model_calls,
                "provider_request_ids": total_usage.provider_request_ids,
                "agent_prompt_sha256": hashlib.sha256(
                    system_prompt.encode("utf-8")
                ).hexdigest(),
            },
        },
        "traces": traces,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if not any(item["telemetry"]["error"] for item in traces) else 3


if __name__ == "__main__":
    raise SystemExit(main())
