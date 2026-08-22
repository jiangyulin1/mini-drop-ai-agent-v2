#!/usr/bin/env python3
"""Record non-secret execution prerequisites for the benchmark host."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def version(command: list[str]) -> str | None:
    try:
        return subprocess.check_output(command, stderr=subprocess.STDOUT, text=True, timeout=5).strip()[:160]
    except Exception:
        return None


def main() -> int:
    usage = shutil.disk_usage(ROOT)
    provider_names = ["MINI_DROP_AI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    payload = {
        "schema": "mini-drop.benchmark-preflight.v1",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "host": {"os": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(), "disk_free_bytes": usage.free, "disk_free_gib": round(usage.free / 1024**3, 2), "docker": version(["docker", "--version"]), "node": version(["node", "--version"]), "go": version(["go", "version"])},
        "budget": {"target_cpu_count": 2, "target_memory_gib": 4, "target_free_disk_gib": 12, "serial_only": True},
        "provider": {"configured": any(bool(os.getenv(name)) for name in provider_names), "checked_names": provider_names, "secret_values_recorded": False},
        "decision": "ready_for_replay_and_smoke" if usage.free >= 12 * 1024**3 else "resource_blocked",
        "limitations": ["Provider credential values are never read into artifacts.", "Host preflight does not prove the remote lab has the same resources."],
    }
    (ROOT / "benchmark" / "preflight.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": payload["decision"], "provider_configured": payload["provider"]["configured"], "disk_free_gib": payload["host"]["disk_free_gib"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
