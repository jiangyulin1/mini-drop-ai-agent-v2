import sys

sys.path.insert(0, ".")
import server.app._env  # noqa: F401
from server.app.database import new_session
from server.app.models import ArtifactModel
from server.app import storage

s = new_session()
for aid in (163, 164):
    a = s.query(ArtifactModel).filter_by(id=aid).first()
    if not a:
        print(aid, "not found")
        continue
    print(aid, "| type:", a.artifact_type, "| object_key:", a.object_key, "| bucket:", a.bucket)
    try:
        raw = storage.read_object_bytes(a.bucket, a.object_key)
        print("  read OK bytes:", len(raw))
        import json
        data = json.loads(raw.decode("utf-8"))
        print("  json keys:", list(data.keys())[:6], "| log_files:", len(data.get("log_files") or []))
    except Exception as exc:
        print("  read FAIL:", type(exc).__name__, str(exc)[:150])
