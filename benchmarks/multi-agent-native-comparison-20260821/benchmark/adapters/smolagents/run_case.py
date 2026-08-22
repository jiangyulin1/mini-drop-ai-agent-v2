#!/usr/bin/env python3
"""Adapter entrypoint for smolagents.

This is a thin adapter: it delegates to the benchmark-owned unified replay
runner and fixes the agent id. It does not execute the upstream runtime.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from benchmark.adapters.common.run_case import run_case, BENCHMARK  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--run-root", type=Path, default=BENCHMARK / "runs")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    args = parser.parse_args()
    api_key = os.environ.get(args.api_key_env, "").strip()
    result = run_case("smolagents", args.case_id, args.repeat, args.seed, args.run_root, api_key)
    print(result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
