import sqlite3

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
print("== 最近采集任务 ==")
for r in c.execute("SELECT id, collector_type, status, target_pid, created_at FROM tasks ORDER BY created_at DESC LIMIT 10"):
    print(" ", r)
print()
print("== 最新 log_scan 产物 ==")
row = c.execute("SELECT id FROM tasks WHERE collector_type='log_scan' ORDER BY created_at DESC LIMIT 1").fetchone()
if row:
    tid = row[0]
    for r in c.execute("SELECT id, artifact_type, filename FROM artifacts WHERE task_id=?", (tid,)):
        print(" ", r)
print()
print("== 最新诊断会话的 probes ==")
for r in c.execute("SELECT diagnosis_id, status FROM diagnosis_sessions ORDER BY created_at DESC LIMIT 3"):
    print(" ", r)
