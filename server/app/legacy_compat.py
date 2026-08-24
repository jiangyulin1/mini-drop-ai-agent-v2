"""Compatibility gates for retired product paths.

Legacy diagnosis remains available for historical reads and explicitly opted-in
maintenance/tests, but it must not be started by the default Evidence-native
runtime.  Keep the switch in one module so route and scheduler behavior cannot
drift apart.
"""

from __future__ import annotations

import os


LEGACY_DIAGNOSIS_ENV = "MINI_DROP_ENABLE_LEGACY_DIAGNOSIS"
LEGACY_DIAGNOSIS_DISABLED = "LEGACY_DIAGNOSIS_DISABLED"


def legacy_diagnosis_enabled() -> bool:
    """Return whether the retired DiagnosisSession write path is opted in."""

    return os.getenv(LEGACY_DIAGNOSIS_ENV, "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def legacy_diagnosis_disabled_detail() -> str:
    return (
        f"{LEGACY_DIAGNOSIS_DISABLED}: legacy DiagnosisSession is read-only by default; "
        f"set {LEGACY_DIAGNOSIS_ENV}=1 only for compatibility maintenance"
    )

