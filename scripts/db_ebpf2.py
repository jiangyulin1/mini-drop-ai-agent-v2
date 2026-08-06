import sys

sys.path.insert(0, ".")
import server.app._env  # noqa: F401
import sqlite3
import json

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
rows = c.execute("SELECT id, artifact_type, object_key FROM artifacts WHERE task_id='task_20260806_080101_f7f411'").fetchall()
print("rows:", rows)
if rows:
    from server.app import storage
    raw = storage.read_object_bytes("mini-drop", rows[0][2])
    d = json.loads(raw.decode("utf-8"))
    s = d.get("summary") or {}
    print("summary keys:", list(s.keys()))
    print("p95_us:", s.get("p95_us"), "| p99_us:", s.get("p99_us"), "| avg_us:", s.get("avg_us"), "| count:", s.get("count"))
    hist = d.get("io_latency_us") or {}
    print("hist buckets:", list(hist.items())[:6])
