"""Unit tests for the live Pi strategy-matrix report helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "run_agent_strategy_matrix",
    ROOT / "scripts" / "run_agent_strategy_matrix.py",
)
matrix_script = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(matrix_script)


def test_sum_model_attempts_aggregates_tokens_cost_and_latency():
    attempts = [
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 5,
            "cache_write_tokens": 2,
            "cost": 0.001,
            "latency_ms": 300,
        },
        {
            "input_tokens": 200,
            "output_tokens": 40,
            "cache_read_tokens": 10,
            "cache_write_tokens": 4,
            "cost": 0.002,
            "latency_ms": 700,
        },
    ]
    summary = matrix_script._sum_model_attempts(attempts)
    assert summary["attempt_count"] == 2
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 60
    assert summary["cache_read_tokens"] == 15
    assert summary["cache_write_tokens"] == 6
    assert summary["cost"] == 0.003
    assert summary["latency_ms"] == 1000
