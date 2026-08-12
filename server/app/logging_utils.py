"""Small JSON logging helpers for the server runtime."""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|token|secret|password|authorization|cookie)", re.I)
_URL_CREDENTIAL = re.compile(r"([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@/\s]+)(@)", re.I)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")


def _redact(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(str(item_key), item_value) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(key, item) for item in value]
    if isinstance(value, str):
        value = _URL_CREDENTIAL.sub(r"\1[REDACTED]\3", value)
        return _BEARER.sub(r"\1[REDACTED]", value)
    return value


def log_event(level: str, event: str, **fields: Any) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "event": event,
        **{key: _redact(key, value) for key, value in fields.items()},
    }
    stream = sys.stderr if level in {"error", "warning"} else sys.stdout
    print(json.dumps(record, ensure_ascii=False, default=str), file=stream)
