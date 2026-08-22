#!/usr/bin/env python3
"""Freeze hashes used to establish adapter comparability."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    paths = {
        "testset": ROOT / "benchmark/testset-v1.json",
        "contract": ROOT / "benchmark/agent-contract-v1.json",
        "system_prompt": ROOT / "prompts/system-prompt-common.md",
        "score_prompt": ROOT / "prompts/03-score-and-report.md",
    }
    payload = {"schema": "mini-drop.frozen-hashes.v1", "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "files": {name: {"path": str(path.relative_to(ROOT)), "sha256": digest(path)} for name, path in paths.items()}}
    (ROOT / "benchmark/frozen-hashes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
