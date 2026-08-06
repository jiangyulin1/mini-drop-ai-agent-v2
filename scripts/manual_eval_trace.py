"""手动模拟 run_eval 完整链路，打印每步 API 返回，定位故障。"""
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
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "detail": e.read().decode()[:200]}


print("== 1. scan ==")
scan = api("/api/agents/linux-worker-1/processes/scan", "POST", {"query": "catalog", "timeout_sec": 20})
procs = scan.get("data", {}).get("processes", [])
print("status:", scan.get("data", {}).get("status"), "count:", len(procs))
if procs:
    print("first:", procs[0].get("pid"), procs[0].get("comm"), procs[0].get("cmdline", "")[:40])

print("== 2. 创建 case ==")
case = api("/api/v1/cases", "POST", {
    "title": "评测：手动链路",
    "problem_description": "product-catalog 变慢，CPU 很高",
    "recovery_goal": "定位根因",
    "run_mode": "COLLABORATE",
    "environment": "production",
    "target_scope": {
        "service_id": "product-catalog",
        "instances": [{"service_id": "product-catalog", "instance_id": "i1",
                       "host_id": "linux-worker-1", "agent_id": "linux-worker-1",
                       "pid": procs[0]["pid"], "environment": "production"}],
        "dependencies": [],
    },
}) if procs else None
if case is None:
    print("scan 为空，无法继续")
    sys.exit(1)
case_id = case.get("data", {}).get("case_id")
print("case_id:", case_id)
print("keys:", list(case.get("data", {}).keys()))

print("== 3. 启动诊断 ==")
started = api(f"/api/v1/cases/{case_id}/diagnoses", "POST", {
    "analysis_strategy": "CONSTRAINED_HYBRID", "budget_profile": "production_safe"})
print("resp keys:", list(started.get("data", {}).keys()) if isinstance(started.get("data"), dict) else started)
print("data:", json.dumps(started.get("data"), ensure_ascii=False)[:400])

diag_id = started.get("data", {}).get("diagnosis_id") or started.get("data", {}).get("session_id") or case_id
print("diag_id:", diag_id)

print("== 4. 轮询 3 次（每 10s）==")
for i in range(3):
    time.sleep(10)
    d = api(f"/api/v1/diagnoses/{diag_id}")
    data = d.get("data", d)
    if isinstance(data, dict):
        print(f"  t+{(i+1)*10}s status={data.get('status')} conclusion={'Y' if data.get('latest_conclusion') else 'N'} probes={len(data.get('probes') or [])}")
    else:
        print(f"  t+{(i+1)*10}s resp:", str(d)[:200])
