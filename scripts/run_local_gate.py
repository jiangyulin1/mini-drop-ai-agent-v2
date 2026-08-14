#!/usr/bin/env python3
"""Local gate: one command replays every documented pre-release check.

This is the E0 "本地总门禁入口" that lets any future change be compared against
the machine baseline.  Steps mirror section 19.5 of
docs/ai_agent_runtime_integration_plan.md.  Frontend steps are optional because
this machine may not have node_modules built yet; pass --frontend to include them.

Exit code: 0 when every selected step passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_step(name: str, argv: list[str]) -> bool:
    print(f"\n=== {name} ===", flush=True)
    try:
        proc = subprocess.run(argv, cwd=str(ROOT), check=False)
        ok = proc.returncode == 0
    except FileNotFoundError as exc:
        print(f"  cannot run {argv[0]}: {exc}")
        ok = False
    print(f"--- {name}: {'PASS' if ok else 'FAIL'}", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe")
                        if os.name == "nt" else "python")
    parser.add_argument("--frontend", action="store_true",
                        help="include web ci/lint/test/build steps")
    parser.add_argument("--run-id", default=f"gate-{int(time.time())}")
    args = parser.parse_args()

    py = args.python

    # Windows may only have the WSL bash stub on PATH; prefer Git Bash when present.
    bash_candidates = ["bash"]
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if os.path.isfile(candidate):
            bash_candidates.insert(0, candidate)
    bash = bash_candidates[0]
    shell_scripts = [str(p) for p in (ROOT / "deploy" / "scripts").glob("*.sh")]

    # Windows 下 npm 是 .cmd shim，subprocess 无法直接执行裸名。
    npm_bin = "npm.cmd" if os.name == "nt" else "npm"
    sidecar_dir = str(ROOT / "agent_runtime" / "pi-sidecar")

    steps: list[tuple[str, list[str]]] = [
        ("repo hygiene", [py, "scripts/check_repo_hygiene.py"]),
        ("compile proto", [py, "scripts/compile_proto.py"]),
        ("check migrations", [py, "scripts/check_migrations.py"]),
        ("ruff", [py, "-m", "ruff", "check", "server", "agent", "analyzer"]),
        ("pytest", [py, "-m", "pytest", "-q"]),
        ("pi-sidecar test", [npm_bin, "--prefix", sidecar_dir, "test"]),
        ("shell syntax", [bash, "-n", *shell_scripts] if shell_scripts else ["python", "-c", "pass"]),
    ]
    if args.frontend:
        steps += [
            ("web ci install", [npm_bin, "--prefix", "web", "ci"]),
            ("web audit:prod", [npm_bin, "--prefix", "web", "run", "audit:prod"]),
            ("web lint", [npm_bin, "--prefix", "web", "run", "lint"]),
            ("web test", [npm_bin, "--prefix", "web", "test"]),
            ("web build", [npm_bin, "--prefix", "web", "run", "build"]),
        ]

    results: dict[str, str] = {}
    failed = 0
    for name, argv in steps:
        ok = run_step(name, argv)
        results[name] = "pass" if ok else "fail"
        failed += 0 if ok else 1

    report = {
        "run_id": args.run_id,
        "finished_at": utcnow(),
        "steps": results,
        "failed": failed,
        "passed": len(results) - failed,
        "exit_code": 1 if failed else 0,
    }
    out_dir = ROOT / "reports" / "implementation" / "local-gate"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.run_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
