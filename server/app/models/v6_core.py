from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from server.app.models.base import Base


class InvestigationRunModel(Base):
    """One Case business investigation run.  Distinct from evaluation_run_id."""

    __tablename__ = "investigation_runs"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", "run_id", name="uq_investigation_run"),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_investigation_run_case_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_investigation_runs_case_status", "case_id", "status"),
    )

    run_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="CREATED", index=True)
    scope_revision = Column(Integer, nullable=False, default=1)
    control_revision = Column(Integer, nullable=False, default=1)
    case_command_revision = Column(Integer, nullable=False, default=1)
    active_plan_revision = Column(Integer, nullable=False, default=0)
    evidence_watermark = Column(Integer, nullable=False, default=0)
    created_from_turn_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "scope_revision": self.scope_revision,
            "control_revision": self.control_revision,
            "case_command_revision": self.case_command_revision,
            "active_plan_revision": self.active_plan_revision,
            "evidence_watermark": self.evidence_watermark,
            "created_from_turn_id": self.created_from_turn_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CaseContextSnapshotModel(Base):
    __tablename__ = "case_context_snapshots"
    __table_args__ = (
        UniqueConstraint("case_id", "snapshot_id", name="uq_case_context_snapshot"),
    )

    snapshot_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    investigation_run_id = Column(String(128), nullable=True, index=True)
    case_command_revision = Column(Integer, nullable=False, default=1)
    control_revision = Column(Integer, nullable=False, default=1)
    scope_revision = Column(Integer, nullable=False, default=1)
    plan_revision = Column(Integer, nullable=False, default=0)
    campaign_revision = Column(Integer, nullable=False, default=0)
    evidence_watermark = Column(Integer, nullable=False, default=0)
    snapshot_hash = Column(String(128), nullable=False)
    content_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "investigation_run_id": self.investigation_run_id,
            "case_command_revision": self.case_command_revision,
            "control_revision": self.control_revision,
            "scope_revision": self.scope_revision,
            "plan_revision": self.plan_revision,
            "campaign_revision": self.campaign_revision,
            "evidence_watermark": self.evidence_watermark,
            "snapshot_hash": self.snapshot_hash,
            "content": self.content_json or {},
            "created_at": self.created_at,
        }


class AgentCycleModel(Base):
    __tablename__ = "agent_cycles"
    __table_args__ = (
        UniqueConstraint("case_id", "run_id", "cycle_id", name="uq_agent_cycle"),
    )

    cycle_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=False, index=True)
    trigger_type = Column(String(32), nullable=False)
    trigger_ref = Column(String(128), nullable=True)
    trigger_turn_id = Column(String(128), nullable=True, index=True)
    origin_turn_id = Column(String(128), nullable=True, index=True)
    recovery_of_cycle_id = Column(String(128), nullable=True)
    context_snapshot_id = Column(String(128), nullable=True)
    evidence_watermark = Column(Integer, nullable=False, default=0)
    runtime_binding_id = Column(String(128), nullable=True)
    generation = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="QUEUED", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "trigger_type": self.trigger_type,
            "trigger_ref": self.trigger_ref,
            "trigger_turn_id": self.trigger_turn_id,
            "origin_turn_id": self.origin_turn_id,
            "recovery_of_cycle_id": self.recovery_of_cycle_id,
            "context_snapshot_id": self.context_snapshot_id,
            "evidence_watermark": self.evidence_watermark,
            "runtime_binding_id": self.runtime_binding_id,
            "generation": self.generation,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ModelRequestModel(Base):
    __tablename__ = "model_requests"
    __table_args__ = (
        UniqueConstraint("case_id", "model_request_id", name="uq_model_request"),
        Index("ix_model_requests_cycle_status", "cycle_id", "status"),
    )

    model_request_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=False, index=True)
    cycle_id = Column(String(128), nullable=False, index=True)
    provider_request_id = Column(String(128), nullable=True)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    input_snapshot_hash = Column(String(128), nullable=True)
    evidence_projection_hashes = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="QUEUED", index=True)
    usage = Column(JSON, nullable=False, default=dict)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "model_request_id": self.model_request_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "cycle_id": self.cycle_id,
            "provider_request_id": self.provider_request_id,
            "idempotency_key": self.idempotency_key,
            "input_snapshot_hash": self.input_snapshot_hash,
            "evidence_projection_hashes": self.evidence_projection_hashes or [],
            "status": self.status,
            "usage": self.usage or {},
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
        }


class ModelResponseModel(Base):
    __tablename__ = "model_responses"
    __table_args__ = (
        UniqueConstraint("model_request_id", "idempotency_key", name="uq_model_response_idem"),
        UniqueConstraint("model_response_id", name="uq_model_response_id"),
    )

    model_response_id = Column(String(128), primary_key=True)
    model_request_id = Column(String(128), nullable=False, index=True)
    provider_request_id = Column(String(128), nullable=True)
    idempotency_key = Column(String(128), nullable=False)
    canonical_visible_content = Column(Text, nullable=False, default="")
    proposed_tool_calls = Column(JSON, nullable=False, default=list)
    response_hash = Column(String(128), nullable=False)
    durable_spool_offset = Column(BigInteger, nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "model_response_id": self.model_response_id,
            "model_request_id": self.model_request_id,
            "provider_request_id": self.provider_request_id,
            "idempotency_key": self.idempotency_key,
            "canonical_visible_content": self.canonical_visible_content,
            "proposed_tool_calls": self.proposed_tool_calls or [],
            "response_hash": self.response_hash,
            "durable_spool_offset": self.durable_spool_offset,
            "accepted_at": self.accepted_at,
        }


class AssistantMessageModel(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        UniqueConstraint("case_id", "message_id", name="uq_assistant_message"),
        Index("ix_assistant_messages_turn", "trigger_turn_id", "created_at"),
    )

    message_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    trigger_turn_id = Column(String(128), nullable=True)
    origin_turn_id = Column(String(128), nullable=True)
    cycle_id = Column(String(128), nullable=True, index=True)
    model_request_id = Column(String(128), nullable=True, index=True)
    content = Column(Text, nullable=False)
    evidence_refs = Column(JSON, nullable=False, default=list)
    limitation_refs = Column(JSON, nullable=False, default=list)
    conclusion_revision_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "trigger_turn_id": self.trigger_turn_id,
            "origin_turn_id": self.origin_turn_id,
            "cycle_id": self.cycle_id,
            "model_request_id": self.model_request_id,
            "content": self.content,
            "evidence_refs": self.evidence_refs or [],
            "limitation_refs": self.limitation_refs or [],
            "conclusion_revision_id": self.conclusion_revision_id,
            "created_at": self.created_at,
        }


class AgentProposalModel(Base):
    __tablename__ = "agent_proposals"
    __table_args__ = (
        UniqueConstraint("case_id", "proposal_id", name="uq_agent_proposal"),
    )

    proposal_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    object_type = Column(String(48), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    validation_result = Column(JSON, nullable=False, default=dict)
    source_cycle_id = Column(String(128), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="PROPOSED", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "object_type": self.object_type,
            "payload": self.payload or {},
            "validation_result": self.validation_result or {},
            "source_cycle_id": self.source_cycle_id,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


class AgentDecisionRecordModel(Base):
    __tablename__ = "agent_decision_records"
    __table_args__ = (
        UniqueConstraint("cycle_id", "decision_id", name="uq_agent_decision"),
    )

    decision_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    cycle_id = Column(String(128), nullable=False, index=True)
    model_request_id = Column(String(128), nullable=False, index=True)
    observed_projection_hashes = Column(JSON, nullable=False, default=list)
    hypotheses = Column(JSON, nullable=False, default=list)
    opposing_evidence = Column(JSON, nullable=False, default=list)
    selected_missing_fact = Column(String(500), nullable=True)
    selection_reason = Column(Text, nullable=True)
    proposed_operation_or_action = Column(JSON, nullable=False, default=dict)
    alternatives_considered = Column(JSON, nullable=False, default=list)
    stop_reason = Column(String(64), nullable=True)
    provider_response_hash = Column(String(128), nullable=True)
    tool_call_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "cycle_id": self.cycle_id,
            "model_request_id": self.model_request_id,
            "observed_projection_hashes": self.observed_projection_hashes or [],
            "hypotheses": self.hypotheses or [],
            "opposing_evidence": self.opposing_evidence or [],
            "selected_missing_fact": self.selected_missing_fact,
            "selection_reason": self.selection_reason,
            "proposed_operation_or_action": self.proposed_operation_or_action or {},
            "alternatives_considered": self.alternatives_considered or [],
            "stop_reason": self.stop_reason,
            "provider_response_hash": self.provider_response_hash,
            "tool_call_ids": self.tool_call_ids or [],
            "created_at": self.created_at,
        }


# ── v6 Evidence projection / review / durable wake ───────────────────


class EvidenceProjectionModel(Base):
    __tablename__ = "evidence_projections"
    __table_args__ = (
        UniqueConstraint("evidence_id", "projection_kind", "projection_version", name="uq_evidence_projection"),
        Index("ix_evidence_projections_evidence", "evidence_id"),
    )

    projection_id = Column(String(128), primary_key=True)
    evidence_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    projection_kind = Column(String(32), nullable=False)
    projection_schema = Column(String(64), nullable=False, default="evidence-projection.v1")
    projection_version = Column(Integer, nullable=False, default=1)
    content_json = Column(JSON, nullable=False, default=dict)
    projection_hash = Column(String(128), nullable=False)
    truncated = Column(Boolean, nullable=False, default=False)
    source_bytes = Column(Integer, nullable=False, default=0)
    projected_bytes = Column(Integer, nullable=False, default=0)
    parser_version = Column(String(64), nullable=False, default="deterministic.v1")
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "projection_id": self.projection_id,
            "evidence_id": self.evidence_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "projection_kind": self.projection_kind,
            "projection_schema": self.projection_schema,
            "projection_version": self.projection_version,
            "content": self.content_json or {},
            "projection_hash": self.projection_hash,
            "truncated": bool(self.truncated),
            "source_bytes": self.source_bytes,
            "projected_bytes": self.projected_bytes,
            "parser_version": self.parser_version,
            "created_at": self.created_at,
        }


class EvidenceReviewRevisionModel(Base):
    __tablename__ = "evidence_review_revisions"
    __table_args__ = (
        UniqueConstraint("evidence_id", "review_revision", name="uq_evidence_review_revision"),
    )

    review_revision_id = Column(String(128), primary_key=True)
    evidence_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    review_revision = Column(Integer, nullable=False, default=1)
    decision = Column(String(24), nullable=False)
    reason = Column(Text, nullable=True)
    reviewed_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "review_revision_id": self.review_revision_id,
            "evidence_id": self.evidence_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "review_revision": self.review_revision,
            "decision": self.decision,
            "reason": self.reason,
            "reviewed_by": self.reviewed_by,
            "created_at": self.created_at,
        }


class DomainOutboxModel(Base):
    __tablename__ = "domain_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_domain_outbox_dedupe"),
        Index("ix_domain_outbox_status_available", "status", "available_at"),
    )

    outbox_id = Column(String(128), primary_key=True)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    dedupe_key = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    claim_token = Column(String(128), nullable=True)
    claimed_by = Column(String(128), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    dispatch_outcome = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "outbox_id": self.outbox_id,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type,
            "payload": self.payload or {},
            "dedupe_key": self.dedupe_key,
            "status": self.status,
            "available_at": self.available_at,
            "claim_token": self.claim_token,
            "claimed_by": self.claimed_by,
            "claim_expires_at": self.claim_expires_at,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "dispatch_outcome": self.dispatch_outcome,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RuntimeWakeupModel(Base):
    __tablename__ = "runtime_wakeups"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_runtime_wakeup_dedupe"),
    )

    wakeup_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    investigation_run_id = Column(String(128), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    source_refs = Column(JSON, nullable=False, default=list)
    control_revision = Column(Integer, nullable=False, default=1)
    scope_revision = Column(Integer, nullable=False, default=1)
    reason_class = Column(String(32), nullable=False)
    from_evidence_watermark = Column(Integer, nullable=False, default=0)
    to_evidence_watermark = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    claim_token = Column(String(128), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    dedupe_key = Column(String(128), nullable=False)
    sealed_at = Column(DateTime(timezone=True), nullable=True)
    sealed_to_evidence_watermark = Column(Integer, nullable=True)
    cycle_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "wakeup_id": self.wakeup_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "investigation_run_id": self.investigation_run_id,
            "reason": self.reason,
            "source_refs": self.source_refs or [],
            "control_revision": self.control_revision,
            "scope_revision": self.scope_revision,
            "reason_class": self.reason_class,
            "from_evidence_watermark": self.from_evidence_watermark,
            "to_evidence_watermark": self.to_evidence_watermark,
            "status": self.status,
            "claim_token": self.claim_token,
            "claim_expires_at": self.claim_expires_at,
            "dedupe_key": self.dedupe_key,
            "sealed_at": self.sealed_at,
            "sealed_to_evidence_watermark": self.sealed_to_evidence_watermark,
            "cycle_id": self.cycle_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RuntimeWakeupSourceModel(Base):
    __tablename__ = "runtime_wakeup_sources"
    __table_args__ = (
        UniqueConstraint("outbox_id", name="uq_runtime_wakeup_source_outbox"),
    )

    wakeup_id = Column(String(128), nullable=False, primary_key=True)
    outbox_id = Column(String(128), nullable=False, index=True)
    source_ref = Column(String(256), nullable=False)
    evidence_watermark = Column(Integer, nullable=False, default=0)
    mapped_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "wakeup_id": self.wakeup_id,
            "outbox_id": self.outbox_id,
            "source_ref": self.source_ref,
            "evidence_watermark": self.evidence_watermark,
            "mapped_at": self.mapped_at,
        }


# ── v6 Plan / Campaign / Execution domain ───────────────────────────


class OperationSpecModel(Base):
    __tablename__ = "operation_specs"
    __table_args__ = (
        UniqueConstraint("operation_id", "version", name="uq_operation_spec"),
    )

    operation_id = Column(String(128), primary_key=True)
    version = Column(String(32), nullable=False, default="v1")
    execution_kind = Column(String(24), nullable=False)
    backend_ref = Column(String(128), nullable=False)
    description = Column(Text, nullable=False, default="")
    supported_target_types = Column(JSON, nullable=False, default=list)
    parameters_schema = Column(JSON, nullable=False, default=dict)
    evidence_schema = Column(JSON, nullable=False, default=dict)
    required_capabilities = Column(JSON, nullable=False, default=list)
    capability_version = Column(String(32), nullable=True)
    risk = Column(String(24), nullable=False, default="READ_LOW")
    timeout_sec = Column(Integer, nullable=False, default=30)
    max_output_bytes = Column(Integer, nullable=False, default=1048576)
    parser_version = Column(String(64), nullable=True)
    renderer_hash = Column(String(128), nullable=True)
    cache_ttl = Column(Integer, nullable=False, default=0)
    fingerprint_fields = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    auto_allowed = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "version": self.version,
            "execution_kind": self.execution_kind,
            "backend_ref": self.backend_ref,
            "description": self.description,
            "supported_target_types": self.supported_target_types or [],
            "parameters_schema": self.parameters_schema or {},
            "evidence_schema": self.evidence_schema or {},
            "required_capabilities": self.required_capabilities or [],
            "capability_version": self.capability_version,
            "risk": self.risk,
            "timeout_sec": self.timeout_sec,
            "max_output_bytes": self.max_output_bytes,
            "parser_version": self.parser_version,
            "renderer_hash": self.renderer_hash,
            "cache_ttl": self.cache_ttl,
            "fingerprint_fields": self.fingerprint_fields or [],
            "enabled": bool(self.enabled),
            "auto_allowed": bool(self.auto_allowed),
            "updated_at": self.updated_at,
        }


class CampaignRevisionModel(Base):
    __tablename__ = "campaign_revisions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "revision", name="uq_campaign_revision"),
    )

    campaign_id = Column(String(128), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    plan_step_revision_id = Column(String(128), nullable=True)
    membership_snapshot_id = Column(String(128), nullable=True)
    coverage_policy = Column(String(64), nullable=False, default="REQUIRED_ALL")
    status = Column(String(24), nullable=False, default="DRAFT", index=True)
    common_baseline_assignment_ids = Column(JSON, nullable=False, default=list)
    differential_assignment_ids = Column(JSON, nullable=False, default=list)
    actor = Column(String(24), nullable=False, default="USER")
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "revision": self.revision,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "plan_step_revision_id": self.plan_step_revision_id,
            "membership_snapshot_id": self.membership_snapshot_id,
            "coverage_policy": self.coverage_policy,
            "status": self.status,
            "common_baseline_assignment_ids": self.common_baseline_assignment_ids or [],
            "differential_assignment_ids": self.differential_assignment_ids or [],
            "actor": self.actor,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AcquisitionAssignmentModel(Base):
    __tablename__ = "acquisition_assignments"
    __table_args__ = (
        UniqueConstraint("campaign_id", "assignment_id", name="uq_acquisition_assignment"),
    )

    assignment_id = Column(String(128), primary_key=True)
    campaign_id = Column(String(128), nullable=False, index=True)
    campaign_revision = Column(Integer, nullable=False, default=1)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    role = Column(String(64), nullable=True)
    operation_ref = Column(String(128), nullable=False)
    target_selector = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)
    requested_window = Column(JSON, nullable=False, default=dict)
    required_fact_ids = Column(JSON, nullable=False, default=list)
    risk = Column(String(24), nullable=False, default="READ_LOW")
    priority = Column(Integer, nullable=False, default=50)
    depends_on = Column(JSON, nullable=False, default=list)
    required_coverage = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, default="PLANNED")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "campaign_id": self.campaign_id,
            "campaign_revision": self.campaign_revision,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "operation_ref": self.operation_ref,
            "target_selector": self.target_selector or {},
            "parameters": self.parameters or {},
            "requested_window": self.requested_window or {},
            "required_fact_ids": self.required_fact_ids or [],
            "risk": self.risk,
            "priority": self.priority,
            "depends_on": self.depends_on or [],
            "required_coverage": self.required_coverage,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ExecutionUnitModel(Base):
    __tablename__ = "execution_units"
    __table_args__ = (
        UniqueConstraint("assignment_id", "resource_ref", "fingerprint", name="uq_execution_unit_fingerprint"),
        UniqueConstraint("task_id", name="uq_execution_unit_task"),
        UniqueConstraint("source_call_id", name="uq_execution_unit_source_call"),
        Index("ix_execution_units_case_status", "case_id", "status"),
    )

    execution_unit_id = Column(String(128), primary_key=True)
    assignment_id = Column(String(128), nullable=False, index=True)
    campaign_id = Column(String(128), nullable=False, index=True)
    campaign_revision = Column(Integer, nullable=False, default=1)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    resource_ref = Column(String(256), nullable=False)
    operation_id = Column(String(128), nullable=False)
    operation_version = Column(String(32), nullable=False, default="v1")
    normalized_parameters = Column(JSON, nullable=False, default=dict)
    evaluation_run_id = Column(String(128), nullable=True)
    deployment_epoch = Column(Integer, nullable=False, default=1)
    control_revision = Column(Integer, nullable=False, default=1)
    scope_revision = Column(Integer, nullable=False, default=1)
    plan_revision = Column(Integer, nullable=False, default=0)
    fingerprint = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="PLANNED", index=True)
    task_id = Column(String(128), nullable=True)
    source_call_id = Column(String(128), nullable=True)
    cancel_epoch = Column(Integer, nullable=True)
    cancel_command_id = Column(String(128), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    terminal_result_status = Column(String(24), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "execution_unit_id": self.execution_unit_id,
            "assignment_id": self.assignment_id,
            "campaign_id": self.campaign_id,
            "campaign_revision": self.campaign_revision,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "resource_ref": self.resource_ref,
            "operation_id": self.operation_id,
            "operation_version": self.operation_version,
            "normalized_parameters": self.normalized_parameters or {},
            "evaluation_run_id": self.evaluation_run_id,
            "deployment_epoch": self.deployment_epoch,
            "control_revision": self.control_revision,
            "scope_revision": self.scope_revision,
            "plan_revision": self.plan_revision,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "task_id": self.task_id,
            "source_call_id": self.source_call_id,
            "cancel_epoch": self.cancel_epoch,
            "cancel_command_id": self.cancel_command_id,
            "cancel_requested_at": self.cancel_requested_at,
            "terminal_result_status": self.terminal_result_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── v6 Causal / Gap / Conclusion / Repair ───────────────────────────


class CausalGraphRevisionModel(Base):
    __tablename__ = "causal_graph_revisions"
    __table_args__ = (
        UniqueConstraint("case_id", "graph_revision", name="uq_causal_graph_revision"),
    )

    graph_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    investigation_run_id = Column(String(128), nullable=True, index=True)
    graph_revision = Column(Integer, nullable=False, default=1)
    evidence_watermark = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="PROPOSED")
    model_proposed_json = Column(JSON, nullable=False, default=dict)
    verifier_json = Column(JSON, nullable=False, default=dict)
    verifier_version = Column(String(64), nullable=True)
    created_from_cycle_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "graph_id": self.graph_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "investigation_run_id": self.investigation_run_id,
            "graph_revision": self.graph_revision,
            "evidence_watermark": self.evidence_watermark,
            "status": self.status,
            "model_proposed": self.model_proposed_json or {},
            "verifier": self.verifier_json or {},
            "verifier_version": self.verifier_version,
            "created_from_cycle_id": self.created_from_cycle_id,
            "created_at": self.created_at,
        }


class CausalNodeModel(Base):
    __tablename__ = "causal_nodes"
    __table_args__ = (
        UniqueConstraint("graph_id", "node_id", name="uq_causal_node"),
    )

    node_id = Column(String(128), primary_key=True)
    graph_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    entity_ref = Column(String(256), nullable=False)
    mechanism = Column(Text, nullable=False)
    role = Column(String(40), nullable=False, default="SYMPTOM")
    model_proposed_role = Column(String(40), nullable=True)
    verifier_role = Column(String(40), nullable=True)
    onset_start = Column(DateTime(timezone=True), nullable=True)
    onset_end = Column(DateTime(timezone=True), nullable=True)
    supporting_evidence_refs = Column(JSON, nullable=False, default=list)
    opposing_evidence_refs = Column(JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.0)
    role_rationale = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "graph_id": self.graph_id,
            "case_id": self.case_id,
            "entity_ref": self.entity_ref,
            "mechanism": self.mechanism,
            "role": self.role,
            "model_proposed_role": self.model_proposed_role,
            "verifier_role": self.verifier_role,
            "onset_start": self.onset_start,
            "onset_end": self.onset_end,
            "supporting_evidence_refs": self.supporting_evidence_refs or [],
            "opposing_evidence_refs": self.opposing_evidence_refs or [],
            "confidence": self.confidence,
            "role_rationale": self.role_rationale,
            "created_at": self.created_at,
        }


class CausalEdgeModel(Base):
    __tablename__ = "causal_edges"
    __table_args__ = (
        UniqueConstraint("graph_id", "edge_id", name="uq_causal_edge"),
    )

    edge_id = Column(String(128), primary_key=True)
    graph_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    source_node_id = Column(String(128), nullable=False)
    target_node_id = Column(String(128), nullable=False)
    relation = Column(String(32), nullable=False, default="CAUSES")
    model_proposed_relation = Column(String(32), nullable=True)
    verifier_relation = Column(String(32), nullable=True)
    mechanism = Column(Text, nullable=True)
    expected_lag = Column(String(64), nullable=True)
    observed_lag = Column(String(64), nullable=True)
    topology_path_refs = Column(JSON, nullable=False, default=list)
    supporting_evidence_refs = Column(JSON, nullable=False, default=list)
    knowledge_refs = Column(JSON, nullable=False, default=list)
    verification_state = Column(String(24), nullable=False, default="UNVERIFIED")
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "graph_id": self.graph_id,
            "case_id": self.case_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "relation": self.relation,
            "model_proposed_relation": self.model_proposed_relation,
            "verifier_relation": self.verifier_relation,
            "mechanism": self.mechanism,
            "expected_lag": self.expected_lag,
            "observed_lag": self.observed_lag,
            "topology_path_refs": self.topology_path_refs or [],
            "supporting_evidence_refs": self.supporting_evidence_refs or [],
            "knowledge_refs": self.knowledge_refs or [],
            "verification_state": self.verification_state,
            "created_at": self.created_at,
        }


class EvidenceGapModel(Base):
    __tablename__ = "evidence_gaps"
    __table_args__ = (
        UniqueConstraint("case_id", "gap_id", name="uq_evidence_gap"),
    )

    gap_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    investigation_run_id = Column(String(128), nullable=True, index=True)
    blocked_claim = Column(Text, nullable=True)
    required_fact = Column(Text, nullable=False)
    attempted_execution = Column(String(128), nullable=True)
    target = Column(String(256), nullable=True)
    requested_time_window = Column(JSON, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="OPEN", index=True)
    reason_code = Column(String(48), nullable=False)
    raw_error_ref = Column(String(128), nullable=True)
    observed_evidence = Column(JSON, nullable=False, default=list)
    what_it_supports = Column(Text, nullable=True)
    what_it_does_not_support = Column(Text, nullable=True)
    conflicting_evidence_refs = Column(JSON, nullable=False, default=list)
    retryable = Column(Boolean, nullable=False, default=False)
    next_best_action = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "gap_id": self.gap_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "investigation_run_id": self.investigation_run_id,
            "blocked_claim": self.blocked_claim,
            "required_fact": self.required_fact,
            "attempted_execution": self.attempted_execution,
            "target": self.target,
            "requested_time_window": self.requested_time_window or {},
            "status": self.status,
            "reason_code": self.reason_code,
            "raw_error_ref": self.raw_error_ref,
            "observed_evidence": self.observed_evidence or [],
            "what_it_supports": self.what_it_supports,
            "what_it_does_not_support": self.what_it_does_not_support,
            "conflicting_evidence_refs": self.conflicting_evidence_refs or [],
            "retryable": bool(self.retryable),
            "next_best_action": self.next_best_action,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


class ConclusionRevisionModel(Base):
    __tablename__ = "conclusion_revisions"
    __table_args__ = (
        UniqueConstraint("case_id", "revision", name="uq_conclusion_revision"),
    )

    conclusion_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    investigation_run_id = Column(String(128), nullable=False, index=True)
    revision = Column(Integer, nullable=False, default=1)
    state = Column(String(32), nullable=False, default="PARTIALLY_CONFIRMED")
    primary_root_causes = Column(JSON, nullable=False, default=list)
    ranked_primary_candidates = Column(JSON, nullable=False, default=list)
    contributing_factors = Column(JSON, nullable=False, default=list)
    amplifiers = Column(JSON, nullable=False, default=list)
    propagated_effects = Column(JSON, nullable=False, default=list)
    symptoms = Column(JSON, nullable=False, default=list)
    coincidental_anomalies = Column(JSON, nullable=False, default=list)
    ruled_out = Column(JSON, nullable=False, default=list)
    causal_graph_revision_id = Column(String(128), nullable=True)
    claims = Column(JSON, nullable=False, default=list)
    evidence_gap_ids = Column(JSON, nullable=False, default=list)
    recommendation_ids = Column(JSON, nullable=False, default=list)
    limitations = Column(JSON, nullable=False, default=list)
    abstention_reason = Column(Text, nullable=True)
    report_text = Column(Text, nullable=True)
    created_from_cycle_id = Column(String(128), nullable=True)
    model_request_id = Column(String(128), nullable=True)
    verifier_version = Column(String(64), nullable=False, default="causal-report-verifier.v1")
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "conclusion_id": self.conclusion_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "investigation_run_id": self.investigation_run_id,
            "revision": self.revision,
            "state": self.state,
            "primary_root_causes": self.primary_root_causes or [],
            "ranked_primary_candidates": self.ranked_primary_candidates or [],
            "contributing_factors": self.contributing_factors or [],
            "amplifiers": self.amplifiers or [],
            "propagated_effects": self.propagated_effects or [],
            "symptoms": self.symptoms or [],
            "coincidental_anomalies": self.coincidental_anomalies or [],
            "ruled_out": self.ruled_out or [],
            "causal_graph_revision_id": self.causal_graph_revision_id,
            "claims": self.claims or [],
            "evidence_gap_ids": self.evidence_gap_ids or [],
            "recommendation_ids": self.recommendation_ids or [],
            "limitations": self.limitations or [],
            "abstention_reason": self.abstention_reason,
            "report_text": self.report_text,
            "created_from_cycle_id": self.created_from_cycle_id,
            "model_request_id": self.model_request_id,
            "verifier_version": self.verifier_version,
            "created_at": self.created_at,
        }


class ClaimEvidenceBindingModel(Base):
    __tablename__ = "claim_evidence_bindings"
    __table_args__ = (
        UniqueConstraint("conclusion_id", "claim_id", "evidence_id", name="uq_claim_evidence_binding"),
    )

    claim_id = Column(String(128), primary_key=True)
    conclusion_id = Column(String(128), nullable=False, index=True)
    evidence_id = Column(String(128), nullable=False, index=True)
    projection_hash = Column(String(128), nullable=False)
    field_path = Column(String(256), nullable=True)
    extractor_id = Column(String(128), nullable=True)
    extractor_version = Column(String(32), nullable=True)
    extractor_hash = Column(String(128), nullable=True)
    target_ref = Column(String(256), nullable=True)
    resource_incarnation = Column(String(256), nullable=True)
    event_window = Column(JSON, nullable=False, default=dict)
    predicate = Column(JSON, nullable=False, default=dict)
    observed_value = Column(JSON, nullable=False, default=dict)
    support_kind = Column(String(16), nullable=False, default="SUPPORTS")
    verifier_result = Column(String(24), nullable=False, default="PENDING")
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "conclusion_id": self.conclusion_id,
            "evidence_id": self.evidence_id,
            "projection_hash": self.projection_hash,
            "field_path": self.field_path,
            "extractor_id": self.extractor_id,
            "extractor_version": self.extractor_version,
            "extractor_hash": self.extractor_hash,
            "target_ref": self.target_ref,
            "resource_incarnation": self.resource_incarnation,
            "event_window": self.event_window or {},
            "predicate": self.predicate or {},
            "observed_value": self.observed_value or {},
            "support_kind": self.support_kind,
            "verifier_result": self.verifier_result,
            "created_at": self.created_at,
        }


class RepairRecommendationModel(Base):
    __tablename__ = "repair_recommendations"
    __table_args__ = (
        UniqueConstraint("case_id", "recommendation_id", name="uq_repair_recommendation"),
    )

    recommendation_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    conclusion_id = Column(String(128), nullable=True, index=True)
    cause_or_edge_ref = Column(String(128), nullable=False)
    category = Column(String(32), nullable=False)
    target = Column(String(256), nullable=False)
    concrete_action = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    evidence_refs = Column(JSON, nullable=False, default=list)
    prerequisites = Column(JSON, nullable=False, default=list)
    risk = Column(String(24), nullable=True)
    approval = Column(String(24), nullable=True)
    expected_effect = Column(Text, nullable=True)
    verification_operations = Column(JSON, nullable=False, default=list)
    success_criteria = Column(JSON, nullable=False, default=list)
    rollback_or_failure_condition = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    limitations = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "recommendation_id": self.recommendation_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "conclusion_id": self.conclusion_id,
            "cause_or_edge_ref": self.cause_or_edge_ref,
            "category": self.category,
            "target": self.target,
            "concrete_action": self.concrete_action,
            "rationale": self.rationale,
            "evidence_refs": self.evidence_refs or [],
            "prerequisites": self.prerequisites or [],
            "risk": self.risk,
            "approval": self.approval,
            "expected_effect": self.expected_effect,
            "verification_operations": self.verification_operations or [],
            "success_criteria": self.success_criteria or [],
            "rollback_or_failure_condition": self.rollback_or_failure_condition,
            "confidence": self.confidence,
            "limitations": self.limitations or [],
            "created_at": self.created_at,
        }


class DeploymentAssessmentModel(Base):
    __tablename__ = "deployment_assessments"
    __table_args__ = (
        UniqueConstraint("case_id", "assessment_id", name="uq_deployment_assessment"),
    )

    assessment_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    verdict = Column(String(24), nullable=False)
    summary = Column(Text, nullable=False)
    requirements_json = Column(JSON, nullable=False, default=dict)
    eligible_nodes = Column(JSON, nullable=False, default=list)
    rejected_nodes = Column(JSON, nullable=False, default=list)
    missing_inputs = Column(JSON, nullable=False, default=list)
    assumptions = Column(JSON, nullable=False, default=list)
    evidence_refs = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "assessment_id": self.assessment_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "verdict": self.verdict,
            "summary": self.summary,
            "requirements": self.requirements_json or {},
            "eligible_nodes": self.eligible_nodes or [],
            "rejected_nodes": self.rejected_nodes or [],
            "missing_inputs": self.missing_inputs or [],
            "assumptions": self.assumptions or [],
            "evidence_refs": self.evidence_refs or [],
            "created_at": self.created_at,
        }
