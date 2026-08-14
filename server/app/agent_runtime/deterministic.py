"""DeterministicAgentRuntime: the current rule/scaffold path behind the port.

This is the control group and the always-available fallback (plan section 4.2 /
17.2).  It never calls a model and never fabricates tools; every observable is
selected from the SourceRegistry and executed through SourceGateway.  Turn
processing reuses the existing conversation-first implementation in
server.app.diagnosis.agent_runtime so behavior is unchanged under
MINI_DROP_AGENT_RUNTIME=deterministic.
"""

from __future__ import annotations

from uuid import uuid4

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

RUNTIME_VERSION = "deterministic-1.0"


class DeterministicAgentRuntime(AgentRuntimePort):
    runtime_type = "deterministic"
    runtime_version = RUNTIME_VERSION

    def __init__(self) -> None:
        # In-memory binding table.  The Case DB remains the authority for
        # bindings once agent_runtime_bindings migration lands (0020).
        self._bindings: dict[str, RuntimeBinding] = {}

    def start_or_resume(self, case_context: CaseContextSnapshot) -> RuntimeBinding:
        existing = self._bindings.get(case_context.case_id)
        if existing is not None:
            return existing
        binding = RuntimeBinding(
            case_id=case_context.case_id,
            runtime_type=self.runtime_type,
            runtime_version=self.runtime_version,
            runtime_session_id=f"det-{uuid4().hex[:16]}",
            runtime_generation=1,
            status="READY",
        )
        self._bindings[case_context.case_id] = binding
        return binding

    def submit_turn(self, case_id: str, turn: AgentTurnInput) -> AcceptedTurn:
        return AcceptedTurn(
            turn_id=f"turn-{uuid4().hex[:16]}",
            accepted=True,
            mode="deterministic",
            detail="提交到确定性调查路径，不调用模型。",
        )

    def steer(self, case_id: str, instruction: RuntimeSteer) -> None:
        # Deterministic path has no model turn to interrupt; commands are applied
        # by the Case Command queue directly.
        return None

    def follow_up(self, case_id: str, instruction: RuntimeFollowUp) -> None:
        return None

    def abort(self, case_id: str, reason: str) -> None:
        return None

    def get_state(self, case_id: str) -> RuntimeState:
        binding = self._bindings.get(case_id)
        if binding is None:
            return RuntimeState(
                case_id=case_id,
                status="NOT_STARTED",
                runtime_generation=0,
                last_event_seq=0,
                runtime_version=self.runtime_version,
            )
        return RuntimeState(
            case_id=case_id,
            status=binding.status,
            runtime_generation=binding.runtime_generation,
            last_event_seq=binding.last_event_seq,
            runtime_version=binding.runtime_version,
        )


def deterministic_factory() -> DeterministicAgentRuntime:
    return DeterministicAgentRuntime()
