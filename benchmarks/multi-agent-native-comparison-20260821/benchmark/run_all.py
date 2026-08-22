#!/usr/bin/env python3
"""Run the full benchmark matrix using the unified replay adapter.

Agents: mini-drop, holmesgpt, smolagents, itops-agent-platform (9x3 each),
k8sgpt (case-06 x3). Model: deepseek-v4-flash via DEEPSEEK_API_KEY.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.adapters.common.run_case import run_case, BENCHMARK

AGENTS = ["mini-drop", "holmesgpt", "smolagents", "itops-agent-platform", "k8sgpt"]
CASES = [f"case-{i:02d}" for i in range(1, 10)]
REPEATS = [1, 2, 3]
SEED_BASE = {"mini-drop": 101, "holmesgpt": 201, "smolagents": 301, "itops-agent-platform": 401, "k8sgpt": 601}


def main() -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("missing DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    run_root = BENCHMARK / "runs"
    progress_path = BENCHMARK / "work" / "run_progress.jsonl"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if progress_path.exists():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("status") == "completed":
                    done.add((item["agent_id"], item["case_id"], item["repeat"]))
            except json.JSONDecodeError:
                continue
    started_total = time.monotonic()
    for agent in AGENTS:
        cases = CASES if agent != "k8sgpt" else ["case-06"]
        for case_id in cases:
            for repeat in REPEATS:
                key = (agent, case_id, repeat)
                if key in done:
                    print(f"skip {agent} {case_id} repeat-{repeat}")
                    continue
                seed = SEED_BASE.get(agent, 0) + repeat - 1
                t0 = time.monotonic()
                try:
                    result = run_case(agent, case_id, repeat, seed, run_root, api_key)
                    elapsed = time.monotonic() - t0
                    print(f"{agent} {case_id} repeat-{repeat} -> {result['status']} ({elapsed:.1f}s)")
                    with progress_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"agent_id": agent, "case_id": case_id, "repeat": repeat, "status": result["status"], "elapsed_s": round(elapsed, 3)}) + "\n")
                except Exception as exc:
                    print(f"{agent} {case_id} repeat-{repeat} -> ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
                    with progress_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"agent_id": agent, "case_id": case_id, "repeat": repeat, "status": "error", "error": str(exc)}) + "\n")
    print(f"total elapsed {time.monotonic()-started_total:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
