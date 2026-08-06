"""拉取 4 个场景的真实诊断结论，作为客户演示素材。"""
import json
import ssl
import sys
import urllib.request

BASE = "https://127.0.0.1"
KEY = sys.argv[1]
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

DIAGS = {
    "cpu-hotspot": "diag_session_20260806_082858_38415692",
    "pg-down": "diag_session_20260806_083228_e5ff96c0",
    "io-contention": "diag_session_20260806_083610_e4d9c8b5",
    "no-fault": "diag_session_20260806_083945_6b8de119",
}


def api(path):
    req = urllib.request.Request(f"{BASE}{path}")
    req.add_header("X-API-Key", KEY)
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        return json.loads(r.read().decode())


def shorten(text, n=160):
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


out = {}
for label, diag_id in DIAGS.items():
    try:
        d = api(f"/api/v1/diagnoses/{diag_id}").get("data", {})
        c = d.get("latest_conclusion") or {}
        out[label] = {
            "diagnosis_id": diag_id,
            "status": d.get("status"),
            "classification": (c.get("cluster_assessment") or {}).get("classification"),
            "confidence": (c.get("cluster_assessment") or {}).get("confidence_level"),
            "root_location": (c.get("root_location") or {}),
            "domain_cause": (c.get("domain_cause") or {}),
            "summary": shorten(c.get("summary"), 220),
            "findings": [
                {
                    "category": f.get("category"),
                    "finding_type": f.get("finding_type"),
                    "summary": shorten(f.get("summary"), 150),
                    "severity": f.get("severity"),
                }
                for f in (c.get("findings") or [])[:4]
            ],
            "recommendations": [
                {"title": r.get("title") or r.get("action_id"), "desc": shorten(r.get("description") or r.get("reason"), 140)}
                for r in (c.get("recommendations") or [])[:3]
            ],
            "next_best_action": (c.get("next_best_action") or {}),
            "evidence_count": len(d.get("evidence") or []),
            "probes": [(p.get("probe_id"), p.get("status")) for p in (d.get("probes") or [])],
        }
    except Exception as exc:
        out[label] = {"error": str(exc)}

with open("/tmp/demo_diags.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps({k: {"classification": v.get("classification"), "findings": len(v.get("findings", []))} for k, v in out.items()}, ensure_ascii=False, indent=1))
