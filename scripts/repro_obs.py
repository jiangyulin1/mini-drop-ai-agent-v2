import sys
import json

sys.path.insert(0, ".")
import server.app._env  # noqa: F401
from server.app.database import new_session
from server.app.models import ArtifactModel, TaskModel
from server.app.diagnosis.orchestrator import DiagnosisOrchestrator

s = new_session()
task_ids = ["task_20260806_063310_1bcceb", "task_20260806_063311_018ceb", "task_20260806_063337_e3c52a"]
for tid in task_ids:
    t = s.query(TaskModel).filter_by(id=tid).first()
    if not t:
        print(tid, "task not found")
        continue
    arts = []
    for a in s.query(ArtifactModel).filter_by(task_id=tid).all():
        arts.append(a.to_dict())
    print(f"\n== {tid} | collector={t.collector_type} | artifacts={len(arts)}")
    for a in arts:
        print("  artifact:", a.get("artifact_type"), "| object_key:", (a.get("object_key") or "")[-40:])
    orch = DiagnosisOrchestrator.__new__(DiagnosisOrchestrator)
    structured = orch._structured_artifacts(arts)
    print("  structured:", [(k, type(v).__name__) for k, v, _ in structured])
    values = {k: v for k, v, _ in structured}
    obs = orch._build_task_observation("diag_test", t, values, ["ev_test"])
    log = obs.get("log")
    print("  observation log:", json.dumps(log, ensure_ascii=False)[:200] if log else None)
    print("  observation summary keys:", list((obs.get("summary") or {}).keys())[:6])
