"""The checked-in diagnosis testset must match schema and runtime contracts."""

import sys
from pathlib import Path

import pytest

from scripts.validate_testsets import validate


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="testset validation requires a POSIX bash and targets Linux fault-injection VMs",
)
def test_checked_in_testsets_are_runtime_compatible():
    root = Path(__file__).resolve().parents[1] / "testsets"
    assert validate(root) == []
