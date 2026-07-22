#!/usr/bin/env python3
"""运行 golden scenarios 并输出 JSON/Markdown 报告。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.app.diagnosis.eval_harness import render_markdown, run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    report = run_evaluation(args.scenario_root)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "diagnosis_eval.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (args.output_dir / "diagnosis_eval.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
