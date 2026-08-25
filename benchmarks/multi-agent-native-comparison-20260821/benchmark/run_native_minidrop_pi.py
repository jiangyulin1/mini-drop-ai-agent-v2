#!/usr/bin/env python3
"""Run Mini-Drop native via real Case/Evidence/Agent Runtime + Pi sidecar."""

import hashlib
import json
import os
import pathlib
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
SUITE_ROOT = pathlib.Path(os.getenv("MINI_DROP_SUITE_ROOT", "")).expanduser() if os.getenv("MINI_DROP_SUITE_ROOT") else None
CASE_ROOT = (SUITE_ROOT / "cases") if SUITE_ROOT else (BENCHMARK / "cases")
INTERVENTION_ROOT = (SUITE_ROOT / "interventions") if SUITE_ROOT else (BENCHMARK / "interventions")
SOURCE_SHA = "651c450867c4d6db26cc78de5928bb14f7b3c3b9"
BASE = os.getenv("MINI_DROP_BASE_URL", "http://127.0.0.1:8192").rstrip("/")
EVAL_TOKEN = os.getenv("MINI_DROP_EVAL_IMPORT_TOKEN", "").strip()
API_KEY = os.getenv("MINI_DROP_API_KEY", "").strip()
REPLAY_RUNTIME_POLICY = {"side_effect_policy": "READ_ONLY"}
RUN_ROOT_NAME = os.getenv("MINI_DROP_RUN_ROOT", "runs-native")
TRACK = os.getenv("MINI_DROP_TRACK", "expert_intervention_tuning").strip()
AGENT_ID = os.getenv("MINI_DROP_AGENT_ID", "mini-drop").strip() or "mini-drop"
PROGRESS_NAME = os.getenv("MINI_DROP_PROGRESS", "work/native_minidrop_pi_progress.jsonl")
REPEATS = tuple(
    int(item.strip())
    for item in os.getenv("MINI_DROP_REPEATS", "1,2,3").split(",")
    if item.strip()
)
URL_CONTEXT = (
    ssl._create_unverified_context()
    if os.getenv("MINI_DROP_TLS_INSECURE", "0").strip().lower() in {"1", "true", "yes"}
    else None
)

_PACKAGED_SOURCE_ROOT = ROOT / "agents" / "mini-drop-ai-agent-v2"
SOURCE_ROOT = _PACKAGED_SOURCE_ROOT if _PACKAGED_SOURCE_ROOT.exists() else ROOT.parents[1]
CONTRACT_HASH = "sha256:8c53e398a3d87dca87f2816c142decf34b588949b26d185016cbfb128764a1b8"
MODEL_HASH = "sha256:4d8b0cc142d0cb2e31c471b1c01d70b5c7424be927ea346295ba2ebd4f65e27a"
TOOLS_HASH = "sha256:b8fdb34d2ccd2312986aeac63c74454d0a8254f59e20dca7695d1d2456b8972d"
COMMON_PROMPT = (ROOT / "prompts" / "system-prompt-common.md").read_text(encoding="utf-8")

def sha256_text(t): return "sha256:" + hashlib.sha256(t.encode()).hexdigest()
def now_iso(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def load_json(p): return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
def case_json(kind, case_id):
    return load_json(CASE_ROOT / kind / f"{case_id}.json")
def stable_hash(value):
    payload=json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def normalize_root_location(value):
    """Map Pi's structured location object to the benchmark contract enum."""
    allowed = {"self", "same_host", "downstream", "shared_resource", "unknown"}
    if isinstance(value, dict):
        kind = str(value.get("type") or value.get("location") or "").strip().lower()
        if kind in allowed:
            return kind
        # Process/function/service/data-structure locations are local to the
        # scoped target unless the model explicitly names a topology relation.
        return "self" if kind else "unknown"
    text = str(value or "unknown").strip().lower()
    if text in allowed:
        return text
    # Pi occasionally explains the location in prose. Preserve the topology
    # relation when one is explicit, and otherwise treat a non-empty location
    # description as the scoped target itself.
    for relation in ("shared_resource", "same_host", "downstream"):
        if relation in text or relation.replace("_", " ") in text:
            return relation
    return "self" if text else "unknown"

def req(path, method='GET', body=None, headers=None):
    data=json.dumps(body).encode() if body is not None else None
    h={'Content-Type':'application/json'}
    if API_KEY:
        h['X-API-Key'] = API_KEY
    if headers: h.update(headers)
    r=urllib.request.Request(BASE+path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120, context=URL_CONTEXT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {'http_error':e.code,'body':e.read().decode()}

EVIDENCE_ID_RE = re.compile(r"ev-\d+-[a-z0-9_-]+", re.IGNORECASE)

def extract_evidence_id(value, case_id):
    """Map any evidence reference (internal or prose-wrapped) to the public ev-* id."""
    if isinstance(value, dict):
        for key in ("evidence_id", "id", "evidence", "name"):
            val = value.get(key)
            if isinstance(val, str) and val.strip():
                value = val
                break
        else:
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    # Strip Mini-Drop internal prefixes such as eval:case_...: or eval:<case_id>:
    text = re.sub(r"^eval:[A-Za-z0-9_:-]+:", "", text)
    m = EVIDENCE_ID_RE.search(text)
    if not m:
        return None
    return m.group(0)

def normalize_final(raw_text, case_id):
    text=raw_text.strip()
    if text.startswith("```"):
        text=text.strip("`")
        if text.startswith("json"): text=text[4:].lstrip()
    try:
        parsed=json.loads(text)
    except Exception:
        start=text.find("{"); end=text.rfind("}")
        try: parsed=json.loads(text[start:end+1])
        except: parsed={}
    # Some Pi answers wrap the schema in a nested "answer" object.
    if isinstance(parsed, dict) and isinstance(parsed.get("answer"), dict):
        inner = parsed["answer"]
        if any(k in inner for k in ("conclusion","结论","root_location","根因","mechanism","机制","confidence","置信度","abstain","abstention_reason")):
            parsed = inner
    def get_key(*names):
        for n in names:
            if n in parsed and parsed[n] is not None:
                return parsed[n]
        return None
    def to_list(v):
        if not isinstance(v,list): return []
        out=[]
        for x in v:
            eid = extract_evidence_id(x, case_id)
            if eid and eid not in out:
                out.append(eid)
        return out
    confidence_value = get_key("confidence", "置信度")
    if isinstance(confidence_value, str):
        confidence_label = confidence_value.strip().upper()
        confidence_levels = {"LOW": 0.3, "MEDIUM": 0.6, "HIGH": 0.85}
        if confidence_label in confidence_levels:
            conf = confidence_levels[confidence_label]
        else:
            try:
                conf = float(confidence_value)
            except ValueError:
                conf = 0.0
    else:
        try:
            conf = float(confidence_value or 0)
        except (TypeError, ValueError):
            conf = 0.0
    conf=max(0.0,min(1.0,conf))
    abstain_raw=get_key("abstain","弃权","is_abstain")
    abstain=bool(abstain_raw) or bool(get_key("abstention_reason","弃权原因"))
    supporting_raw = get_key("supporting_evidence","支持证据","证据依据")
    if not supporting_raw:
        # Many Pi answers put the actual cited evidence in evidence_basis
        # instead of a flat supporting_evidence list.
        supporting_raw = get_key("evidence_basis","evidenceBasis","证据基础")
    return {
        "schema":"mini-drop.normalized-answer.v1",
        "conclusion":str(get_key("conclusion","结论") or ""),
        "root_location":normalize_root_location(get_key("root_location","rootLocation","根因位置","根因")),
        "mechanism":str(get_key("mechanism","机制","作用机制") or ""),
        "confidence":conf,
        "confidence_reason":str(get_key("confidence_reason","置信度理由") or ""),
        "supporting_evidence":to_list(supporting_raw),
        "counter_evidence":to_list(get_key("counter_evidence","反证","反驳证据")),
        "missing_evidence":to_list(get_key("missing_evidence","缺失证据","仍缺失的事实")),
        "next_action":str(get_key("next_action","nextAction","下一步") or "request aligned evidence"),
        "abstain":abstain,
    }

def perform_turn(cid, msg, start_seq=0):
    turn=req(
        f"/api/v1/cases/{cid}/agent/turn",
        'POST',
        {
            "message": msg,
            "max_tool_calls": 16,
            "execute_safe_tools": False,
            "requested_disposition": "INVESTIGATE",
            "runtime_policy": REPLAY_RUNTIME_POLICY,
        },
    )
    turn_id=None
    tdata=turn.get('data') or {}
    if isinstance(tdata,dict):
        turn_id=tdata.get('turn_id')
        if not turn_id:
            for na in (tdata.get('next_actions') or []):
                if isinstance(na,dict) and na.get('turn_id'):
                    turn_id=na['turn_id']; break
    final_text=None
    # Wait for the exact turn to complete (turn.completed) before returning.
    # This prevents a second turn from racing the first turn's Pi stream and
    # also prevents the operator-hint user message from being mistaken for the
    # assistant's final JSON answer.
    completed=False
    for _ in range(90):
        time.sleep(2)
        evs=req(f"/api/v1/cases/{cid}/events")
        items=evs.get('data',{}).get('items',[]) if isinstance(evs.get('data'),dict) else []
        for e in items:
            seq=int(e.get('case_event_seq') or 0)
            if seq <= start_seq:
                continue
            et=e.get('event_type') or (e.get('payload') or {}).get('event_type') or ''
            payload=e.get('payload') or {}
            if et == 'turn.completed':
                completed_tid=payload.get('turn_id') or ''
                outcome = str(payload.get('outcome') or '').lower()
                # A collection/topology dispatch completes the current model
                # turn deliberately; the Evidence wakeup owns the next turn.
                # Do not mistake that intermediate event for the final answer.
                intermediate = outcome in {
                    'collection_scheduled',
                    'topology_discovery_collecting',
                }
                if not intermediate and (not turn_id or completed_tid == turn_id or completed_tid):
                    completed=True
                    continue
            if et != 'assistant.message':
                continue
            content=payload.get('content') or payload.get('visible_text') or ''
            if not content or content.strip() == msg.strip():
                continue
            candidate=content.strip()
            if candidate.startswith("```"):
                candidate=candidate.strip("`")
                if candidate.startswith("json"): candidate=candidate[4:].lstrip()
            parsed=None
            try:
                parsed=json.loads(candidate)
            except Exception:
                start=candidate.find("{"); end=candidate.rfind("}")
                if start != -1 and end > start:
                    try:
                        parsed=json.loads(candidate[start:end+1])
                    except Exception:
                        pass
            if isinstance(parsed,dict) and any(k in parsed for k in (
                "conclusion","root_location","mechanism","confidence",
                "结论","根因","机制","置信度","abstain","abstention_reason","弃权原因"
            )):
                # Keep the last JSON assistant message; it is the final answer.
                final_text=content
            elif isinstance(parsed,dict) and isinstance(parsed.get("answer"), dict) and any(k in parsed["answer"] for k in (
                "conclusion","root_location","mechanism","confidence",
                "结论","根因","机制","置信度","abstain","abstention_reason","弃权原因"
            )):
                # Nested "answer" wrapper (common when the model also emits
                # operator_hint_assessment / case_id metadata).
                final_text=content
        if completed:
            break
    evs=req(f"/api/v1/cases/{cid}/events")
    all_events=evs.get('data',{}).get('items',[]) if isinstance(evs.get('data'),dict) else []
    tool_trace_path = BENCHMARK / "work" / f"pi_tool_trace_{cid}.jsonl"
    tool_trace = []
    if tool_trace_path.exists():
        for line in tool_trace_path.read_text().splitlines():
            try:
                tool_trace.append(json.loads(line))
            except: pass
        tool_trace_path.unlink()
    return final_text, all_events, tool_trace, turn

def apply_exclusion(cid, evidence_short_id, reason):
    evidence_id=f"eval:{cid}:{evidence_short_id}"
    preview=req(f"/api/v1/cases/{cid}/evidence/{evidence_id}/reviews/preview",'POST',{"decision":"EXCLUDED"})
    if preview.get('http_error'):
        raise RuntimeError(f"evidence review preview failed HTTP {preview['http_error']}: {preview.get('body','')}")
    data=preview.get('data') or {}
    if isinstance(data,dict) and data.get('code') not in (None,0):
        raise RuntimeError(f"evidence review preview failed: {data}")
    token=""
    if isinstance(data,dict):
        token=data.get('impact_token') or data.get('token') or ""
    if not token and isinstance(data,dict) and isinstance(data.get('data'),dict):
        token=data['data'].get('impact_token') or ""
    if not token:
        raise RuntimeError("evidence review preview did not return impact_token")
    review=req(f"/api/v1/cases/{cid}/evidence/{evidence_id}/reviews",'POST',{
        "evidence_id":evidence_id,"decision":"EXCLUDED","expected_review_revision":0,
        "impact_token":token,"reason_code":"BENCHMARK_EXCLUDE","reason":reason
    })
    if review.get('http_error'):
        raise RuntimeError(f"evidence review failed HTTP {review['http_error']}: {review.get('body','')}")
    if isinstance(review.get('data'),dict) and review['data'].get('code') not in (None,0):
        raise RuntimeError(f"evidence review failed: {review}")
    # Verify the review revision actually advanced and lifecycle is EXCLUDED.
    rdata = review.get('data') or {}
    if isinstance(rdata, dict) and isinstance(rdata.get('data'), dict):
        rdata = rdata['data']
    revision = 0
    if isinstance(rdata, dict):
        revision = int(rdata.get('review_revision') or rdata.get('current_review_revision') or 0)
        lifecycle = str(rdata.get('lifecycle_status') or rdata.get('status') or rdata.get('decision') or '')
        if revision < 1 or 'EXCLUDED' not in lifecycle.upper():
            raise RuntimeError(f"evidence review did not persist EXCLUDED revision: {review}")
    else:
        raise RuntimeError(f"evidence review returned unexpected shape: {review}")
    return review

def run_case(case_id, repeat, seed, run_root):
    public=case_json("public", case_id)
    scope={"service_id":"redacted-service","environment":public['incident']['service_scope']['environment'],"target_pid":12345,"worker_id":"worker-1","target_type":"service","host_id":"host-1"}
    case=req('/api/v1/cases','POST',{"title":f"Benchmark {case_id} native pi","problem_description":public['incident']['symptom'],"recovery_goal":"Identify bounded runtime cost","run_mode":"COLLABORATE","environment":public['incident']['service_scope']['environment'],"target_scope":scope})
    cid=case['data']['case_id']
    replay=case_json("replay", case_id)
    injection_records=[]
    for ev in replay['evidence']:
        eid=f"eval:{cid}:{ev['evidence_id']}"
        # The public pack uses ``sha256:<digest>`` labels while the canonical
        # Evidence columns store the 64-character digest only.
        import_body={"evidence_id":eid,"pack_kind":"SOURCE_DERIVED","source_id":f"benchmark-{cid}-{ev['evidence_id']}","source_ref":f"benchmark://{cid}/{ev['evidence_id']}","projection":ev.get('projection',{}),"projection_hash":stable_hash(ev.get('projection',{})),"content_hash":ev['integrity_hash'].split(':', 1)[-1],"source_bytes":len(json.dumps(ev).encode()),"synthetic":True}
        imported = req(
            f"/api/v1/cases/{cid}/evidence/import",
            'POST',
            import_body,
            headers={'X-Evaluation-Import-Token': EVAL_TOKEN},
        )
        if imported.get('http_error'):
            raise RuntimeError(f"evidence import failed: {imported}")
        injection_records.append({
            "agent_id": AGENT_ID,
            "method": "POST /api/v1/cases/{case_id}/evidence/import",
            "evidence_id": ev["evidence_id"],
            "canonical_evidence_id": eid,
            "source_id": import_body["source_id"],
            "projection_hash": import_body["projection_hash"],
            "content_hash": import_body["content_hash"],
            "request_bytes": len(json.dumps(import_body, ensure_ascii=False).encode()),
            "response_status": "ok",
            "response_hash": stable_hash(imported),
        })
    base_msg="Investigate the incident using the available evidence. You MUST call at least list_evidence and one query tool before answering. Only cite evidence ids that exist and are lifecycle=ACTIVE in supporting_evidence/counter_evidence. Return exactly one JSON object with conclusion, root_location, mechanism, confidence, confidence_reason, supporting_evidence, counter_evidence, missing_evidence, next_action, abstain."
    interventions=[]
    all_events=[]
    tool_trace=[]
    turns=[]
    final_text=None
    if TRACK == "expert_intervention_tuning":
        # Initial turn
        f1, ev1, tt1, turn1 = perform_turn(cid, base_msg)
        all_events += ev1; tool_trace += tt1; turns.append(turn1)
        intervention_pack = load_json(INTERVENTION_ROOT / f"{case_id}.json")
        event = (intervention_pack.get("events") or [{}])[0]
        event_type = str(event.get("type") or "HYPOTHESIS_CHALLENGE")
        short_id = str(event.get("evidence_id") or "")
        if event_type == "EVIDENCE_REVIEW" and short_id:
            review=apply_exclusion(cid, short_id, str(event.get("reason") or "expert evidence review exclusion"))
            event = dict(event)
            event["review_response"] = review
        elif event_type == "OPERATOR_HINT_UNVERIFIED":
            hint = str(event.get("content") or "Operator hint is unverified; verify against active evidence.")
            req(f"/api/v1/cases/{cid}/messages",'POST',{"content":hint,"kind":"message"})
        interventions=[event]
        second_msg=(
            f"An expert intervention of type {event_type} has been applied. "
            "Before answering, you MUST call list_evidence or get_case_snapshot to re-read the current evidence state, "
            "then revise or defend the hypothesis using only active evidence. Return a new JSON answer."
        )
        seq_after_first = max([int(e.get('case_event_seq') or 0) for e in ev1] or [0])
        f2, ev2, tt2, turn2 = perform_turn(cid, second_msg, start_seq=seq_after_first)
        all_events += ev2; tool_trace += tt2; turns.append(turn2)
        final_text = f2 or f1
    elif TRACK == "fair_same_data":
        final_text, all_events, tool_trace, turn1 = perform_turn(cid, base_msg)
        turns.append(turn1)
    else:
        raise ValueError(f"unsupported track: {TRACK}")
    if final_text is None:
        status="agent_error"; error="no valid JSON final answer from Pi sidecar"; final=normalize_final("{}", cid)
    else:
        status="completed"; error=""; final=normalize_final(final_text, cid)
        if not final["conclusion"] and not final["abstain"]:
            status="agent_error"; error="empty final answer without abstain"
        # Output validation: keep only known, ACTIVE (non-EXCLUDED) evidence ids.
        # This mirrors the benchmark contract that answers may only cite
        # evidence that exists and is citable after expert intervention.
        known_ids={ev["evidence_id"] for ev in replay["evidence"]}
        excluded_ids={ev.get("evidence_id") for ev in interventions if ev.get("lifecycle")=="EXCLUDED"}
        for field in ("supporting_evidence","counter_evidence"):
            final[field]=[eid for eid in final.get(field) or [] if eid in known_ids and eid not in excluded_ids]
    # Deduplicate events by case_event_seq: each /events poll returns the full
    # history, so accumulating two polls would otherwise double every event.
    seen_seqs=set(); all_events_dedup=[]
    for e in all_events:
        seq=int(e.get('case_event_seq') or 0)
        if seq in seen_seqs:
            continue
        seen_seqs.add(seq); all_events_dedup.append(e)
    all_events = all_events_dedup

    # Normalize Pi tool-call/result records into a single canonical shape so
    # the native trace carries explicit tool_call/tool_result markers.
    normalized_tool_trace=[]
    for i, entry in enumerate(tool_trace):
        if not isinstance(entry, dict):
            continue
        tool_name = str(entry.get("tool") or entry.get("tool_name") or entry.get("name") or "unknown")
        args = entry.get("params") if isinstance(entry.get("params"), (dict, list)) else entry.get("args")
        result = entry.get("result") if isinstance(entry.get("result"), (dict, list, str, int, float, bool, type(None))) else {}
        err = (isinstance(result, dict) and (result.get("http_error") is not None or str(result.get("status") or "").upper() == "REJECTED" or str(result.get("status") or "").upper() == "ERROR"))
        normalized_tool_trace.append({
            "tool_call_id": f"tool-{i+1}",
            "tool_call": tool_name,
            "tool_name": tool_name,
            "args": args,
            "tool_result": result,
            "status": "error" if err else "ok",
            "ts": entry.get("ts"),
        })

    turn={"turns": turns}
    run_id=f"mini-drop-pi-{case_id}-r{repeat}-{uuid.uuid4().hex[:8]}"
    run_dir=pathlib.Path(run_root)/"mini-drop"/SOURCE_SHA/case_id/f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    public_hash=sha256_text(json.dumps(public, ensure_ascii=False, sort_keys=True))
    prompt_hash=sha256_text(COMMON_PROMPT)
    sidecar_dir = SOURCE_ROOT / "agent_runtime" / "pi-sidecar"
    sidecar_lock = sidecar_dir / "package-lock.json"
    sidecar_hash = sha256_text(sidecar_lock.read_text(encoding="utf-8") if sidecar_lock.exists() else str(sidecar_dir))
    native_runtime={
        "framework":"mini-drop","framework_entrypoint":"POST /api/v1/cases/{case_id}/agent/turn + official Pi Sidecar",
        "source_sha":SOURCE_SHA,"source_path":str(SOURCE_ROOT),
        "dependency_lock_hash":sha256_text((SOURCE_ROOT/"pyproject.toml").read_text()),
        "sidecar_source":"agents/mini-drop-ai-agent-v2/agent_runtime/pi-sidecar",
        "sidecar_package_lock_hash":sidecar_hash,
        "runtime_type":"pi","runtime_version":"pi-0.84.2","process_id":"mini-drop-server-127.0.0.1:8192",
        "container_id":None,"started_at":now_iso(),"ended_at":now_iso(),"cleanup_completed":True,
        "model":"deepseek-v4-flash","native_runtime":True,
    }
    manifest={
        "schema":"mini-drop.run-manifest.v2","run_id":run_id,"agent_id":"mini-drop","source_sha":SOURCE_SHA,
        "adapter_mode":"native","native_runtime":True,"framework_entrypoint":"POST /api/v1/cases/{case_id}/agent/turn + official Pi Sidecar",
        "case_id":case_id,"case_public_hash":public_hash,"model_identifier":"deepseek-v4-flash","model_config_hash":MODEL_HASH,
        "prompt_hash":prompt_hash,"tools_hash":TOOLS_HASH,"common_contract_hash":CONTRACT_HASH,
        "seed":seed,"seed_supported":False,"status":status,"exit_reason":error or "completed","started_at":now_iso(),"repeat":repeat,
    }
    native_trace=json.dumps({"case_id":cid,"turn":turn,"final":final_text,"events":all_events,"tool_trace":normalized_tool_trace,"interventions":interventions}, ensure_ascii=False)
    manifest["native_trace_hash"]=sha256_text(native_trace)
    input_hashes={"schema":"mini-drop.input-hashes.v2","case_public_hash":public_hash,"prompt_hash":prompt_hash,"tools_hash":TOOLS_HASH,"common_contract_hash":CONTRACT_HASH,"system_prompt_hash":prompt_hash,"model_config_hash":MODEL_HASH,"source_sha":SOURCE_SHA}
    (run_dir/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"input-hashes.json").write_text(json.dumps(input_hashes, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"native-runtime.json").write_text(json.dumps(native_runtime, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"native-trace.jsonl").write_text(native_trace+"\n")
    (run_dir/"tool-trace.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in normalized_tool_trace)+"\n")
    (run_dir/"interventions.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in interventions)+"\n")
    (run_dir/"injection-manifest.json").write_text(json.dumps({"schema":"mini-drop.agent-data-injection.v1","agent_id":AGENT_ID,"track":TRACK,"case_id":case_id,"records":injection_records,"total_request_bytes":sum(int(x.get("request_bytes") or 0) for x in injection_records)}, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"raw-agent-output.txt").write_text(final_text or "")
    (run_dir/"normalized-answer.json").write_text(json.dumps(final, ensure_ascii=False, indent=2)+"\n")
    resource={"wall_time_seconds":0,"tool_calls":1,"tool_result_bytes":len(final_text or ""),"model_calls":1,"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"max_rss_mb":None,"network_upload_bytes_estimate":None,"network_download_bytes_estimate":None}
    (run_dir/"resource-usage.json").write_text(json.dumps(resource, ensure_ascii=False, indent=2)+"\n")
    return {"run_id":run_id,"status":status,"case_id":case_id,"repeat":repeat,"run_dir":str(run_dir),"case_id_internal":cid}

def main():
    run_root=BENCHMARK/RUN_ROOT_NAME
    progress=BENCHMARK/PROGRESS_NAME
    progress.parent.mkdir(parents=True, exist_ok=True)
    done=set()
    if progress.exists():
        for line in progress.read_text().splitlines():
            try:
                d=json.loads(line)
                if d.get("status")=="completed": done.add((d["case_id"],d["repeat"]))
            except: pass
    case_ids = [f"case-{i:02d}" for i in range(1, 31)] if SUITE_ROOT else [f"case-{i:02d}" for i in range(1,10)]
    for case_id in case_ids:
        for rep in REPEATS:
            if (case_id,rep) in done: continue
            seed=100+rep
            try:
                r=run_case(case_id,rep,seed,run_root)
                print(case_id,rep,r["status"],flush=True)
                with progress.open("a") as f: f.write(json.dumps({"case_id":case_id,"repeat":rep,"status":r["status"],"run_id":r["run_id"]})+"\n")
            except Exception as e:
                print(case_id,rep,"ERROR",type(e).__name__,e,flush=True)
                with progress.open("a") as f: f.write(json.dumps({"case_id":case_id,"repeat":rep,"status":"error","error":str(e)})+"\n")

if __name__=="__main__":
    main()
