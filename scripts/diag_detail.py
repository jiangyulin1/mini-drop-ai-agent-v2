"""打印指定诊断的结论与观察证据（log 相关）。"""
import json
import ssl
import sys
import urllib.request

BASE = "https://127.0.0.1"
KEY = sys.argv[1]
DIAG = sys.argv[2]
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(path):
    req = urllib.request.Request(f"{BASE}{path}")
    req.add_header("X-API-Key", KEY)
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        return json.loads(r.read().decode())


d = api(f"/api/v1/diagnoses/{DIAG}").get("data", {})
print("status:", d.get("status"))
print("\n== probes ==")
for p in d.get("probes") or []:
    print(" ", json.dumps(p, ensure_ascii=False)[:300])
concl = d.get("latest_conclusion") or {}
print("\n== conclusion ==")
print("classification:", concl.get("cluster_assessment", {}).get("classification"))
print("domain:", concl.get("domain_cause", {}))
print("\n== findings ==")
for f in concl.get("findings") or []:
    print("  -", f.get("category"), "|", f.get("finding_type"), "|", f.get("summary", "")[:100])
print("\n== evidence ==")
for ev in d.get("evidence") or []:
    print("  -", ev.get("evidence_id"), "| kind:", ev.get("kind") or ev.get("evidence_kind"), "| task:", (ev.get("task") or {}).get("id") if isinstance(ev.get("task"), dict) else "", "| artifact:", (ev.get("artifact") or {}).get("artifact_type") if isinstance(ev.get("artifact"), dict) else "", "| payload keys:", list((ev.get("payload") or {}).keys())[:4])
print("\n== conclusion updated_at ==", concl.get("updated_at"), "| created_at:", concl.get("created_at"))
print("\n== 诊断 created/updated ==", d.get("created_at"), d.get("updated_at"))
