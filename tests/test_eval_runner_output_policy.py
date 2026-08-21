from pathlib import Path

from scripts.run_github_pr_attribution_eval import (
    _default_output_dir as github_pr_output_dir,
)
from scripts.run_unknown_topology_e2e import (
    _default_output_dir as topology_output_dir,
)
from scripts.run_unknown_topology_pi_e2e import (
    _apply_runtime_retention,
    _default_output_dir as topology_pi_output_dir,
)


def test_eval_runner_defaults_stay_under_repository_report_root():
    timestamp = "20260821-120000"
    expected_root = Path(__file__).resolve().parents[1] / "reports" / "eval"

    assert github_pr_output_dir(timestamp) == (
        expected_root / f"github-pr-attribution-{timestamp}"
    )
    assert topology_output_dir(timestamp) == (
        expected_root / f"unknown-topology-{timestamp}"
    )
    assert topology_pi_output_dir(timestamp) == (
        expected_root / f"unknown-topology-pi-{timestamp}"
    )


def test_pi_success_removes_runtime_by_default(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "e2e.sqlite").write_text("database", encoding="utf-8")
    (runtime_dir / "pi-events.jsonl").write_text("event", encoding="utf-8")
    (runtime_dir / "sidecar.log").write_text("raw", encoding="utf-8")
    (runtime_dir / "agent-spool").mkdir()

    retention = _apply_runtime_retention(
        runtime_dir,
        succeeded=True,
        keep_runtime=False,
    )

    assert retention == {
        "keep_requested": False,
        "retained": False,
        "reason": "successful_run_cleanup",
    }
    assert not runtime_dir.exists()


def test_pi_failure_retains_runtime_for_diagnosis(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    retention = _apply_runtime_retention(
        runtime_dir,
        succeeded=False,
        keep_runtime=False,
    )

    assert retention["retained"] is True
    assert retention["reason"] == "failed_run_diagnostics"
    assert runtime_dir.is_dir()


def test_pi_keep_runtime_overrides_success_cleanup(tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    retention = _apply_runtime_retention(
        runtime_dir,
        succeeded=True,
        keep_runtime=True,
    )

    assert retention["keep_requested"] is True
    assert retention["retained"] is True
    assert retention["reason"] == "explicit_keep"
    assert runtime_dir.is_dir()
