"""Durable Agent-side result spool.

Collector output is written atomically before NotifyResult.  If the Server is
temporarily unavailable, the next loop or a restarted Agent replays the same
envelope.  Server-side NotifyResult idempotency makes acknowledgement loss safe.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class ResultSpool:
    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        task_id: str,
        ok: bool,
        reason: str,
        artifacts: list[dict[str, Any]],
    ) -> Path:
        path = self._path(task_id)
        temp = path.with_suffix(".json.tmp")
        payload = {
            "schema_version": 1,
            "task_id": task_id,
            "ok": bool(ok),
            "reason": str(reason or ""),
            "artifacts": artifacts,
        }
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, path)
        return path

    def pending(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if (
                    payload.get("schema_version") != 1
                    or not payload.get("task_id")
                    or not isinstance(payload.get("artifacts"), list)
                ):
                    raise ValueError("invalid result envelope")
                result.append(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                quarantine = path.with_suffix(".corrupt")
                try:
                    os.replace(path, quarantine)
                except OSError:
                    pass
        return result

    def acknowledge(self, task_id: str) -> None:
        try:
            self._path(task_id).unlink()
        except FileNotFoundError:
            pass

    def _path(self, task_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id))[:128]
        if not safe:
            raise ValueError("task_id cannot be empty")
        return self.root / f"{safe}.json"
