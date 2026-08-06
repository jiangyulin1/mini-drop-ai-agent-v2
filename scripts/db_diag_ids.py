import sqlite3

c = sqlite3.connect("/home/control/mini-drop/data/mini_drop.db")
print("== diagnosis_sessions 最近 6 ==")
for r in c.execute("SELECT diagnosis_id, status, created_at FROM diagnosis_sessions ORDER BY created_at DESC LIMIT 6"):
    print(" ", r)
