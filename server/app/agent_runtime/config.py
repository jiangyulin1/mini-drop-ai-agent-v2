"""Feature flags for the agent runtime surface.

Mirrors the documented MINI_DROP_AGENT_RUNTIME=deterministic|pi_shadow|pi switch
plus the autonomy toggles the plan gates against (section 16.1).  Values are
read once at startup; a restart is required to change runtime mode, which is
intentional so a mis-configured sidecar can never half-take-over a live Case.
"""

from __future__ import annotations

import os
from enum import Enum


class AgentRuntimeMode(str, Enum):
    DETERMINISTIC = "deterministic"
    PI_SHADOW = "pi_shadow"
    PI = "pi"


class AgentAutonomyMode(str, Enum):
    ANALYZE_ONLY = "ANALYZE_ONLY"
    COLLABORATE = "COLLABORATE"
    AUTO_INVESTIGATE = "AUTO_INVESTIGATE"


def _as_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def runtime_mode() -> AgentRuntimeMode:
    configured = os.getenv("MINI_DROP_AGENT_RUNTIME")
    # A configured Sidecar URL means the operator opted into the real Agent
    # path. Keep deterministic only as the explicit offline fallback, rather
    # than silently running it during a Pi deployment.
    raw = (
        configured.strip().lower()
        if configured is not None and configured.strip()
        else ("pi" if pi_runtime_url() else "deterministic")
    )
    try:
        return AgentRuntimeMode(raw)
    except ValueError:
        return AgentRuntimeMode.DETERMINISTIC


def pi_runtime_url() -> str:
    return os.getenv("MINI_DROP_PI_RUNTIME_URL", "").strip()


def pi_runtime_version() -> str:
    return os.getenv("MINI_DROP_PI_RUNTIME_VERSION", "0.84.2").strip()


def agent_auto_read_low() -> bool:
    """If false, READ_LOW steps are surfaced for confirmation instead of auto-run."""
    return _as_bool("MINI_DROP_AGENT_AUTO_READ_LOW", "0")


def agent_mcp_enabled() -> bool:
    return _as_bool("MINI_DROP_AGENT_MCP_ENABLED", "0")


def agent_max_active_cases() -> int:
    try:
        return max(1, min(int(os.getenv("MINI_DROP_AGENT_MAX_ACTIVE_CASES", "4")), 64))
    except ValueError:
        return 4


def agent_skills_enabled() -> str:
    return os.getenv("MINI_DROP_AGENT_SKILLS_ENABLED", "0").strip().lower()


def agent_skill_max_per_turn() -> int:
    try:
        return max(0, min(int(os.getenv("MINI_DROP_AGENT_SKILL_MAX_PER_TURN", "3")), 10))
    except ValueError:
        return 3


def agent_cluster_fanout_enabled() -> bool:
    # Cluster investigations are the primary deployment mode. Operators can
    # explicitly disable fanout for a single-node compatibility run.
    return _as_bool("MINI_DROP_AGENT_CLUSTER_FANOUT_ENABLED", "1")


def agent_max_fanout_targets() -> int:
    try:
        return max(1, min(int(os.getenv("MINI_DROP_AGENT_MAX_FANOUT_TARGETS", "8")), 256))
    except ValueError:
        return 8


def agent_flags() -> dict[str, object]:
    """Summary used by /api/v1/agent-runtime/config and diagnostics, no secrets."""
    return {
        "runtime_mode": runtime_mode().value,
        "pi_runtime_url": pi_runtime_url() or None,
        "pi_runtime_version": pi_runtime_version(),
        "agent_auto_read_low": agent_auto_read_low(),
        "agent_mcp_enabled": agent_mcp_enabled(),
        "agent_max_active_cases": agent_max_active_cases(),
        "agent_skills_enabled": agent_skills_enabled(),
        "agent_skill_max_per_turn": agent_skill_max_per_turn(),
        "agent_cluster_fanout_enabled": agent_cluster_fanout_enabled(),
        "agent_max_fanout_targets": agent_max_fanout_targets(),
    }
