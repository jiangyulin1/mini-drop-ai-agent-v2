#!/usr/bin/env python3
"""Native smolagents runner using upstream ToolCallingAgent.run().

This runner uses the real smolagents framework (ToolCallingAgent) with an
OpenAI-compatible DeepSeek model. It writes native-runtime.json,
native-trace.jsonl and the standard run artifacts under benchmark/runs-native.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = ROOT / "benchmark"
COMMON_PROMPT = (ROOT / "prompts" / "system-prompt-common.md").read_text(encoding="utf-8")
MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
API_KEY_ENV = "DEEPSEEK_API_KEY"
SOURCE_SHA = "e3a5b8994b301983b91c0325546e9dc82eab8cf0"
MAX_STEPS = 12

sys.path.insert(0, str(ROOT / "agents" / "smolagents" / "src"))
from smolagents import Tool  # noqa: E402


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def sanitize_evidence_refs(answer: dict[str, Any], service: Any) -> dict[str, Any]:
    def active(eid: str) -> bool:
        return service.lifecycle.get(eid, "ACTIVE") == "ACTIVE"
    answer = dict(answer)
    answer["supporting_evidence"] = [e for e in (answer.get("supporting_evidence") or []) if active(e)]
    answer["counter_evidence"] = [e for e in (answer.get("counter_evidence") or []) if active(e)]
    return answer


class InterventionRequired(RuntimeError):
    pass


class BaseEvidenceTool(Tool):
    def __init__(self, service, case_id, interventions, triggered):
        Tool.__init__(self)
        self._service = service
        self._case_id = case_id
        self._interventions = interventions
        self._triggered = triggered

    def maybe_intervene_tool(self, args: dict[str, Any], tool_name: str) -> list[dict[str, Any]]:
        if self._triggered[0]:
            return []
        events = []
        text = json.dumps(args, ensure_ascii=False)
        if self._case_id == "case-07" and "ev-07-latency" in text:
            self._service.exclude("ev-07-latency", "target identity and time window do not match the active incident")
            events.append({
                "event_id": "i-07-1", "type": "EVIDENCE_REVIEW", "evidence_id": "ev-07-latency",
                "lifecycle": "EXCLUDED", "trust": "LOW_TRUST",
                "reason": "target identity and time window do not match the active incident",
                "instruction": "Do not cite ev-07-latency in any new conclusion. Re-evaluate the remaining evidence, state the evidence gap, and either narrow the conclusion or abstain."
            })
            self._triggered[0] = True
        elif self._case_id == "case-08" and tool_name != "final_answer" and len(self._interventions) == 0:
            events.append({
                "event_id": "i-08-1", "type": "OPERATOR_HINT_UNVERIFIED",
                "content": "I believe the proposed cleanup already fixed the memory issue.",
                "trust": "UNVERIFIED",
                "instruction": "Treat this as an unverified hypothesis, not evidence. Verify with retained-node evidence before certifying the fix."
            })
            self._triggered[0] = True
        elif self._case_id == "case-09" and "ev-09-rss" in text:
            self._service.exclude("ev-09-rss", "RSS correlation does not identify a mechanism")
            events.append({
                "event_id": "i-09-1", "type": "EVIDENCE_REVIEW", "evidence_id": "ev-09-rss",
                "lifecycle": "EXCLUDED", "trust": "LOW_TRUST",
                "reason": "RSS correlation does not identify a mechanism",
                "instruction": "Do not cite ev-09-rss in any new conclusion. Rely on ev-09-queue and ev-09-retention, or state what is still missing."
            })
            self._triggered[0] = True
        if events:
            self._interventions.extend(events)
        return events

    def decorate(self, result: Any, events: list[dict[str, Any]]) -> Any:
        if events:
            return {"result": result, "intervention": events}
        return result


class ListEvidenceTool(BaseEvidenceTool):
    name = "list_evidence"
    description = "List all evidence items for the case with id, kind, summary, lifecycle, trust, integrity hash."
    inputs = {}
    output_type = "object"

    def forward(self) -> dict[str, Any]:
        events = self.maybe_intervene_tool({}, self.name)
        result = self._service.list_evidence()
        return self.decorate(result, events)


class QueryMetricsTool(BaseEvidenceTool):
    name = "query_metrics"
    description = "Query a metrics projection for one evidence id. Arguments: evidence_id, time_range (optional), aggregation (optional)."
    inputs = {
        "evidence_id": {"type": "string", "description": "Evidence id"},
        "time_range": {"type": "string", "description": "Optional time range", "nullable": True},
        "aggregation": {"type": "string", "description": "Optional aggregation", "nullable": True},
    }
    output_type = "object"

    def forward(self, evidence_id: str, time_range: str | None = None, aggregation: str | None = None) -> dict[str, Any]:
        events = self.maybe_intervene_tool({"evidence_id": evidence_id}, self.name)
        try:
            result = self._service.query_metrics(evidence_id, time_range, aggregation)
        except Exception as exc:
            result = {"status": "REJECTED", "reason": str(exc)}
        return self.decorate(result, events)


class SearchLogsTool(BaseEvidenceTool):
    name = "search_logs"
    description = "Search normalized log templates. This suite returns NO_MATCH for raw logs by design."
    inputs = {
        "query": {"type": "string", "description": "Search query"},
        "time_range": {"type": "string", "description": "Optional time range", "nullable": True},
        "limit": {"type": "integer", "description": "Optional limit", "nullable": True},
    }
    output_type = "object"

    def forward(self, query: str, time_range: str | None = None, limit: int | None = 20) -> dict[str, Any]:
        events = self.maybe_intervene_tool({"query": query}, self.name)
        result = self._service.search_logs(query, time_range, limit)
        return self.decorate(result, events)


class GetProfileTopnTool(BaseEvidenceTool):
    name = "get_profile_topn"
    description = "Get a CPU/profile top-N projection. Arguments: evidence_id, dimension (optional, default topn), top_n (optional, default 10)."
    inputs = {
        "evidence_id": {"type": "string", "description": "Evidence id"},
        "dimension": {"type": "string", "description": "Optional dimension", "nullable": True},
        "top_n": {"type": "integer", "description": "Optional top n", "nullable": True},
    }
    output_type = "object"

    def forward(self, evidence_id: str, dimension: str | None = "topn", top_n: int | None = 10) -> dict[str, Any]:
        events = self.maybe_intervene_tool({"evidence_id": evidence_id}, self.name)
        try:
            result = self._service.get_profile_topn(evidence_id, dimension, top_n)
        except Exception as exc:
            result = {"status": "REJECTED", "reason": str(exc)}
        return self.decorate(result, events)


class GetEvidenceSliceTool(BaseEvidenceTool):
    name = "get_evidence_slice"
    description = "Get a bounded slice of an evidence projection. Arguments: evidence_id, selector (optional), limit (optional)."
    inputs = {
        "evidence_id": {"type": "string", "description": "Evidence id"},
        "selector": {"type": "string", "description": "Optional selector", "nullable": True},
        "limit": {"type": "integer", "description": "Optional limit", "nullable": True},
    }
    output_type = "object"

    def forward(self, evidence_id: str, selector: str | None = "projection", limit: int | None = 20) -> dict[str, Any]:
        events = self.maybe_intervene_tool({"evidence_id": evidence_id}, self.name)
        try:
            result = self._service.get_evidence_slice(evidence_id, selector, limit)
        except Exception as exc:
            result = {"status": "REJECTED", "reason": str(exc)}
        return self.decorate(result, events)


class FinalAnswerTool(BaseEvidenceTool):
    name = "final_answer"
    description = "Stop the investigation and submit the final normalized answer."
    inputs = {
        "conclusion": {"type": "string", "description": "Conclusion"},
        "root_location": {"type": "string", "description": "self|downstream|same_host|unknown"},
        "mechanism": {"type": "string", "description": "Mechanism"},
        "confidence": {"type": "number", "description": "0..1"},
        "confidence_reason": {"type": "string", "description": "Reason"},
        "supporting_evidence": {"type": "array", "description": "Evidence ids", "items": {"type": "string"}},
        "counter_evidence": {"type": "array", "description": "Counter evidence ids", "items": {"type": "string"}},
        "missing_evidence": {"type": "array", "description": "Missing evidence descriptions", "items": {"type": "string"}},
        "next_action": {"type": "string", "description": "Next action"},
        "abstain": {"type": "boolean", "description": "Abstain"},
    }
    output_type = "object"

    def forward(self, conclusion: str, root_location: str, mechanism: str, confidence: float, confidence_reason: str,
                supporting_evidence: list, counter_evidence: list, missing_evidence: list, next_action: str, abstain: bool) -> dict[str, Any]:
        answer = {
            "conclusion": conclusion, "root_location": root_location, "mechanism": mechanism,
            "confidence": confidence, "confidence_reason": confidence_reason,
            "supporting_evidence": supporting_evidence, "counter_evidence": counter_evidence,
            "missing_evidence": missing_evidence, "next_action": next_action, "abstain": abstain,
        }
        # If intervention still not triggered and final references excluded-prone evidence, force revision.
        if not self._triggered[0]:
            events = self.maybe_intervene_tool({"final": answer}, self.name)
            if events:
                raise InterventionRequired(json.dumps({"intervention": events}, ensure_ascii=False))
        return answer


def run_case(case_id: str, repeat: int, seed: int, run_root: Path, api_key: str) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "agents" / "smolagents" / "src"))
    from benchmark.replay import ReplayService
    from smolagents import OpenAIModel, Tool, ToolCallingAgent
    from smolagents.monitoring import LogLevel

    service = ReplayService(BENCHMARK, case_id)
    public = load_json(BENCHMARK / "cases" / "public" / f"{case_id}.json")
    run_id = f"smolagents-native-{case_id}-r{repeat}-{uuid.uuid4().hex[:8]}"
    run_dir = run_root / "smolagents" / SOURCE_SHA / case_id / f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)

    interventions: list[dict[str, Any]] = []
    triggered = [False]

    tools = [
        ListEvidenceTool(service, case_id, interventions, triggered),
        QueryMetricsTool(service, case_id, interventions, triggered),
        SearchLogsTool(service, case_id, interventions, triggered),
        GetProfileTopnTool(service, case_id, interventions, triggered),
        GetEvidenceSliceTool(service, case_id, interventions, triggered),
        FinalAnswerTool(service, case_id, interventions, triggered),
    ]

    model = OpenAIModel(
        model_id=MODEL,
        api_base=BASE_URL,
        api_key=api_key,
        temperature=0,
        max_tokens=2400,
        extra_body={"thinking": {"type": "disabled"}},
    )
    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        instructions=COMMON_PROMPT,
        max_steps=MAX_STEPS,
        max_tool_threads=1,
        verbosity_level=LogLevel.OFF,
        return_full_result=True,
    )
    task = json.dumps({
        "adapter_mode": "native",
        "framework": "smolagents",
        "seed": seed,
        "incident": public["incident"],
        "evidence_index": public["evidence_index"],
        "budget": public.get("budget", {}),
        "instruction": "Use the provided tools to investigate. When ready, call final_answer with the required output schema.",
    }, ensure_ascii=False)

    started = time.monotonic()
    error = ""
    status = "completed"
    final = None
    native_trace = []
    try:
        result = agent.run(task, return_full_result=True)
        final = normalize_final(result.output)
        native_trace = result.steps if isinstance(result.steps, list) else []
        if result.state != "success":
            status = "agent_error"
            error = result.state
    except InterventionRequired as exc:
        status = "agent_error"
        error = str(exc)
        final = normalize_final({})
    except Exception as exc:
        status = "agent_error"
        error = f"{type(exc).__name__}: {exc}"
        final = normalize_final({})
        try:
            native_trace = agent.memory.get_full_steps()
        except Exception:
            native_trace = []

    wall_time_s = time.monotonic() - started
    final = sanitize_evidence_refs(final, service)

    # Build tool trace from native trace steps
    tool_trace = []
    for step in native_trace:
        if not isinstance(step, dict):
            continue
        for tc in step.get("tool_calls") or []:
            tool_trace.append({
                "step": step.get("step_number"),
                "tool": tc.get("function", {}).get("name"),
                "args": tc.get("function", {}).get("arguments"),
                "status": "ok" if step.get("error") is None else "error",
                "result": step.get("action_output"),
            })

    resource_usage = {
        "wall_time_seconds": round(wall_time_s, 3),
        "tool_calls": len(tool_trace),
        "tool_result_bytes": sum(len(json.dumps(item.get("result", {}), ensure_ascii=False).encode()) for item in tool_trace),
        "model_calls": len([s for s in native_trace if s.get("model_output_message") is not None]),
        "prompt_tokens": sum((s.get("token_usage") or {}).get("input_tokens", 0) for s in native_trace if s.get("token_usage")),
        "completion_tokens": sum((s.get("token_usage") or {}).get("output_tokens", 0) for s in native_trace if s.get("token_usage")),
        "total_tokens": 0,
        "max_rss_mb": None,
        "network_upload_bytes_estimate": None,
        "network_download_bytes_estimate": None,
    }
    resource_usage["total_tokens"] = resource_usage["prompt_tokens"] + resource_usage["completion_tokens"]

    public_hash = sha256_text(json.dumps(public, ensure_ascii=False, sort_keys=True))
    prompt_hash = sha256_text(COMMON_PROMPT)
    tools_hash = sha256_text(json.dumps([{"name": t.name, "description": t.description, "inputs": t.inputs} for t in tools], ensure_ascii=False, sort_keys=True))
    model_config_hash = sha256_text(json.dumps({"model": MODEL, "base_url": BASE_URL, "temperature": 0, "max_tokens": 2400}, sort_keys=True))
    dependency_lock = sha256_text((ROOT / "agents" / "smolagents" / "pyproject.toml").read_text(encoding="utf-8"))
    native_runtime = {
        "framework": "smolagents",
        "framework_entrypoint": "ToolCallingAgent.run()",
        "source_sha": SOURCE_SHA,
        "source_path": str(ROOT / "agents" / "smolagents"),
        "dependency_lock_hash": dependency_lock,
        "process_id": os.getpid(),
        "started_at": utc_now(),
        "ended_at": utc_now(),
        "cleanup_completed": True,
        "model": MODEL,
        "native_runtime": True,
    }
    manifest = {
        "schema": "mini-drop.run-manifest.v2",
        "run_id": run_id,
        "agent_id": "smolagents",
        "source_sha": SOURCE_SHA,
        "adapter_mode": "native",
        "native_runtime": True,
        "framework_entrypoint": "ToolCallingAgent.run()",
        "case_id": case_id,
        "case_public_hash": public_hash,
        "model_identifier": MODEL,
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
        "schema": "mini-drop.input-hashes.v2",
        "case_public_hash": public_hash,
        "prompt_hash": prompt_hash,
        "tools_hash": tools_hash,
        "system_prompt_hash": prompt_hash,
        "model_config_hash": model_config_hash,
        "source_sha": SOURCE_SHA,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "input-hashes.json").write_text(json.dumps(input_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "native-runtime.json").write_text(json.dumps(native_runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (run_dir / "native-trace.jsonl").open("w", encoding="utf-8") as fh:
        for step in native_trace:
            fh.write(json.dumps(step, ensure_ascii=False, default=str) + "\n")
    trace_lines = []
    for i, item in enumerate(tool_trace, 1):
        trace_lines.append(json.dumps({"call": i, "tool": item["tool"], "args": item["args"], "status": item["status"], "result_bytes": len(json.dumps(item.get("result", {}), ensure_ascii=False).encode()), "result_hash": sha256_text(json.dumps(item.get("result", {}), ensure_ascii=False, sort_keys=True))}, ensure_ascii=False))
    (run_dir / "tool-trace.jsonl").write_text("\n".join(trace_lines) + ("\n" if trace_lines else ""), encoding="utf-8")
    (run_dir / "interventions.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in interventions) + ("\n" if interventions else ""), encoding="utf-8")
    (run_dir / "raw-agent-output.txt").write_text(json.dumps({"run_id": run_id, "final": final, "error": error, "native_trace": native_trace}, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "normalized-answer.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "resource-usage.json").write_text(json.dumps(resource_usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"run_id": run_id, "status": status, "case_id": case_id, "repeat": repeat, "run_dir": str(run_dir)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--run-root", type=Path, default=BENCHMARK / "runs-native")
    args = parser.parse_args()
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print("missing DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    result = run_case(args.case_id, args.repeat, args.seed, args.run_root, api_key)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
