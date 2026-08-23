#!/usr/bin/env python3
"""Run one benchmark case/repeat through the unified replay adapter.

This adapter is intentionally thin: it exposes the five common read-only tools
to the same remote model (DeepSeek deepseek-v4-flash) and records every tool
call, result hash, intervention, resource snapshot and normalized answer. It
does not claim to execute the upstream agent's private runtime; it is the
benchmark-owned replay adapter used for comparable measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]  # workspace root
BENCHMARK = ROOT / "benchmark"
COMMON_PROMPT = (ROOT / "prompts" / "system-prompt-common.md").read_text(encoding="utf-8")
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
API_KEY_ENV = "DEEPSEEK_API_KEY"
TIMEOUT = 120
MAX_TOOL_CALLS = 16

SOURCE_SHAS = {
    "mini-drop": "651c450867c4d6db26cc78de5928bb14f7b3c3b9",
    "holmesgpt": "87333f17b33985680a77525e1cc3a775eaf77b91",
    "smolagents": "e3a5b8994b301983b91c0325546e9dc82eab8cf0",
    "itops-agent-platform": "4398bbe20755e469012e261f69837337afdca0ce",
    "k8sgpt": "05247a851ba9292ca57e5070f1d0c4d3986b8d4c",
}

TOOL_NAMES = [
    "list_evidence",
    "query_metrics",
    "search_logs",
    "get_profile_topn",
    "get_evidence_slice",
    "final_answer",
]

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_evidence",
            "description": "List all evidence items for the case with id, kind, summary, lifecycle, trust, integrity hash.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_metrics",
            "description": "Query a metrics projection for one evidence id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string"},
                    "time_range": {"type": "string"},
                    "aggregation": {"type": "string"},
                },
                "required": ["evidence_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "Search normalized log templates. This suite returns NO_MATCH for raw logs by design.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "time_range": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_profile_topn",
            "description": "Get a CPU/profile top-N projection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string"},
                    "dimension": {"type": "string"},
                    "top_n": {"type": "integer"},
                },
                "required": ["evidence_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evidence_slice",
            "description": "Get a bounded slice of an evidence projection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string"},
                    "selector": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["evidence_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": "Stop the investigation and submit the final normalized answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conclusion": {"type": "string"},
                    "root_location": {"type": "string", "enum": ["self", "downstream", "same_host", "unknown"]},
                    "mechanism": {"type": "string"},
                    "confidence": {"type": "number"},
                    "confidence_reason": {"type": "string"},
                    "supporting_evidence": {"type": "array", "items": {"type": "string"}},
                    "counter_evidence": {"type": "array", "items": {"type": "string"}},
                    "missing_evidence": {"type": "array", "items": {"type": "string"}},
                    "next_action": {"type": "string"},
                    "abstain": {"type": "boolean"},
                },
                "required": ["conclusion", "root_location", "mechanism", "confidence", "confidence_reason", "supporting_evidence", "counter_evidence", "missing_evidence", "next_action", "abstain"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_DESCRIPTIONS = {
    "list_evidence": {"description": "List all evidence items for the case with id, kind, summary, lifecycle, trust, integrity hash."},
    "query_metrics": {"description": "Query a metrics projection for one evidence id. Arguments: evidence_id, time_range (optional), aggregation (optional)."},
    "search_logs": {"description": "Search normalized log templates. Arguments: query, time_range (optional), limit (optional). This suite returns NO_MATCH for raw logs by design."},
    "get_profile_topn": {"description": "Get a CPU/profile top-N projection. Arguments: evidence_id, dimension (optional, default topn), top_n (optional, default 10)."},
    "get_evidence_slice": {"description": "Get a bounded slice of an evidence projection. Arguments: evidence_id, selector (optional), limit (optional)."},
    "final_answer": {"description": "Stop the investigation and submit the final normalized answer. Arguments must match the required output schema."},
}


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def api_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def deepseek_complete(messages: list[dict[str, Any]], api_key: str, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    body = json.dumps({
        "model": model,
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "tool_choice": "auto",
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 2400,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url(base_url),
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DeepSeek provider failure: {type(exc).__name__}: {exc}") from exc


def initial_user_prompt(case: dict[str, Any], agent_id: str, seed: int) -> str:
    return json.dumps({
        "adapter": f"{agent_id}-replay-adapter",
        "seed": seed,
        "incident": case["incident"],
        "evidence_index": case["evidence_index"],
        "budget": case.get("budget", {}),
        "tools": TOOL_DESCRIPTIONS,
        "instruction": "Investigate using the provided tools. Use as few calls as needed. Once you have enough evidence, immediately call final_answer with the required output schema. Avoid redundant queries.",
        "required_output_schema": {
            "conclusion": "string",
            "root_location": "self|downstream|same_host|unknown",
            "mechanism": "string",
            "confidence": "number 0..1",
            "confidence_reason": "string",
            "supporting_evidence": ["evidence-id"],
            "counter_evidence": ["evidence-id"],
            "missing_evidence": ["string"],
            "next_action": "string",
            "abstain": "boolean"
        }
    }, ensure_ascii=False)


def tool_result_message(tool_call_id: str, tool_name: str, result: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, ensure_ascii=False),
    }


def intervention_for(case_id: str, service: Any, last_action: dict[str, Any], already_triggered: bool, step: int) -> list[dict[str, Any]]:
    if already_triggered:
        return []
    events = []
    if case_id == "case-07":
        text = json.dumps(last_action, ensure_ascii=False)
        cited = "ev-07-latency" in text
        confidence_high = False
        if isinstance(last_action.get("final"), dict):
            try:
                confidence_high = float(last_action["final"].get("confidence", 0) or 0) > 0.6
            except (TypeError, ValueError):
                confidence_high = False
        if cited or confidence_high:
            service.exclude("ev-07-latency", "target identity and time window do not match the active incident")
            events.append({
                "event_id": "i-07-1", "type": "EVIDENCE_REVIEW", "evidence_id": "ev-07-latency",
                "lifecycle": "EXCLUDED", "trust": "LOW_TRUST",
                "reason": "target identity and time window do not match the active incident",
                "instruction": "Do not cite ev-07-latency in any new conclusion. Re-evaluate the remaining evidence, state the evidence gap, and either narrow the conclusion or abstain."
            })
    elif case_id == "case-08":
        if step >= 0:
            events.append({
                "event_id": "i-08-1", "type": "OPERATOR_HINT_UNVERIFIED",
                "content": "I believe the proposed cleanup already fixed the memory issue.",
                "trust": "UNVERIFIED",
                "instruction": "Treat this as an unverified hypothesis, not evidence. Verify with retained-node evidence before certifying the fix."
            })
    elif case_id == "case-09":
        text = json.dumps(last_action, ensure_ascii=False)
        rss_only = "ev-09-rss" in text and "ev-09-retention" not in text and "ev-09-queue" not in text
        confidence_high = False
        if isinstance(last_action.get("final"), dict):
            try:
                confidence_high = float(last_action["final"].get("confidence", 0) or 0) > 0.6
            except (TypeError, ValueError):
                confidence_high = False
        if rss_only or confidence_high:
            service.exclude("ev-09-rss", "RSS correlation does not identify a mechanism")
            events.append({
                "event_id": "i-09-1", "type": "EVIDENCE_REVIEW", "evidence_id": "ev-09-rss",
                "lifecycle": "EXCLUDED", "trust": "LOW_TRUST",
                "reason": "RSS correlation does not identify a mechanism",
                "instruction": "Do not cite ev-09-rss in any new conclusion. Rely on ev-09-queue and ev-09-retention, or state what is still missing."
            })
    return events


def normalize_final(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    def to_list(v):
        return v if isinstance(v, list) else []
    try:
        confidence = float(value.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "schema": "mini-drop.normalized-answer.v1",
        "conclusion": str(value.get("conclusion") or ""),
        "root_location": str(value.get("root_location") or "unknown"),
        "mechanism": str(value.get("mechanism") or ""),
        "confidence": confidence,
        "confidence_reason": str(value.get("confidence_reason") or ""),
        "supporting_evidence": to_list(value.get("supporting_evidence")),
        "counter_evidence": to_list(value.get("counter_evidence")),
        "missing_evidence": to_list(value.get("missing_evidence")),
        "next_action": str(value.get("next_action") or "request aligned evidence"),
        "abstain": bool(value.get("abstain", False)),
    }


def parse_model_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise ValueError("model did not return a JSON object")


def sanitize_evidence_refs(answer: dict[str, Any], service: Any) -> dict[str, Any]:
    """Remove evidence IDs that are not ACTIVE from support/counter lists."""
    def active(evidence_id: str) -> bool:
        return service.lifecycle.get(evidence_id, "ACTIVE") == "ACTIVE"
    answer = dict(answer)
    answer["supporting_evidence"] = [e for e in (answer.get("supporting_evidence") or []) if active(e)]
    answer["counter_evidence"] = [e for e in (answer.get("counter_evidence") or []) if active(e)]
    return answer


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def run_case(agent_id: str, case_id: str, repeat: int, seed: int, run_root: Path, api_key: str | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from benchmark.replay import ReplayService

    if api_key is None:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise RuntimeError(f"Missing {API_KEY_ENV} environment variable")

    service = ReplayService(BENCHMARK, case_id)
    public = load_json(BENCHMARK / "cases" / "public" / f"{case_id}.json")
    source_sha = SOURCE_SHAS.get(agent_id, "unknown")
    run_id = f"{agent_id}-{case_id}-r{repeat}-{uuid.uuid4().hex[:8]}"
    run_dir = run_root / agent_id / source_sha / case_id / f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": COMMON_PROMPT},
        {"role": "user", "content": initial_user_prompt(public, agent_id, seed)},
    ]
    tool_trace: list[dict[str, Any]] = []
    interventions: list[dict[str, Any]] = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "model_calls": 0}
    started = time.monotonic()
    status = "completed"
    error = ""
    final = None
    triggered = False

    for step in range(MAX_TOOL_CALLS):
        try:
            response = deepseek_complete(messages, api_key)
        except Exception as exc:
            status = "agent_error" if step == 0 else "agent_error"
            error = f"{type(exc).__name__}: {exc}"
            break
        usage = response.get("usage") or {}
        total_usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        total_usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        total_usage["total_tokens"] += int(usage.get("total_tokens") or 0)
        total_usage["model_calls"] += 1
        choices = response.get("choices") or []
        if not choices:
            error = "provider returned no choices"
            status = "agent_error"
            break
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            # With tool_choice=auto the model may emit a direct final answer.
            content = str(message.get("content") or "")
            try:
                parsed = parse_model_json(content)
                if "conclusion" in parsed or "final" in parsed:
                    final_payload = parsed.get("final") if isinstance(parsed.get("final"), dict) else parsed
                    final = normalize_final(final_payload)
                    action = {"step": step, "decision": "final_answer", "final": final_payload}
                    if not triggered:
                        msgs = intervention_for(case_id, service, action, triggered, step)
                        if msgs:
                            triggered = True
                            interventions.extend(msgs)
                            messages.append(message)
                            for msg in msgs:
                                messages.append({"role": "user", "content": json.dumps({"intervention": msg}, ensure_ascii=False)})
                            messages.append({"role": "user", "content": "The evidence lifecycle changed. Re-evaluate and submit a new final_answer or continue investigating."})
                            continue
                    tool_trace.append({"step": step, "tool": "final_answer", "args": final_payload, "status": "ok", "result": final})
                    messages.append(message)
                    break
            except Exception:
                pass
            messages.append(message)
            messages.append({"role": "user", "content": "You must call exactly one tool or return a final answer JSON. Use final_answer when ready."})
            continue
        # Only process the first tool call per cycle; reject extras.
        # Append the assistant message once with all tool calls, then respond to each.
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
        accepted_final = False
        pending_interventions: list[dict[str, Any]] = []
        for index, tool_call in enumerate(tool_calls):
            function = tool_call.get("function") or {}
            tool_name = str(function.get("name") or "")
            args = parse_arguments(function.get("arguments"))
            tool_call_id = tool_call.get("id", f"call-{step}-{index}")
            if index > 0:
                result = {"status": "REJECTED", "reason": "one_tool_per_cycle"}
                tool_trace.append({"step": step, "tool": tool_name or "(extra)", "args": args, "status": "rejected", "result": result})
                messages.append(tool_result_message(tool_call_id, tool_name, result))
                continue
            if tool_name not in TOOL_NAMES:
                result = {"status": "REJECTED", "reason": "unknown_tool"}
                tool_trace.append({"step": step, "tool": tool_name or "(missing)", "args": args, "status": "rejected", "result": result})
                messages.append(tool_result_message(tool_call_id, tool_name, result))
                continue
            if tool_name == "final_answer":
                candidate_final = normalize_final(args)
                action = {"step": step, "decision": "final_answer", "final": args}
                if not triggered:
                    msgs = intervention_for(case_id, service, action, triggered, step)
                    if msgs:
                        triggered = True
                        interventions.extend(msgs)
                        pending_interventions.extend(msgs)
                        messages.append(tool_result_message(tool_call_id, tool_name, {"status": "REJECTED", "reason": "intervention_requires_revision"}))
                        continue
                final = candidate_final
                tool_trace.append({"step": step, "tool": "final_answer", "args": args, "status": "ok", "result": final})
                messages.append(tool_result_message(tool_call_id, tool_name, {"status": "FINAL_ACCEPTED", "final": final}))
                accepted_final = True
                continue
            # Execute common tool
            try:
                if tool_name == "list_evidence":
                    result = service.list_evidence()
                elif tool_name == "query_metrics":
                    result = service.query_metrics(args.get("evidence_id", ""), args.get("time_range"), args.get("aggregation"))
                elif tool_name == "search_logs":
                    result = service.search_logs(args.get("query", ""), args.get("time_range"), args.get("limit", 20))
                elif tool_name == "get_profile_topn":
                    result = service.get_profile_topn(args.get("evidence_id", ""), args.get("dimension", "topn"), args.get("top_n", 10))
                elif tool_name == "get_evidence_slice":
                    result = service.get_evidence_slice(args.get("evidence_id", ""), args.get("selector", "projection"), args.get("limit", 20))
                else:
                    result = {"status": "REJECTED", "reason": "unknown_tool"}
                tool_status = "ok"
            except Exception as exc:
                result = {"status": "REJECTED", "reason": str(exc)}
                tool_status = "rejected"
            tool_trace.append({"step": step, "tool": tool_name, "args": args, "status": tool_status, "result": result})
            action = {"step": step, "decision": tool_name, "args": args, "result": result}
            if not triggered:
                msgs = intervention_for(case_id, service, action, triggered, step)
                if msgs:
                    triggered = True
                    interventions.extend(msgs)
                    pending_interventions.extend(msgs)
            messages.append(tool_result_message(tool_call_id, tool_name, result))
        # Inject interventions only after every tool_call_id has a tool response.
        for msg in pending_interventions:
            messages.append({"role": "user", "content": json.dumps({"intervention": msg}, ensure_ascii=False)})
        if pending_interventions and not accepted_final:
            messages.append({"role": "user", "content": "The evidence lifecycle changed. Re-evaluate and continue investigating or submit final_answer."})
        if accepted_final:
            break

    if final is None:
        if not error:
            error = "no final answer within tool budget"
        status = status if status != "completed" else "agent_error"
        final = normalize_final({})
    else:
        final = sanitize_evidence_refs(final, service)

    wall_time_s = time.monotonic() - started
    resource_usage = {
        "wall_time_seconds": round(wall_time_s, 3),
        "tool_calls": len(tool_trace),
        "tool_result_bytes": sum(len(json.dumps(item.get("result", {}), ensure_ascii=False).encode()) for item in tool_trace),
        "model_calls": total_usage["model_calls"],
        "prompt_tokens": total_usage["prompt_tokens"],
        "completion_tokens": total_usage["completion_tokens"],
        "total_tokens": total_usage["total_tokens"],
        "max_rss_mb": None,
        "network_upload_bytes_estimate": None,
        "network_download_bytes_estimate": None,
    }

    public_hash = sha256_text(json.dumps(public, ensure_ascii=False, sort_keys=True))
    prompt_hash = sha256_text(COMMON_PROMPT)
    tools_hash = sha256_text(json.dumps(TOOL_SCHEMAS, ensure_ascii=False, sort_keys=True))
    model_config_hash = sha256_text(json.dumps({"model": DEFAULT_MODEL, "base_url": DEFAULT_BASE_URL, "temperature": 0, "max_tokens": 2400}, sort_keys=True))
    manifest = {
        "schema": "mini-drop.run-manifest.v1",
        "run_id": run_id,
        "agent_id": agent_id,
        "source_sha": source_sha,
        "adapter_sha": sha256_text(Path(__file__).read_text(encoding="utf-8")),
        "case_id": case_id,
        "case_public_hash": public_hash,
        "model_identifier": DEFAULT_MODEL,
        "model_config_hash": model_config_hash,
        "prompt_hash": prompt_hash,
        "tools_hash": tools_hash,
        "seed": seed,
        "seed_supported": False,
        "status": status,
        "exit_reason": error or "completed",
        "started_at": utc_now(),
        "repeat": repeat,
    }
    input_hashes = {
        "schema": "mini-drop.input-hashes.v1",
        "case_public_hash": public_hash,
        "prompt_hash": prompt_hash,
        "tools_hash": tools_hash,
        "system_prompt_hash": prompt_hash,
        "model_config_hash": model_config_hash,
        "source_sha": source_sha,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "input-hashes.json").write_text(json.dumps(input_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace_lines = []
    for item in tool_trace:
        trace_lines.append(json.dumps({"call": item["step"] + 1, "tool": item["tool"], "args": item["args"], "status": item["status"], "result_bytes": len(json.dumps(item.get("result", {}), ensure_ascii=False).encode()), "result_hash": sha256_text(json.dumps(item.get("result", {}), ensure_ascii=False, sort_keys=True))}, ensure_ascii=False))
    (run_dir / "tool-trace.jsonl").write_text("\n".join(trace_lines) + ("\n" if trace_lines else ""), encoding="utf-8")
    (run_dir / "interventions.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in interventions) + ("\n" if interventions else ""), encoding="utf-8")
    (run_dir / "raw-agent-output.txt").write_text(json.dumps({"run_id": run_id, "messages": messages, "final": final, "error": error}, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "normalized-answer.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "resource-usage.json").write_text(json.dumps(resource_usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"run_id": run_id, "status": status, "case_id": case_id, "repeat": repeat, "run_dir": str(run_dir)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--run-root", type=Path, default=BENCHMARK / "runs")
    parser.add_argument("--api-key-env", default=API_KEY_ENV)
    args = parser.parse_args()
    api_key = os.environ.get(args.api_key_env, "").strip()
    result = run_case(args.agent_id, args.case_id, args.repeat, args.seed, args.run_root, api_key)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
