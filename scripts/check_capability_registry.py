#!/usr/bin/env python3
"""Compatibility alias for the canonical registry consistency checker.

The canonical implementation lives in scripts/check_registry_consistency.py.
This alias preserves the name used in the capability-registry design and is
kept intentionally thin so there is only one source of truth.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).with_name("check_registry_consistency.py")
    raise SystemExit(subprocess.call([sys.executable, str(target), *sys.argv[1:]]))
