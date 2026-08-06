import sqlite3

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
print("== 表清单 ==")
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print(tables)
print()
print("== 诊断相关任务 options ==")
for r in c.execute("SELECT id, collector_type, request_params FROM tasks WHERE collector_type IN ('log_scan','sys_metrics') ORDER BY created_at DESC LIMIT 3"):
    tid, ct, params = r
    print(" ", tid, ct, (params or "")[:200])
