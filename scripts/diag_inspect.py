"""查看最近 case 的诊断详情：探针、证据、findings。"""
import json
import ssl
import sys
import urllib.request

BASE = "https://127.0.0.1"
KEY = sys.argv[1]
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(path):
    req = urllib.request.Request(f"{BASE}{path}")
    req.add_header("X-API-Key", KEY)
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        return json.loads(r.read().decode())


cases = api("/api/v1/cases").get("data", {}).get("items", [])
for case in cases[:6]:
    cid = case.get("case_id")
    title = (case.get("title") or "")[:30]
    diag_id = case.get("diagnosis_session_id")
    print(f"\n{'='*70}\ncase {cid} | {title} | state={case.get('state')} | diag={diag_id}")
    if not diag_id:
        continue
    d = api(f"/api/v1/diagnoses/{diag_id}").get("data", {})
    print("status:", d.get("status"))
    for p in d.get("probes") or []:
        print(f"  probe: {p.get('step_id')} | {p.get('collector_type')} | status={p.get('status')} | evidence={len(p.get('evidence_refs') or [])}")
    concl = d.get("latest_conclusion") or {}
    print("classification:", concl.get("cluster_assessment", {}).get("classification"))
    print("domain:", concl.get("domain_cause", {}).get("type"), "| subtype:", concl.get("domain_cause", {}).get("subtype"))
    print("findings:", json.dumps(concl.get("findings") or [], ensure_ascii=False)[:600])
    print("limitations:", concl.get("limitations"))
