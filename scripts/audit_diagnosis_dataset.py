#!/usr/bin/env python3
"""Audit a diagnosis benchmark before spending time on active AI trials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.app.diagnosis.benchmark_audit import (  # noqa: E402
    BenchmarkAuditError,
    audit_dataset,
    render_markdown,
)


DEFAULT_ENVIRONMENT = REPO_ROOT / "benchmarks" / "environments" / "hyperv_three_node.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit diagnosis-case quality and Mini-Drop environment readiness."
    )
    parser.add_argument("dataset_root", type=Path, help="Directory containing manifest.json and cases/")
    parser.add_argument("--environment", type=Path, default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--minimum-score", type=float, default=None,
        help="Exit with code 2 when the readiness score is below this threshold.",
    )
    parser.add_argument(
        "--fail-on-blocker", action="store_true",
        help="Exit with code 2 when any case contains a blocking specification defect.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_dataset(args.dataset_root, args.environment)
    except BenchmarkAuditError as exc:
        print(f"benchmark audit error: {exc}", file=sys.stderr)
        return 1

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "audit-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.output_dir / "audit-report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    below_threshold = args.minimum_score is not None and report["score"] < args.minimum_score
    has_blocker = args.fail_on_blocker and report["blocking_finding_count"] > 0
    return 2 if below_threshold or has_blocker else 0


if __name__ == "__main__":
    raise SystemExit(main())
