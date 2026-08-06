import sqlite3

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
print("== 最近 12 个任务 ==")
for r in c.execute("SELECT id, collector_type, target_pid, status, created_at FROM tasks ORDER BY created_at DESC LIMIT 12"):
    print(r)
print()
print("== scan 任务的产物 ==")
for r in c.execute("SELECT id, artifact_type, filename, meta_json FROM artifacts WHERE task_id='task_20260806_054711_01edc8'"):
    print(r)
print()
print("== process_scan 任务数 ==")
print(c.execute("SELECT collector_type, count(*) FROM tasks GROUP BY collector_type").fetchall())
