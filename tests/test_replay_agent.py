from __future__ import annotations

import json
from pathlib import Path

from scripts.run_replay_agent import ReplayEnvironment, api_url, public_cases, tool_specs


ROOT = Path(__file__).resolve().parents[1]


def test_public_loader_does_not_require_private_oracle(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    (suite / "public" / "replays").mkdir(parents=True)
    (suite / "public" / "cases.json").write_text(json.dumps({
        "cases": [{"case_id": "x", "prompt": "p", "target": {}, "replay": "replays/x.json"}],
    }))
    (suite / "public" / "replays" / "x.json").write_text(json.dumps({
        "case_id": "x", "available_collectors": [], "branches": {},
    }))
    assert len(public_cases(suite)) == 1


def test_replay_environment_rejects_unknown_and_reuses_duplicate() -> None:
    env = ReplayEnvironment(
        {"case_id": "x"},
        {"branches": {"sys_metrics": {
            "status": "SUCCEEDED", "evidence_id": "ev", "projection_hash": "sha256:x",
            "projection": {"cpu": 90}, "cost": 1,
        }}},
    )
    assert env.collect("unknown")["status"] == "REJECTED"
    assert env.collect("sys_metrics")["status"] == "SUCCEEDED"
    assert env.collect("sys_metrics")["status"] == "REUSED"
    assert len(env.evidence) == 1


def test_replay_environment_enforces_visible_case_budget() -> None:
    branches = {
        name: {"status": "SUCCEEDED", "evidence_id": name, "projection_hash": name,
               "projection": {}, "cost": 1}
        for name in ("a", "b")
    }
    env = ReplayEnvironment({"case_id": "x", "budget": {"max_cost": 1, "max_tool_calls": 1}}, {"branches": branches})
    assert env.collect("a")["status"] == "SUCCEEDED"
    assert env.collect("b")["reason"] == "tool_call_budget_exhausted"
    assert env.actions[-1]["accepted"] is False


def test_replay_environment_tracks_policy_rejections() -> None:
    env = ReplayEnvironment({"case_id": "x"}, {"branches": {}})
    env.proposal_rejections += 1
    assert env.proposal_rejections == 1


def test_tool_schema_locks_collector_enum() -> None:
    tools = tool_specs(["sys_metrics", "log_scan"])
    enum = tools[0]["function"]["parameters"]["properties"]["collector_id"]["enum"]
    assert enum == ["sys_metrics", "log_scan"]


def test_provider_url_normalization() -> None:
    assert api_url("https://api.deepseek.com") == "https://api.deepseek.com/v1/chat/completions"
    assert api_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"


def test_seed_suite_public_cases_load() -> None:
    assert len(public_cases(ROOT / "benchmarks" / "collector_agent_v1")) == 3
