import sqlite3
import json

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
rows = c.execute("SELECT id, request_params FROM tasks WHERE collector_type='log_scan' ORDER BY created_at DESC LIMIT 2").fetchall()
for tid, params in rows:
    try:
        d = json.loads(params)
        print(tid, "| diagnosis_id:", d.get("options", {}).get("diagnosis_id"), "| step:", d.get("options", {}).get("diagnosis_step_id"))
    except Exception as e:
        print(tid, "parse err", e)
