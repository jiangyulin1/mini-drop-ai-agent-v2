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
        *,
        attempt_id: str = "",
        cancelled: bool = False,
        exit_code: int = 0,
        error_code: str = "",
        request_id: str = "",
        traceparent: str = "",
        resource_usage: dict[str, Any] | None = None,
    ) -> Path:
        path = self._path(task_id, attempt_id)
        temp = path.with_suffix(".json.tmp")
        payload = {
            "schema_version": 3,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "ok": bool(ok),
            "cancelled": bool(cancelled),
            "exit_code": int(exit_code),
            "error_code": str(error_code or ""),
            "request_id": str(request_id or "")[:64],
            "traceparent": str(traceparent or "")[:64],
            "resource_usage": resource_usage or {},
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
                    payload.get("schema_version") not in {1, 2, 3}
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

    def acknowledge(self, task_id: str, attempt_id: str = "") -> None:
        try:
            self._path(task_id, attempt_id).unlink()
        except FileNotFoundError:
            pass

    def _path(self, task_id: str, attempt_id: str = "") -> Path:
        identity = f"{task_id}--{attempt_id}" if attempt_id else str(task_id)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", identity)[:220]
        if not safe:
            raise ValueError("task_id cannot be empty")
        return self.root / f"{safe}.json"
