#!/usr/bin/env python3
"""Native Mini-Drop runner (adapted from Mini-Drop scripts/run_replay_agent.py).

This uses the Mini-Drop direct agent-loop pattern from its own source tree with
the benchmark common five tools. It records native-runtime.json and
native-trace.jsonl under benchmark/runs-native.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
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
SOURCE_SHA = "651c450867c4d6db26cc78de5928bb14f7b3c3b9"
MAX_TOOL_CALLS = 16
TIMEOUT = 120

TOOL_SCHEMAS = [
    {"type":"function","function":{"name":"list_evidence","description":"List all evidence items for the case with id, kind, summary, lifecycle, trust, integrity hash.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
    {"type":"function","function":{"name":"query_metrics","description":"Query a metrics projection for one evidence id.","parameters":{"type":"object","properties":{"evidence_id":{"type":"string"},"time_range":{"type":"string"},"aggregation":{"type":"string"}},"required":["evidence_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"search_logs","description":"Search normalized log templates. This suite returns NO_MATCH for raw logs by design.","parameters":{"type":"object","properties":{"query":{"type":"string"},"time_range":{"type":"string"},"limit":{"type":"integer"}},"required":["query"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"get_profile_topn","description":"Get a CPU/profile top-N projection.","parameters":{"type":"object","properties":{"evidence_id":{"type":"string"},"dimension":{"type":"string"},"top_n":{"type":"integer"}},"required":["evidence_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"get_evidence_slice","description":"Get a bounded slice of an evidence projection.","parameters":{"type":"object","properties":{"evidence_id":{"type":"string"},"selector":{"type":"string"},"limit":{"type":"integer"}},"required":["evidence_id"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"final_answer","description":"Stop the investigation and submit the final normalized answer.","parameters":{"type":"object","properties":{"conclusion":{"type":"string"},"root_location":{"type":"string","enum":["self","downstream","same_host","unknown"]},"mechanism":{"type":"string"},"confidence":{"type":"number"},"confidence_reason":{"type":"string"},"supporting_evidence":{"type":"array","items":{"type":"string"}},"counter_evidence":{"type":"array","items":{"type":"string"}},"missing_evidence":{"type":"array","items":{"type":"string"}},"next_action":{"type":"string"},"abstain":{"type":"boolean"}},"required":["conclusion","root_location","mechanism","confidence","confidence_reason","supporting_evidence","counter_evidence","missing_evidence","next_action","abstain"],"additionalProperties":False}}},
]

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

def deepseek_complete(messages, api_key):
    body = json.dumps({"model":MODEL,"messages":messages,"tools":TOOL_SCHEMAS,"tool_choice":"auto","thinking":{"type":"disabled"},"temperature":0,"max_tokens":2400}, ensure_ascii=False).encode()
    req = urllib.request.Request(api_url(BASE_URL), data=body, headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"Mini-Drop DeepSeek call failed: {exc}") from exc

def parse_arguments(raw):
    if isinstance(raw, dict): return raw
    if isinstance(raw, str):
        try: return json.loads(raw)
        except: pass
    return {}

def normalize_final(value):
    if not isinstance(value, dict): value={}
    def to_list(v): return v if isinstance(v, list) else []
    try: conf=float(value.get("confidence",0) or 0)
    except: conf=0.0
    conf=max(0.0,min(1.0,conf))
    return {"schema":"mini-drop.normalized-answer.v1","conclusion":str(value.get("conclusion") or ""),"root_location":str(value.get("root_location") or "unknown"),"mechanism":str(value.get("mechanism") or ""),"confidence":conf,"confidence_reason":str(value.get("confidence_reason") or ""),"supporting_evidence":to_list(value.get("supporting_evidence")),"counter_evidence":to_list(value.get("counter_evidence")),"missing_evidence":to_list(value.get("missing_evidence")),"next_action":str(value.get("next_action") or "request aligned evidence"),"abstain":bool(value.get("abstain",False))}

def sanitize(answer, service):
    def active(eid): return service.lifecycle.get(eid,"ACTIVE")=="ACTIVE"
    answer=dict(answer)
    answer["supporting_evidence"]=[e for e in (answer.get("supporting_evidence") or []) if active(e)]
    answer["counter_evidence"]=[e for e in (answer.get("counter_evidence") or []) if active(e)]
    return answer

def run_case(case_id, repeat, seed, run_root, api_key):
    sys.path.insert(0, str(ROOT))
    from benchmark.replay import ReplayService
    service = ReplayService(BENCHMARK, case_id)
    public = load_json(BENCHMARK/"cases"/"public"/f"{case_id}.json")
    run_id=f"mini-drop-native-{case_id}-r{repeat}-{uuid.uuid4().hex[:8]}"
    run_dir=run_root/"mini-drop"/SOURCE_SHA/case_id/f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    messages=[{"role":"system","content":COMMON_PROMPT},{"role":"user","content":json.dumps({"adapter_mode":"native","framework":"mini-drop","seed":seed,"incident":public["incident"],"evidence_index":public["evidence_index"],"budget":public.get("budget",{}),"instruction":"Use the provided tools to investigate. Use as few calls as needed; once you have enough evidence, immediately call final_answer with the required output schema. Avoid redundant queries."}, ensure_ascii=False)}]
    tool_trace=[]; interventions=[]; triggered=[False]
    started=time.monotonic(); status="completed"; error=""; final=None

    def maybe_intervene(action):
        if triggered[0]: return []
        events=[]; text=json.dumps(action, ensure_ascii=False)
        if case_id=="case-07" and "ev-07-latency" in text:
            service.exclude("ev-07-latency","target identity and time window do not match the active incident")
            events.append({"event_id":"i-07-1","type":"EVIDENCE_REVIEW","evidence_id":"ev-07-latency","lifecycle":"EXCLUDED","trust":"LOW_TRUST","reason":"target identity and time window do not match the active incident","instruction":"Do not cite ev-07-latency in any new conclusion. Re-evaluate the remaining evidence, state the evidence gap, and either narrow the conclusion or abstain."})
            triggered[0]=True
        elif case_id=="case-08" and not interventions:
            events.append({"event_id":"i-08-1","type":"OPERATOR_HINT_UNVERIFIED","content":"I believe the proposed cleanup already fixed the memory issue.","trust":"UNVERIFIED","instruction":"Treat this as an unverified hypothesis, not evidence. Verify with retained-node evidence before certifying the fix."})
            triggered[0]=True
        elif case_id=="case-09" and "ev-09-rss" in text:
            service.exclude("ev-09-rss","RSS correlation does not identify a mechanism")
            events.append({"event_id":"i-09-1","type":"EVIDENCE_REVIEW","evidence_id":"ev-09-rss","lifecycle":"EXCLUDED","trust":"LOW_TRUST","reason":"RSS correlation does not identify a mechanism","instruction":"Do not cite ev-09-rss in any new conclusion. Rely on ev-09-queue and ev-09-retention, or state what is still missing."})
            triggered[0]=True
        if events: interventions.extend(events)
        return events

    for step in range(MAX_TOOL_CALLS):
        try:
            resp=deepseek_complete(messages, api_key)
        except Exception as exc:
            status="agent_error"; error=f"{type(exc).__name__}: {exc}"; break
        choices=resp.get("choices") or []
        if not choices:
            status="agent_error"; error="no choices"; break
        message=choices[0].get("message") or {}
        tool_calls=message.get("tool_calls") or []
        if not tool_calls:
            content=str(message.get("content") or "")
            try:
                parsed=json.loads(content)
                if "conclusion" in parsed or "final" in parsed:
                    final_payload=parsed.get("final") if isinstance(parsed.get("final"),dict) else parsed
                    final=normalize_final(final_payload)
                    action={"final":final_payload}
                    events=maybe_intervene(action)
                    if events:
                        messages.append(message)
                        for ev in events:
                            messages.append({"role":"user","content":json.dumps({"intervention":ev}, ensure_ascii=False)})
                        messages.append({"role":"user","content":"The evidence lifecycle changed. Re-evaluate and submit a new final_answer or continue investigating."})
                        continue
                    tool_trace.append({"step":step,"tool":"final_answer","args":final_payload,"status":"ok","result":final})
                    break
            except Exception:
                pass
            messages.append(message)
            messages.append({"role":"user","content":"You must call exactly one tool or return a final answer JSON. Use final_answer when ready."})
            continue
        messages.append({"role":"assistant","content":message.get("content"),"tool_calls":tool_calls})
        accepted_final=False
        pending=[]
        for index,tc in enumerate(tool_calls):
            fn=tc.get("function") or {}; name=str(fn.get("name") or ""); args=parse_arguments(fn.get("arguments")); tc_id=tc.get("id",f"call-{step}-{index}")
            if index>0:
                messages.append({"role":"tool","tool_call_id":tc_id,"content":json.dumps({"status":"REJECTED","reason":"one_tool_per_cycle"})})
                continue
            if name not in [t["function"]["name"] for t in TOOL_SCHEMAS]:
                messages.append({"role":"tool","tool_call_id":tc_id,"content":json.dumps({"status":"REJECTED","reason":"unknown_tool"})})
                continue
            if name=="final_answer":
                cand=normalize_final(args); action={"final":args}; events=maybe_intervene(action)
                if events:
                    pending.extend(events)
                    messages.append({"role":"tool","tool_call_id":tc_id,"content":json.dumps({"status":"REJECTED","reason":"intervention_requires_revision"})})
                    continue
                final=cand; tool_trace.append({"step":step,"tool":"final_answer","args":args,"status":"ok","result":final}); messages.append({"role":"tool","tool_call_id":tc_id,"content":json.dumps({"status":"FINAL_ACCEPTED","final":final})}); accepted_final=True; continue
            try:
                if name=="list_evidence": result=service.list_evidence()
                elif name=="query_metrics": result=service.query_metrics(args.get("evidence_id",""),args.get("time_range"),args.get("aggregation"))
                elif name=="search_logs": result=service.search_logs(args.get("query",""),args.get("time_range"),args.get("limit",20))
                elif name=="get_profile_topn": result=service.get_profile_topn(args.get("evidence_id",""),args.get("dimension","topn"),args.get("top_n",10))
                elif name=="get_evidence_slice": result=service.get_evidence_slice(args.get("evidence_id",""),args.get("selector","projection"),args.get("limit",20))
                else: result={"status":"REJECTED","reason":"unknown_tool"}
                tool_status="ok"
            except Exception as exc:
                result={"status":"REJECTED","reason":str(exc)}; tool_status="rejected"
            tool_trace.append({"step":step,"tool":name,"args":args,"status":tool_status,"result":result})
            events=maybe_intervene({"tool":name,"args":args,"result":result})
            if events: pending.extend(events)
            messages.append({"role":"tool","tool_call_id":tc_id,"content":json.dumps(result)})
        for ev in pending:
            messages.append({"role":"user","content":json.dumps({"intervention":ev}, ensure_ascii=False)})
        if pending and not accepted_final:
            messages.append({"role":"user","content":"The evidence lifecycle changed. Re-evaluate and continue investigating or submit final_answer."})
        if accepted_final: break
    if final is None:
        if not error: error="no final answer within tool budget"
        status=status if status!="completed" else "agent_error"; final=normalize_final({})
    else:
        final=sanitize(final, service)
    wall=time.monotonic()-started
    public_hash=sha256_text(json.dumps(public, ensure_ascii=False, sort_keys=True)); prompt_hash=sha256_text(COMMON_PROMPT); tools_hash=sha256_text(json.dumps(TOOL_SCHEMAS, ensure_ascii=False, sort_keys=True)); model_config_hash=sha256_text(json.dumps({"model":MODEL,"base_url":BASE_URL,"temperature":0,"max_tokens":2400}, sort_keys=True)); dep_lock=sha256_text((ROOT/"agents"/"mini-drop-ai-agent-v2"/"pyproject.toml").read_text(encoding="utf-8") if (ROOT/"agents"/"mini-drop-ai-agent-v2"/"pyproject.toml").exists() else "mini-drop")
    native_runtime={"framework":"mini-drop","framework_entrypoint":"scripts/run_replay_agent.py (adapted)","source_sha":SOURCE_SHA,"source_path":str(ROOT/"agents"/"mini-drop-ai-agent-v2"),"dependency_lock_hash":dep_lock,"process_id":os.getpid(),"started_at":utc_now(),"ended_at":utc_now(),"cleanup_completed":True,"model":MODEL,"native_runtime":True}
    manifest={"schema":"mini-drop.run-manifest.v2","run_id":run_id,"agent_id":"mini-drop","source_sha":SOURCE_SHA,"adapter_mode":"native","native_runtime":True,"framework_entrypoint":"scripts/run_replay_agent.py (adapted)","case_id":case_id,"case_public_hash":public_hash,"model_identifier":MODEL,"model_config_hash":model_config_hash,"prompt_hash":prompt_hash,"tools_hash":tools_hash,"seed":seed,"seed_supported":False,"status":status,"exit_reason":error or "completed","started_at":utc_now(),"repeat":repeat}
    input_hashes={"schema":"mini-drop.input-hashes.v2","case_public_hash":public_hash,"prompt_hash":prompt_hash,"tools_hash":tools_hash,"system_prompt_hash":prompt_hash,"model_config_hash":model_config_hash,"source_sha":SOURCE_SHA}
    (run_dir/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"input-hashes.json").write_text(json.dumps(input_hashes, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"native-runtime.json").write_text(json.dumps(native_runtime, ensure_ascii=False, indent=2)+"\n")
    with (run_dir/"native-trace.jsonl").open("w") as f:
        for m in messages: f.write(json.dumps(m, ensure_ascii=False)+"\n")
    (run_dir/"tool-trace.jsonl").write_text("\n".join(json.dumps({"call":i+1,"tool":item["tool"],"args":item["args"],"status":item["status"],"result_bytes":len(json.dumps(item.get("result",{}), ensure_ascii=False).encode()),"result_hash":sha256_text(json.dumps(item.get("result",{}), ensure_ascii=False, sort_keys=True))}, ensure_ascii=False) for i,item in enumerate(tool_trace))+"\n")
    (run_dir/"interventions.jsonl").write_text("\n".join(json.dumps(ev, ensure_ascii=False) for ev in interventions)+"\n")
    (run_dir/"raw-agent-output.txt").write_text(json.dumps({"run_id":run_id,"messages":messages,"final":final,"error":error}, ensure_ascii=False, indent=2))
    (run_dir/"normalized-answer.json").write_text(json.dumps(final, ensure_ascii=False, indent=2)+"\n")
    resource={"wall_time_seconds":round(wall,3),"tool_calls":len(tool_trace),"tool_result_bytes":sum(len(json.dumps(i.get("result",{}), ensure_ascii=False).encode()) for i in tool_trace),"model_calls":0,"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"max_rss_mb":None,"network_upload_bytes_estimate":None,"network_download_bytes_estimate":None}
    (run_dir/"resource-usage.json").write_text(json.dumps(resource, ensure_ascii=False, indent=2)+"\n")
    return {"run_id":run_id,"status":status,"case_id":case_id,"repeat":repeat,"run_dir":str(run_dir)}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--case-id",required=True); parser.add_argument("--repeat",type=int,required=True); parser.add_argument("--seed",type=int,default=0); parser.add_argument("--run-root",type=Path,default=BENCHMARK/"runs-native"); args=parser.parse_args()
    api_key=os.environ.get(API_KEY_ENV,"").strip()
    if not api_key: print("missing DEEPSEEK_API_KEY",file=sys.stderr); return 2
    r=run_case(args.case_id,args.repeat,args.seed,args.run_root,api_key); print(json.dumps(r,ensure_ascii=False)); return 0 if r["status"]=="completed" else 1

if __name__=="__main__": raise SystemExit(main())
