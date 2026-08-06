"""手动模拟 run_eval 完整链路（热点下），用正确 diagnosis_id 轮询。"""
import json
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1"
KEY = sys.argv[1] if len(sys.argv) > 1 else ""
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(path, method="GET", body=None):
    req = urllib.request.Request(f"{BASE}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("X-API-Key", KEY)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            data = json.loads(r.read().decode())
        print(f"  [{method} {path}] {round(time.time()-t0,1)}s")
        return data
    except urllib.error.HTTPError as e:
        print(f"  [{method} {path}] HTTP {e.code} {round(time.time()-t0,1)}s")
        return {"http_error": e.code, "detail": e.read().decode()[:300]}


print("== 1. scan ==")
scan = api("/api/agents/linux-worker-1/processes/scan", "POST", {"query": "catalog", "timeout_sec": 30})
procs = scan.get("data", {}).get("processes", [])
target = next((p for p in procs if "catalog" in (p.get("comm") or "").lower() or "catalog" in (p.get("cmdline") or "").lower()), None)
print("  count:", len(procs), "target:", target and target["pid"])

print("== 2. 创建 case ==")
case = api("/api/v1/cases", "POST", {
    "title": "评测：手动链路2",
    "problem_description": "product-catalog 变慢，CPU 很高",
    "recovery_goal": "定位根因",
    "run_mode": "COLLABORATE",
    "environment": "production",
    "target_scope": {
        "service_id": "product-catalog",
        "instances": [{"service_id": "product-catalog", "instance_id": "i1",
                       "host_id": "linux-worker-1", "agent_id": "linux-worker-1",
                       "pid": target["pid"], "environment": "production"}],
        "dependencies": [],
    },
})
case_id = case.get("data", {}).get("case_id")
print("  case_id:", case_id)

print("== 3. 启动诊断 ==")
started = api(f"/api/v1/cases/{case_id}/diagnoses", "POST", {
    "analysis_strategy": "CONSTRAINED_HYBRID", "budget_profile": "production_safe"})
diag = started.get("data", {}).get("diagnosis") or {}
diag_id = diag.get("diagnosis_id") or started.get("data", {}).get("case", {}).get("diagnosis_session_id")
print("  diag_id:", diag_id, "| diag status:", diag.get("status"), "| keys:", list(diag.keys())[:10])

print("== 4. 轮询（最多 8 次，每 10s）==")
deadline = time.time() + 150
i = 0
while time.time() < deadline and i < 8:
    time.sleep(10)
    i += 1
    d = api(f"/api/v1/diagnoses/{diag_id}")
    data = d.get("data", d)
    if isinstance(data, dict):
        concl = data.get("latest_conclusion")
        probes = data.get("probes") or []
        waiting = [p.get("step_id") for p in probes if p.get("status") == "WAITING_APPROVAL"]
        print(f"  t+{i*10}s status={data.get('status')} concl={'Y' if concl else 'N'} "
              f"probes={len(probes)} waiting_approval={len(waiting)}")
        if waiting:
            api(f"/api/v1/diagnoses/{diag_id}/approvals", "POST",
                {"step_id": waiting[0], "decision": "approve", "scope": "single_execution", "approver_id": "eval_runner"})
            print(f"    -> approved {waiting[0]}")
        if data.get("status") in ("COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED", "BUDGET_EXHAUSTED", "FAILED", "USER_CANCELED"):
            if concl:
                print("  CONCLUSION:", json.dumps({
                    "classification": concl.get("cluster_assessment", {}).get("classification"),
                    "domain": concl.get("domain_cause", {}).get("type"),
                    "location": concl.get("root_location", {}).get("type"),
                }, ensure_ascii=False))
            break
    else:
        print(f"  t+{i*10}s resp:", str(d)[:200])
