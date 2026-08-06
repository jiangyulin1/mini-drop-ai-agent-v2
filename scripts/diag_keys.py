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

req = urllib.request.Request(f"{BASE}/api/v1/diagnoses/{DIAG}")
req.add_header("X-API-Key", KEY)
with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
    d = json.loads(r.read().decode())["data"]

print("detail keys:", list(d.keys()))
for k in ("observations", "task_observations", "tasks"):
    if k in d:
        print(f"  {k}: {len(d[k])} items")
        if d[k]:
            print("  sample:", json.dumps(d[k][0], ensure_ascii=False)[:250])
