#!/usr/bin/env python3
"""Verify the 30-case anonymous packet layout without scoring it."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "blind-review"


def main() -> int:
    failures: list[str] = []
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("cases") != 30 or manifest.get("candidate_count") != 1:
        failures.append("manifest_case_or_candidate_count")
    candidate_ids = set(manifest.get("candidate_ids") or [])
    for path in (ROOT / "input").rglob("*"):
        if path.is_file() and any(token in path.read_text(encoding="utf-8", errors="ignore").lower() for token in ("mini-drop", "deepseek", "651c450")):
            failures.append(f"identity_or_source_leak:{path.relative_to(ROOT)}")
    for track, expected in (("A", 90), ("B1", 90), ("C", 90), ("product", 3)):
        actual = len(list((ROOT / "jury-packets" / track).rglob("jury-*.json")))
        if actual != expected:
            failures.append(f"packet_count:{track}:{actual}/{expected}")
    views = list((ROOT / "input" / "candidates").rglob("event-view.json"))
    if len(views) != 60:
        failures.append(f"event_view_count:{len(views)}/60")
    if any(not re.fullmatch(r"CAND-[0-9A-F]{8}", item) for item in candidate_ids):
        failures.append("candidate_id_not_anonymous")
    status = json.loads((ROOT / "STATUS.json").read_text(encoding="utf-8"))
    result = {"status": "READY_FOR_CASE_AUDIT" if not failures else "NOT_READY", "failures": failures, "packet_counts": manifest.get("packet_counts"), "event_views": len(views), "review_status": status.get("status")}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
