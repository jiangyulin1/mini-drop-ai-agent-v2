"""AgentRuntimePort: the contract a Mini-Drop AI Investigator backend must satisfy.

Mini-Drop owns Case / Evidence / Plan / Task authority; a runtime only advances a
Turn, proposes plans and reacts to steering.  This port keeps Pi (or any future
framework) replaceable without touching the Case API (plan section 6.1 / 4.6).

No model private reasoning crosses this boundary: only auditable summaries and
structured references.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel


class CaseContextSnapshot(StrictModel):
    """L0/L1 projection of a Case handed to a runtime before each Turn (11.1)."""

    case_id: str
    tenant_id: str
    case_goal: str = ""
    target_scope: dict[str, Any] = Field(default_factory=dict)
    autonomy_mode: str = "COLLABORATE"
    case_command_revision: int = 1
    control_revision: int = 1
    plan_revision: int = 0
    scope_revision: int = 1
    campaign_revision: int = 0
    evidence_watermark: int = 0
    investigation_run_id: Optional[str] = None
    turn_id: Optional[str] = None
    disposition: Optional[str] = None
    side_effect_policy: Optional[str] = None
    diagnostic_strategy_id: str = "hybrid"
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    strategy_guidance: str = ""
    runtime_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_options: dict[str, Any] = Field(default_factory=dict)
    context_packet_id: Optional[str] = None
    context_snapshot_id: Optional[str] = None
    runtime_generation: int = 0
    runtime_session_id: str = ""
    collection_proposals: list[dict[str, Any]] = Field(default_factory=list)
    collection_requests: list[dict[str, Any]] = Field(default_factory=list)
    evidence_analyses: list[dict[str, Any]] = Field(default_factory=list)
    information_goals: list[str] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    hypothesis_edges: list[dict[str, Any]] = Field(default_factory=list)
    evidence_gaps: list[dict[str, Any]] = Field(default_factory=list)
    causal_graph: dict[str, Any] = Field(default_factory=dict)
    conclusion: dict[str, Any] = Field(default_factory=dict)
    conclusion_history: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: list[dict[str, Any]] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    running_task_ids: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    recent_user_commands: list[dict[str, Any]] = Field(default_factory=list)
    tool_catalog_summary: list[str] = Field(default_factory=list)
    knowledge_context: list[dict[str, Any]] = Field(default_factory=list)
    skill_context: list[dict[str, Any]] = Field(default_factory=list)
    investigation_directive: dict[str, Any] = Field(default_factory=dict)
    current_support: list[dict[str, Any]] = Field(default_factory=list)
    counterevidence: list[dict[str, Any]] = Field(default_factory=list)
    # A durable operator/review intervention that must be acknowledged before
    # any write-capable tool can continue the investigation.
    intervention: dict[str, Any] = Field(default_factory=dict)


class RuntimeBinding(StrictModel):
    case_id: str
    runtime_type: str
    runtime_version: str
    runtime_session_id: str
    runtime_generation: int = 1
    status: str = "READY"
    last_event_seq: int = 0
    last_context_snapshot_id: Optional[str] = None
    lease_owner: Optional[str] = None


class AgentTurnInput(StrictModel):
    case_id: str
    message: str = Field(min_length=1, max_length=8000)
    references: list[dict[str, Any]] = Field(default_factory=list)
    requested_mode: Optional[str] = None
    client_command_id: Optional[str] = None
    diagnostic_strategy_id: str = "hybrid"
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    runtime_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_options: dict[str, Any] = Field(default_factory=dict)


class AcceptedTurn(StrictModel):
    turn_id: str
    accepted: bool
    mode: str = "deterministic"
    detail: str = ""


class RuntimeSteer(StrictModel):
    case_id: str
    instruction: str = Field(min_length=1, max_length=4000)
    reason_code: str = "USER_DIRECTION"
    scope_revision: int = 0
    plan_revision: int = 0


class RuntimeFollowUp(StrictModel):
    case_id: str
    note: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)
    intervention: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(StrictModel):
    case_id: str
    status: str
    runtime_generation: int
    last_event_seq: int
    runtime_version: str
    detail: str = ""


class AgentRuntimePort(Protocol):
    """Replaceable investigator backend.  Implementations must be restart-safe."""

    runtime_type: str
    runtime_version: str

    def start_or_resume(self, case_context: CaseContextSnapshot) -> RuntimeBinding: ...

    def submit_turn(self, case_id: str, turn: AgentTurnInput) -> AcceptedTurn: ...

    def steer(self, case_id: str, instruction: RuntimeSteer) -> None: ...

    def follow_up(self, case_id: str, instruction: RuntimeFollowUp) -> None: ...

    def abort(self, case_id: str, reason: str) -> None: ...

    def get_state(self, case_id: str) -> RuntimeState: ...
