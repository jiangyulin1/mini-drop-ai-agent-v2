#!/usr/bin/env python3
"""Keep compact per-agent injection provenance after raw traces are removed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-root", action="append", nargs=2, metavar=("TRACK", "ROOT"), required=True)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    rows = []
    for track, raw_root in args.run_root:
        root = Path(raw_root)
        for path in sorted(root.glob("**/repeat-1/injection-manifest.json")):
            item = json.loads(path.read_text(encoding="utf-8"))
            item["track"] = track
            rows.append(item)
    result = {
        "schema": "mini-drop.agent-data-injection-aggregate.v1",
        "raw_source_uploaded": False,
        "projection_only": True,
        "agent_count": len({item.get("agent_id") for item in rows}),
        "track_count": len({item.get("track") for item in rows}),
        "run_count": len(rows),
        "total_request_bytes": sum(int(item.get("total_request_bytes") or 0) for item in rows),
        "runs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("agent_count", "track_count", "run_count", "total_request_bytes")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
