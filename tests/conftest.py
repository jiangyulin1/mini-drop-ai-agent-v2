"""Keep the pre-Evidence tests explicitly on the compatibility profile.

Production defaults remain fail-closed.  Existing tests exercise historical
DiagnosisSession behavior; making that opt-in explicit here prevents the test
suite from accidentally documenting the production default as enabled.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _legacy_compatibility_test_profile():
    previous = os.environ.get("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS")
    os.environ["MINI_DROP_ENABLE_LEGACY_DIAGNOSIS"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("MINI_DROP_ENABLE_LEGACY_DIAGNOSIS", None)
        else:
            os.environ["MINI_DROP_ENABLE_LEGACY_DIAGNOSIS"] = previous

