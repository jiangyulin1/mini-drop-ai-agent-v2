"""The checked-in diagnosis testset must match schema and runtime contracts."""

from pathlib import Path

from scripts.validate_testsets import validate


def test_checked_in_testsets_are_runtime_compatible():
    root = Path(__file__).resolve().parents[1] / "testsets"
    assert validate(root) == []
