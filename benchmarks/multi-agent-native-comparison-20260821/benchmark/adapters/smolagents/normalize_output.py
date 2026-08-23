#!/usr/bin/env python3
"""Normalize output for the adapter (delegates to common normalizer)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from benchmark.adapters.common.normalize_output import normalize  # noqa: E402

__all__ = ["normalize"]
