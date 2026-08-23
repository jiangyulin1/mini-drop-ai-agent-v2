from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from server.app.models.base import Base


class AgentRuntimeBindingModel(Base):
    """Durable binding between a Case and a replaceable Agent Runtime session.

    The Sidecar/Pi session is in-memory and not authoritative.  This row lets
    Mini-Drop rebuild a snapshot and generation after a sidecar restart.
    """

    __tablename__ = "agent_runtime_bindings"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", name="uq_agent_runtime_binding_case"),
    )

    case_id = Column(String(128), primary_key=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    runtime_type = Column(String(32), nullable=False)
    runtime_version = Column(String(64), nullable=False)
    runtime_session_id = Column(String(128), nullable=False)
    runtime_generation = Column(Integer, nullable=False, default=1)
    deployment_epoch = Column(Integer, nullable=False, default=1, server_default="1")
    status = Column(String(32), nullable=False, default="READY")
    last_event_seq = Column(Integer, nullable=False, default=0)
    last_context_snapshot_id = Column(String(128), nullable=True)
    lease_owner = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "runtime_type": self.runtime_type,
            "runtime_version": self.runtime_version,
            "runtime_session_id": self.runtime_session_id,
            "runtime_generation": self.runtime_generation,
            "deployment_epoch": self.deployment_epoch,
            "status": self.status,
            "last_event_seq": self.last_event_seq,
            "last_context_snapshot_id": self.last_context_snapshot_id,
            "lease_owner": self.lease_owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AgentRuntimeTurnModel(Base):
    """One user Turn submitted to an Agent Runtime (AcceptedTurn separated from completion)."""

    __tablename__ = "agent_runtime_turns"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", "turn_id", name="uq_agent_runtime_turn"),
        UniqueConstraint("idempotency_key", name="uq_agent_runtime_turn_idem"),
        Index("ix_agent_runtime_turns_idempotency_key", "idempotency_key"),
    )

    turn_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    runtime_session_id = Column(String(128), nullable=True)
    runtime_generation = Column(Integer, nullable=False, default=1)
    user_message = Column(Text, nullable=False)
    requested_mode = Column(String(40), nullable=True)
    disposition = Column(String(40), nullable=True)
    side_effect_policy = Column(String(24), nullable=True)
    actor_id = Column(String(128), nullable=True)
    client_command_id = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="ACCEPTED")
    accepted_mode = Column(String(32), nullable=False, default="deterministic")
    detail = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "runtime_session_id": self.runtime_session_id,
            "runtime_generation": self.runtime_generation,
            "user_message": self.user_message,
            "requested_mode": self.requested_mode,
            "disposition": self.disposition,
            "side_effect_policy": self.side_effect_policy,
            "actor_id": self.actor_id,
            "client_command_id": self.client_command_id,
            "status": self.status,
            "accepted_mode": self.accepted_mode,
            "detail": self.detail,
            "idempotency_key": self.idempotency_key,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AgentRuntimeEventModel(Base):
    """Normalized, replay-safe events emitted by an Agent Runtime.

    Private thinking is never persisted.  Event seq is unique within a
    generation so a sidecar restart/replay cannot duplicate a side effect.
    """

    __tablename__ = "agent_runtime_events"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "runtime_generation", "event_seq",
            name="uq_agent_runtime_event_seq",
        ),
        UniqueConstraint("idempotency_key", name="uq_agent_runtime_event_idem"),
        Index("ix_agent_runtime_events_idempotency_key", "idempotency_key"),
    )

    event_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    runtime_generation = Column(Integer, nullable=False, default=1)
    event_seq = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    cycle_id = Column(String(128), nullable=True, index=True)
    model_request_id = Column(String(128), nullable=True, index=True)
    evaluation_run_id = Column(String(128), nullable=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "runtime_generation": self.runtime_generation,
            "event_seq": self.event_seq,
            "event_type": self.event_type,
            "cycle_id": self.cycle_id,
            "model_request_id": self.model_request_id,
            "evaluation_run_id": self.evaluation_run_id,
            "payload": self.payload_json or {},
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }


class CaseEvidenceModel(Base):
    """Canonical per-Case Evidence record (G3).

    Attachment and legacy DiagnosisEvidence may project into this table, but
    only this table is consumed by conclusion validation and evidence-chain
    rendering.  Evidence IDs are deterministic from Task/Artifact provenance.
    """

    __tablename__ = "case_evidence"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "evidence_id", name="uq_case_evidence",
        ),
        Index("ix_case_evidence_lifecycle_status", "lifecycle_status"),
        Index("ix_case_evidence_review_trust_state", "review_trust_state"),
    )

    evidence_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    attachment_id = Column(String(128), nullable=True)
    task_id = Column(String(128), nullable=True, index=True)
    artifact_id = Column(Integer, nullable=True)
    artifact_type = Column(String(32), nullable=True)
    collector_id = Column(String(64), nullable=True)
    source_type = Column(String(32), nullable=False, default="task_artifact")
    source_id = Column(String(128), nullable=True)
    source_channel = Column(String(24), nullable=False, default="COLLECTOR", server_default="COLLECTOR")
    data_origin = Column(String(24), nullable=False, default="LIVE", server_default="LIVE")
    investigation_run_id = Column(String(128), nullable=True, index=True)
    execution_unit_id = Column(String(128), nullable=True, index=True)
    source_call_id = Column(String(128), nullable=True)
    membership_snapshot_id = Column(String(128), nullable=True)
    target_ref = Column(String(256), nullable=True)
    resource_incarnation = Column(String(256), nullable=True)
    content_hash = Column(String(64), nullable=True)
    projection_hash = Column(String(64), nullable=True)
    # ``status`` remains a compatibility projection while governance keeps
    # lifecycle, human trust, and UI organization as independent dimensions.
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    lifecycle_status = Column(String(24), nullable=False, default="ACTIVE", server_default="ACTIVE", index=True)
    review_trust_state = Column(String(24), nullable=False, default="UNREVIEWED", server_default="UNREVIEWED", index=True)
    review_revision = Column(Integer, nullable=False, default=0, server_default="0")
    derived_trust_score = Column(Integer, nullable=False, default=50, server_default="50")
    ui_hidden = Column(Boolean, nullable=False, default=False, server_default="0")
    ui_archived = Column(Boolean, nullable=False, default=False, server_default="0")
    quality = Column(String(20), nullable=False, default="UNKNOWN")
    freshness = Column(String(20), nullable=False, default="UNKNOWN")
    time_window_json = Column(JSON, nullable=False, default=dict)
    event_time_start = Column(DateTime(timezone=True), nullable=True)
    event_time_end = Column(DateTime(timezone=True), nullable=True)
    ingested_at = Column(DateTime(timezone=True), nullable=True)
    clock_id = Column(String(128), nullable=True)
    clock_offset_ms = Column(Integer, nullable=True)
    clock_uncertainty_ms = Column(Integer, nullable=True)
    artifact_schema = Column(String(64), nullable=True)
    schema_version = Column(String(32), nullable=True)
    producer_version = Column(String(64), nullable=True)
    raw_locator = Column(String(512), nullable=True)
    size_bytes = Column(BigInteger, nullable=False, default=0, server_default="0")
    sha256 = Column(String(64), nullable=True)
    completeness = Column(String(24), nullable=False, default="COMPLETE", server_default="COMPLETE")
    trust_level = Column(String(24), nullable=False, default="INTERNAL", server_default="INTERNAL")
    lineage_json = Column(JSON, nullable=False, default=dict)
    trace_id = Column(String(128), nullable=True)
    lifecycle_status = Column(String(24), nullable=False, default="ACTIVE", server_default="ACTIVE")
    review_trust_state = Column(String(24), nullable=False, default="UNREVIEWED", server_default="UNREVIEWED")
    review_revision = Column(Integer, nullable=False, default=0, server_default="0")
    derived_trust_score = Column(Integer, nullable=False, default=50, server_default="50")
    ui_hidden = Column(Boolean, nullable=False, default=False, server_default="0")
    ui_archived = Column(Boolean, nullable=False, default=False, server_default="0")
    late_after_cancel = Column(Boolean, nullable=False, default=False, server_default="0")
    stale_for_current_revision = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "attachment_id": self.attachment_id,
            "task_id": self.task_id,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "collector_id": self.collector_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_channel": self.source_channel,
            "data_origin": self.data_origin,
            "investigation_run_id": self.investigation_run_id,
            "execution_unit_id": self.execution_unit_id,
            "source_call_id": self.source_call_id,
            "membership_snapshot_id": self.membership_snapshot_id,
            "target_ref": self.target_ref,
            "resource_incarnation": self.resource_incarnation,
            "content_hash": self.content_hash,
            "projection_hash": self.projection_hash,
            "status": self.status,
            "lifecycle_status": self.lifecycle_status,
            "review_trust_state": self.review_trust_state,
            "review_revision": self.review_revision,
            "derived_trust_score": self.derived_trust_score,
            "ui_hidden": bool(self.ui_hidden),
            "ui_archived": bool(self.ui_archived),
            "quality": self.quality,
            "freshness": self.freshness,
            "time_window": self.time_window_json or {},
            "event_time_start": self.event_time_start,
            "event_time_end": self.event_time_end,
            "ingested_at": self.ingested_at,
            "clock_id": self.clock_id,
            "clock_offset_ms": self.clock_offset_ms,
            "clock_uncertainty_ms": self.clock_uncertainty_ms,
            "artifact_schema": self.artifact_schema,
            "schema_version": self.schema_version,
            "producer_version": self.producer_version,
            "raw_locator": self.raw_locator,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "completeness": self.completeness,
            "trust_level": self.trust_level,
            "lineage": self.lineage_json or {},
            "trace_id": self.trace_id,
            "lifecycle_status": self.lifecycle_status,
            "review_trust_state": self.review_trust_state,
            "review_revision": self.review_revision,
            "derived_trust_score": self.derived_trust_score,
            "ui_hidden": bool(self.ui_hidden),
            "ui_archived": bool(self.ui_archived),
            "late_after_cancel": bool(self.late_after_cancel),
            "stale_for_current_revision": bool(self.stale_for_current_revision),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── v6 canonical Agent core: Turn/Run/Cycle/Model/Proposal/Message ─────
