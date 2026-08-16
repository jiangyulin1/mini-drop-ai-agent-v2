"""Cross-platform helpers for content-addressed UTF-8 text files."""

from __future__ import annotations

import hashlib
from pathlib import Path


def canonical_text_bytes(path: Path) -> bytes:
    """Return UTF-8 bytes with platform-independent LF line endings."""
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_text_sha256(path: Path) -> str:
    return hashlib.sha256(canonical_text_bytes(path)).hexdigest()
