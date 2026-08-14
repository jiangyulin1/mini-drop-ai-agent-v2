"""E0 AgentRuntimePort contract and feature-flag tests.

These verify the deterministic baseline is default, restart-safe and that
pi mode fails closed when the sidecar URL is absent.
"""

from __future__ import annotations

import os
from unittest import mock

from server.app.agent_runtime.config import (
    AgentRuntimeMode,
    agent_flags,
    runtime_mode,
)
from server.app.agent_runtime.deterministic import DeterministicAgentRuntime
from server.app.agent_runtime.dispatcher import (
    active_runtime_info,
    get_runtime,
    reset_runtime,
)
from server.app.agent_runtime.port import AgentTurnInput, CaseContextSnapshot


def _snapshot(case_id: str = "case-test") -> CaseContextSnapshot:
    return CaseContextSnapshot(
        case_id=case_id,
        tenant_id="tenant-test",
        case_goal="定位支付超时根因",
        autonomy_mode="COLLABORATE",
        plan_revision=0,
        scope_revision=0,
    )


def test_runtime_mode_defaults_to_deterministic():
    assert runtime_mode() == AgentRuntimeMode.DETERMINISTIC


def test_runtime_mode_accepts_explicit_values():
    with mock.patch.dict(os.environ, {"MINI_DROP_AGENT_RUNTIME": "pi_shadow"}, clear=False):
        assert runtime_mode() == AgentRuntimeMode.PI_SHADOW
    with mock.patch.dict(os.environ, {"MINI_DROP_AGENT_RUNTIME": "pi"}, clear=False):
        assert runtime_mode() == AgentRuntimeMode.PI


def test_unknown_runtime_mode_falls_back_to_deterministic():
    with mock.patch.dict(os.environ, {"MINI_DROP_AGENT_RUNTIME": "chaos"}, clear=False):
        assert runtime_mode() == AgentRuntimeMode.DETERMINISTIC


def test_deterministic_runtime_is_resumable_and_returns_identical_binding():
    reset_runtime()
    runtime = get_runtime()
    assert isinstance(runtime, DeterministicAgentRuntime)
    first = runtime.start_or_resume(_snapshot("case-a"))
    second = runtime.start_or_resume(_snapshot("case-a"))
    assert first.runtime_session_id == second.runtime_session_id
    assert first.runtime_generation == 1
    state = runtime.get_state("case-a")
    assert state.status == "READY"
    assert state.runtime_version == "deterministic-1.0"


def test_deterministic_submit_turn_is_accepted_without_a_model():
    runtime = get_runtime()
    result = runtime.submit_turn("case-a", AgentTurnInput(case_id="case-a", message="继续调查"))
    assert result.accepted is True
    assert result.mode == "deterministic"


def test_pi_mode_fails_closed_without_sidecar_url():
    reset_runtime()  # 清除之前测试缓存的 deterministic 实例
    with mock.patch.dict(os.environ, {
        "MINI_DROP_AGENT_RUNTIME": "pi",
        "MINI_DROP_PI_RUNTIME_URL": "",
    }, clear=False):
        info = active_runtime_info()
        assert info["mode"] == "pi"
        assert info["ready"] is False
        assert "MINI_DROP_PI_RUNTIME_URL" in info["error"]


def test_flags_summary_contains_no_secrets():
    flags = agent_flags()
    assert flags["runtime_mode"] == "deterministic"
    assert "key" not in " ".join(flags.keys()).lower()
