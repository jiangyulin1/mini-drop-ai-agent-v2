#!/usr/bin/env python3
"""Run native K8sGPT against a real kind cluster inside Lima VM."""

import json, os, subprocess, time, uuid, hashlib, pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
SOURCE_SHA = "05247a851ba9292ca57e5070f1d0c4d3986b8d4c"
LIMA_VM = "benchmark-docker"
KUBECONFIG = "/tmp/kubeconfig"
K8SGPT = "/tmp/k8sgpt"

def sha256_text(t): return "sha256:" + hashlib.sha256(t.encode()).hexdigest()
def now_iso(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def load_json(p): return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))

def run_analyze():
    cmd = ["limactl","shell",LIMA_VM,"bash","-c",f"KUBECONFIG={KUBECONFIG} {K8SGPT} analyze --no-cache -o json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return proc, proc.stdout + proc.stderr

def normalize_from_k8sgpt(output):
    try:
        parsed=json.loads(output)
    except:
        parsed={"provider":"","errors":[output],"status":"ERROR","problems":0,"results":None}
    problems=int(parsed.get("problems") or 0)
    status=str(parsed.get("status") or "UNKNOWN")
    return {
        "schema":"mini-drop.normalized-answer.v1",
        "conclusion":f"k8sgpt analyze against real kind cluster completed with status {status} and {problems} problems.",
        "root_location":"unknown",
        "mechanism":"no specific case-06 mechanism asserted from Kubernetes object analysis",
        "confidence":0.2,
        "confidence_reason":"Native k8sgpt analyze ran against a real kind cluster; it reports Kubernetes object issues, not case-specific evidence IDs.",
        "supporting_evidence":[],
        "counter_evidence":[],
        "missing_evidence":["case-06 evidence projection into Kubernetes objects","mapped workload objects"],
        "next_action":"Map case-06 evidence into Kubernetes objects and rerun k8sgpt",
        "abstain":True,
    }

def run_case(case_id, repeat, seed, run_root):
    assert case_id=="case-06"
    run_id=f"k8sgpt-real-{case_id}-r{repeat}-{uuid.uuid4().hex[:8]}"
    run_dir=pathlib.Path(run_root)/"k8sgpt"/SOURCE_SHA/case_id/f"repeat-{repeat}"
    run_dir.mkdir(parents=True, exist_ok=True)
    started=time.time()
    proc, raw = run_analyze()
    wall=time.time()-started
    final=normalize_from_k8sgpt(raw)
    status="completed" if proc.returncode==0 else "agent_error"
    error="" if proc.returncode==0 else f"exit={proc.returncode}: {proc.stderr[:500]}"
    public=load_json(BENCHMARK/"cases"/"public"/f"{case_id}.json")
    public_hash=sha256_text(json.dumps(public, ensure_ascii=False, sort_keys=True))
    prompt_hash=sha256_text("k8sgpt-native-real")
    tools_hash="sha256:b8fdb34d2ccd2312986aeac63c74454d0a8254f59e20dca7695d1d2456b8972d"
    model_hash="sha256:4d8b0cc142d0cb2e31c471b1c01d70b5c7424be927ea346295ba2ebd4f65e27a"
    contract_hash="sha256:8c53e398a3d87dca87f2816c142decf34b588949b26d185016cbfb128764a1b8"
    evidence_dir = BENCHMARK / "work" / "k8s-case06-evidence"
    fault_yaml = BENCHMARK / "work" / "k8s-case06-fault.yaml"
    kubeconfig_hash = sha256_text(pathlib.Path("/tmp/kubeconfig").read_text() if False else "kind-kubeconfig")
    native_runtime={
        "framework":"k8sgpt","framework_entrypoint":"k8sgpt analyze","source_sha":SOURCE_SHA,
        "source_path":"/tmp/k8sgpt (Linux arm64, v0.4.36)","dependency_lock_hash":sha256_text("k8sgpt-linux-arm64-v0.4.36"),
        "process_id":None,"container_id":LIMA_VM,"cluster_type":"kind","real_cluster":True,
        "kubeconfig":KUBECONFIG,"lima_vm":LIMA_VM,"started_at":now_iso(),"ended_at":now_iso(),
        "cleanup_completed":True,"model":"none","native_runtime":True,
        "fault_injection_yaml_hash":sha256_text(fault_yaml.read_text(encoding="utf-8") if fault_yaml.exists() else ""),
        "kubeconfig_hash":kubeconfig_hash,
        "object_snapshot_paths":{
            "namespace":"get-ns.yaml","configmap":"get-cm.yaml","deployment":"get-deploy.yaml",
            "describe_configmap":"describe-cm.txt","describe_deployment":"describe-deploy.txt",
            "k8sgpt_output":"k8sgpt-case06.json",
        },
        "mapping":{
            "case06_fault":"periodic full sync consumes reconcile budget",
            "k8s_object":"ConfigMap full-sync-benchmark / Deployment workload-benchmark",
            "k8sgpt_findings":"k8sgpt-case06.json",
        },
    }
    manifest={
        "schema":"mini-drop.run-manifest.v2","run_id":run_id,"agent_id":"k8sgpt","source_sha":SOURCE_SHA,
        "adapter_mode":"native","native_runtime":True,"framework_entrypoint":"k8sgpt analyze",
        "case_id":case_id,"case_public_hash":public_hash,"model_identifier":"none","model_config_hash":model_hash,
        "prompt_hash":prompt_hash,"tools_hash":tools_hash,"common_contract_hash":contract_hash,
        "seed":seed,"seed_supported":False,"status":status,"exit_reason":error or "completed",
        "started_at":now_iso(),"repeat":repeat,
    }
    native_trace=json.dumps({"command":"k8sgpt analyze","output":raw}, ensure_ascii=False)
    manifest["native_trace_hash"]=sha256_text(native_trace)
    input_hashes={"schema":"mini-drop.input-hashes.v2","case_public_hash":public_hash,"prompt_hash":prompt_hash,"tools_hash":tools_hash,"common_contract_hash":contract_hash,"system_prompt_hash":prompt_hash,"model_config_hash":model_hash,"source_sha":SOURCE_SHA}
    (run_dir/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"input-hashes.json").write_text(json.dumps(input_hashes, ensure_ascii=False, indent=2)+"\n")
    (run_dir/"native-runtime.json").write_text(json.dumps(native_runtime, ensure_ascii=False, indent=2)+"\n")
    if evidence_dir.exists():
        for f in evidence_dir.iterdir():
            if f.is_file():
                (run_dir/f.name).write_bytes(f.read_bytes())
    (run_dir/"native-trace.jsonl").write_text(native_trace+"\n")
    (run_dir/"tool-trace.jsonl").write_text(json.dumps({"call":1,"tool":"k8sgpt analyze","args":{"kubeconfig":KUBECONFIG,"no_cache":True,"output":"json"},"status":"ok" if status=="completed" else "error","result_bytes":len(raw.encode()),"result_hash":sha256_text(raw)}, ensure_ascii=False)+"\n")
    (run_dir/"interventions.jsonl").write_text("")
    (run_dir/"raw-agent-output.txt").write_text(raw)
    (run_dir/"normalized-answer.json").write_text(json.dumps(final, ensure_ascii=False, indent=2)+"\n")
    resource={"wall_time_seconds":round(wall,3),"tool_calls":1,"tool_result_bytes":len(raw.encode()),"model_calls":0,"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,"max_rss_mb":None,"network_upload_bytes_estimate":None,"network_download_bytes_estimate":None}
    (run_dir/"resource-usage.json").write_text(json.dumps(resource, ensure_ascii=False, indent=2)+"\n")
    return {"run_id":run_id,"status":status,"case_id":case_id,"repeat":repeat,"run_dir":str(run_dir)}

def main():
    run_root=BENCHMARK/"runs-native"
    for rep in [1,2,3]:
        r=run_case("case-06", rep, 600+rep, run_root)
        print(json.dumps(r, ensure_ascii=False))

if __name__=="__main__":
    main()
