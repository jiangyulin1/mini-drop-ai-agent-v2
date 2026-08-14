"""Select the active AgentRuntimePort from MINI_DROP_AGENT_RUNTIME.

pi_shadow behaves exactly like pi for the port surface today (both fail closed
until E3); the flag distinction becomes meaningful once the adapter is wired,
where pi_shadow refuses to create real Tasks.
"""

from __future__ import annotations

from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.deterministic import (
    RUNTIME_VERSION,
    DeterministicAgentRuntime,
    deterministic_factory,
)
from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter

_runtime: DeterministicAgentRuntime | PiAgentRuntimeAdapter | None = None


def get_runtime() -> DeterministicAgentRuntime | PiAgentRuntimeAdapter:
    global _runtime
    if _runtime is not None:
        return _runtime
    mode = runtime_mode()
    if mode in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        try:
            _runtime = PiAgentRuntimeAdapter()
        except RuntimeError:
            # Fail closed: do not silently run a live Case on an unverified path.
            raise
    else:
        _runtime = deterministic_factory()
    return _runtime


def reset_runtime() -> None:
    global _runtime
    _runtime = None


def active_runtime_info() -> dict[str, object]:
    mode = runtime_mode().value
    try:
        runtime = get_runtime()
        return {
            "runtime_type": runtime.runtime_type,
            "runtime_version": runtime.runtime_version,
            "mode": mode,
            "ready": True,
        }
    except RuntimeError as exc:
        return {
            "runtime_type": "pi",
            "runtime_version": RUNTIME_VERSION,
            "mode": mode,
            "ready": False,
            "error": str(exc),
        }
