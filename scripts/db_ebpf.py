import sqlite3
import json

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
r = c.execute("SELECT id, artifact_type, object_key, meta_json FROM artifacts WHERE task_id='task_20260806_080101_f7f411'").fetchall()
for row in r:
    print("artifact:", row[0], row[1], (row[2] or "")[-30:])
    print("meta:", (row[3] or "")[:300])
