import sqlite3

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
print("ARTIFACTS of process_scan tasks:")
for r in c.execute("SELECT id, artifact_type, filename FROM artifacts WHERE task_id IN (SELECT id FROM tasks WHERE collector_type='process_scan')"):
    print(" ", r)
print("scan tasks:", c.execute("SELECT count(*) FROM tasks WHERE collector_type='process_scan'").fetchone())
print("reasons:", c.execute("SELECT status_reason, count(*) FROM tasks WHERE collector_type='process_scan' GROUP BY status_reason").fetchall())
print()
print("latest task attempts:")
for r in c.execute("SELECT task_id, status, result_message FROM task_attempts ORDER BY id DESC LIMIT 4"):
    print(" ", r)
