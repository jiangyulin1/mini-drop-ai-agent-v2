"""PiAgentRuntimeAdapter: Mini-Drop internal protocol client for the Pi Sidecar.

E3 (plan section 4.4 / 6.2): the Sidecar exposes only Mini-Drop's internal
protocol; the raw Pi RPC is never exposed to the network.  This adapter speaks
that internal protocol over HTTP.  In pi_shadow mode it refuses to create real
Tasks and only produces Shadow Plans.

The adapter never touches model credentials: the Sidecar holds the key in
process memory, and this adapter only sends case_context projections.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.port import (
    AcceptedTurn,
    AgentRuntimePort,
    AgentTurnInput,
    CaseContextSnapshot,
    RuntimeBinding,
    RuntimeFollowUp,
    RuntimeState,
    RuntimeSteer,
)

PI_RUNTIME_VERSION = "pi-0.83.0"


class PiSidecarError(RuntimeError):
    pass


class PiAgentRuntimeAdapter(AgentRuntimePort):
    runtime_type = "pi"
    runtime_version = PI_RUNTIME_VERSION

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self._url = (base_url or os.getenv("MINI_DROP_PI_RUNTIME_URL", "")).rstrip("/")
        if not self._url:
            raise RuntimeError(
                "Pi runtime requested but MINI_DROP_PI_RUNTIME_URL is empty; "
                "set the sidecar URL or run with MINI_DROP_AGENT_RUNTIME=deterministic"
            )
        self._timeout = timeout
        self._shadow = runtime_mode() == AgentRuntimeMode.PI_SHADOW

    # ── HTTP 信封 ───────────────────────────────────────────────────────
    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self._url}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        token = os.getenv("MINI_DROP_PI_INTERNAL_TOKEN", "")
        if token:
            request.add_header("X-Internal-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise PiSidecarError(f"sidecar {method} {path}: HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PiSidecarError(f"sidecar {method} {path} unreachable: {exc}") from exc
        if payload.get("ok") is False:
            raise PiSidecarError(f"sidecar {method} {path}: {payload.get('error')}")
        return payload.get("data")

    # ── AgentRuntimePort 实现 ───────────────────────────────────────────
    def start_or_resume(self, case_context: CaseContextSnapshot) -> RuntimeBinding:
        data = self._call("POST", f"/internal/runtime/v1/cases/{case_context.case_id}/resume",
                          {"context": case_context.model_dump(mode="json")})
        return RuntimeBinding(**data)

    def submit_turn(self, case_id: str, turn: AgentTurnInput) -> AcceptedTurn:
        data = self._call("POST", f"/internal/runtime/v1/cases/{case_id}/turn",
                          {
                              "message": turn.message,
                              "references": turn.references,
                              "requested_mode": turn.requested_mode,
                              "client_command_id": turn.client_command_id,
                              "shadow": self._shadow,
                          })
        return AcceptedTurn(**data)

    def steer(self, case_id: str, instruction: RuntimeSteer) -> None:
        self._call("POST", f"/internal/runtime/v1/cases/{case_id}/steer",
                   instruction.model_dump(mode="json"))
        return None

    def follow_up(self, case_id: str, instruction: RuntimeFollowUp) -> None:
        self._call("POST", f"/internal/runtime/v1/cases/{case_id}/follow-up",
                   instruction.model_dump(mode="json"))
        return None

    def abort(self, case_id: str, reason: str) -> None:
        self._call("POST", f"/internal/runtime/v1/cases/{case_id}/abort",
                   {"reason": reason})
        return None

    def get_state(self, case_id: str) -> RuntimeState:
        data = self._call("GET", f"/internal/runtime/v1/cases/{case_id}/state")
        return RuntimeState(**data)

    def submit_shadow_plan(self, case_id: str, case_context: CaseContextSnapshot) -> dict[str, Any]:
        """Shadow 模式专用：请求 Sidecar 生成计划，不创建 Task。"""
        return self._call("POST", f"/internal/runtime/v1/cases/{case_id}/shadow-plan",
                          {"context": case_context.model_dump(mode="json")})
