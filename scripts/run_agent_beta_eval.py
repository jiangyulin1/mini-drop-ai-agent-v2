"""Run the Agent Beta evaluation gate at the highest available realness level.

No VM means the runner stops after local suite validation and reports
AWAITING_ENVIRONMENT.  It never downgrades R4 cases to a local run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "implementation"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="public-v2")
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--mode", choices=["development", "formal"], default="development")
    parser.add_argument("--authority", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--local-contract-only", action="store_true")
    args = parser.parse_args()

    validator = [sys.executable, str(ROOT / "scripts" / "validate_agent_beta_suite.py")]
    proc = subprocess.run(validator, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout, end="")
        print(proc.stderr, end="")
        return proc.returncode

    env = {
        "vm_available": os.getenv("MINI_DROP_VM_RUNNER", "") not in {"", "0", "false"},
        "sidecar_url": os.getenv("MINI_DROP_PI_RUNTIME_URL", ""),
        "provider_configured": bool(os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")),
    }

    if args.mode == "formal":
        if args.candidate is None or not args.candidate.exists():
            print("formal mode requires a candidate manifest/receipt", file=sys.stderr)
            return 15
        if args.authority is None or not args.authority.exists():
            print("formal mode requires a read-only Acceptance Authority", file=sys.stderr)
            return 15
        status = "READY_FOR_RUN" if all(env.values()) else "AWAITING_ENVIRONMENT"
        report_type = "formal-public"
    else:
        status = "LOCAL_CONTRACT_ONLY" if args.local_contract_only else "DEVELOPMENT_EVAL"
        report_type = "development"
        if all(env.values()):
            status = "READY_FOR_RUN"

    report = {
        "suite": args.suite,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_type": report_type,
        "status": status,
        "mode": args.mode,
        "run_id": args.run_id,
        "candidate": str(args.candidate) if args.candidate else None,
        "environment": env,
        "note": "Blind Holdout scoring is NOT performed by this repository-local runner.",
        "next_action": (
            "Configure MINI_DROP_VM_RUNNER, MINI_DROP_PI_RUNTIME_URL and model credentials "
            "then invoke the external evaluator."
            if status == "AWAITING_ENVIRONMENT"
            else ("No local actions pending; VM/Authority required for P01-P10."
                  if args.mode == "development" and args.local_contract_only
                  else "Invoke the candidate-external Formal Harness.")
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "agent-beta-local-preflight.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 12 if status == "AWAITING_ENVIRONMENT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
