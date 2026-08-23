#!/usr/bin/env python3
"""Prevent thin-adapter runs from being mislabeled as a native mainboard.

This is a report hygiene step. It does not change scores or run artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    path = ROOT / "comparisons" / "scoreboard.json"
    scoreboard = read(path)
    for result in scoreboard.get("results", []):
        result["eligible_for_mainboard"] = False
        result["eligible_for_thin_adapter_appendix"] = True
        result["track"] = "k8s_specialty" if result.get("agent_id") == "k8sgpt" else "common_replay_thin_adapter"
        result["comparability_exclusion"] = "native_runtime_not_executed"
    scoreboard["mainboard"] = {"status": "THIN_ADAPTER_ONLY", "eligible_native_run_count": 0, "thin_adapter_run_count": len(scoreboard.get("results", [])), "reason": "No run proves native upstream framework execution; all results remain appendix-only."}
    write(path, scoreboard)

    final_path = ROOT / "comparisons" / "FINAL_ACCEPTANCE.json"
    final = read(final_path)
    final["valid_runs"] = 0
    final["thin_adapter_runs"] = len(scoreboard.get("results", []))
    final["mainboard"]["status"] = "THIN_ADAPTER_ONLY"
    final["mainboard"]["reason"] = "All measured runs are appendix-only because native upstream framework execution is not established."
    write(final_path, final)

    acceptance_path = ROOT / "comparisons" / "FINAL_ACCEPTANCE.md"
    text = acceptance_path.read_text(encoding="utf-8")
    text = text.replace("valid mainboard runs: 111", "valid native mainboard runs: 0\nThin-adapter appendix runs: 111")
    text = text.replace("valid mainboard runs", "valid native mainboard runs")
    acceptance_path.write_text(text, encoding="utf-8")

    report_path = ROOT / "comparisons" / "FINAL_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace("## Mainboard (common replay)", "## Thin-adapter reference table (not a native mainboard)")
    report = report.replace("Mainboard comparability is therefore **FAIL**", "Native mainboard comparability is therefore **FAIL**")
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"thin_adapter_runs": len(scoreboard.get("results", [])), "eligible_native_run_count": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
