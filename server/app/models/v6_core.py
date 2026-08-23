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
from pgvector.sqlalchemy import Vector

from server.app.diagnosis.embeddings import VECTOR_DIMENSIONS

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


class InvestigationTreeNodeModel(Base):
    """Durable branch-local node in the evidence-driven investigation tree."""

    __tablename__ = "investigation_tree_nodes"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", "node_id", name="uq_investigation_tree_node"),
        Index("ix_investigation_tree_nodes_run_status", "case_id", "tenant_id", "run_id", "status"),
        Index("ix_investigation_tree_nodes_parent", "case_id", "tenant_id", "parent_node_id"),
    )

    node_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=False, index=True)
    parent_node_id = Column(String(128), nullable=True, index=True)
    branch_id = Column(String(128), nullable=False, index=True)
    node_type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="OPEN", index=True)
    statement = Column(Text, nullable=False, default="")
    hypothesis_id = Column(String(128), nullable=True, index=True)
    obligation_json = Column(JSON, nullable=False, default=dict)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    invalidated_evidence_refs_json = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)
    depth = Column(Integer, nullable=False, default=0)
    revision = Column(Integer, nullable=False, default=1)
    replay_of_node_id = Column(String(128), nullable=True)
    invalidated_reason = Column(String(128), nullable=True)
    created_by = Column(String(128), nullable=False, default="agent")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "case_id": self.case_id,
            "tenant_id": self.tenant_id, "run_id": self.run_id,
            "parent_node_id": self.parent_node_id, "branch_id": self.branch_id,
            "node_type": self.node_type, "status": self.status,
            "statement": self.statement, "hypothesis_id": self.hypothesis_id,
            "obligation": self.obligation_json or {},
            "evidence_refs": self.evidence_refs_json or [],
            "invalidated_evidence_refs": self.invalidated_evidence_refs_json or [],
            "metadata": self.metadata_json or {}, "depth": self.depth,
            "revision": self.revision, "replay_of_node_id": self.replay_of_node_id,
            "invalidated_reason": self.invalidated_reason,
            "created_by": self.created_by, "created_at": self.created_at,
            "updated_at": self.updated_at, "closed_at": self.closed_at,
        }


class InvestigationTreeDependencyModel(Base):
    """Explicit node dependency used for invalidation propagation."""

    __tablename__ = "investigation_tree_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "node_id", "target_kind", "target_id", "relation",
            name="uq_investigation_tree_dependency",
        ),
        Index("ix_investigation_tree_dependencies_target", "case_id", "tenant_id", "target_kind", "target_id"),
    )

    dependency_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    node_id = Column(String(128), nullable=False, index=True)
    target_kind = Column(String(32), nullable=False)
    target_id = Column(String(256), nullable=False)
    relation = Column(String(32), nullable=False, default="REQUIRES")
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    invalidated_reason = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "dependency_id": self.dependency_id, "case_id": self.case_id,
            "tenant_id": self.tenant_id, "node_id": self.node_id,
            "target_kind": self.target_kind, "target_id": self.target_id,
            "relation": self.relation, "status": self.status,
            "invalidated_reason": self.invalidated_reason,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class InvestigationTreeEventModel(Base):
    """Append-only state transition log for replay and operator audit."""

    __tablename__ = "investigation_tree_events"
    __table_args__ = (
        Index("ix_investigation_tree_events_node_created", "node_id", "created_at"),
        Index("ix_investigation_tree_events_run_created", "case_id", "tenant_id", "run_id", "created_at"),
    )

    event_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=False, index=True)
    node_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    reason = Column(String(256), nullable=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    actor_id = Column(String(128), nullable=False, default="agent")
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "case_id": self.case_id,
            "tenant_id": self.tenant_id, "run_id": self.run_id,
            "node_id": self.node_id, "event_type": self.event_type,
            "from_status": self.from_status, "to_status": self.to_status,
            "reason": self.reason, "payload": self.payload_json or {},
            "actor_id": self.actor_id, "created_at": self.created_at,
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


class KnowledgeDocumentModel(Base):
    """Tenant knowledge supplied by an operator, optionally scoped to one Case."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_sha256", "case_id", name="uq_knowledge_document_scope_hash"),
        Index("ix_knowledge_documents_tenant_scope", "tenant_id", "scope", "status"),
    )

    document_id = Column(String(128), primary_key=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=True, index=True)
    scope = Column(String(16), nullable=False, default="CASE")
    kind = Column(String(24), nullable=False, default="DOCUMENT")
    title = Column(String(256), nullable=False)
    filename = Column(String(512), nullable=True)
    media_type = Column(String(128), nullable=False, default="text/plain")
    content_text = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="ACTIVE", index=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self, *, include_content: bool = False) -> dict:
        value = {
            "document_id": self.document_id,
            "tenant_id": self.tenant_id,
            "case_id": self.case_id,
            "scope": self.scope,
            "kind": self.kind,
            "title": self.title,
            "filename": self.filename,
            "media_type": self.media_type,
            "content_sha256": self.content_sha256,
            "content_length": len(self.content_text or ""),
            "preview": (self.content_text or "")[:420],
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_content:
            value["content_text"] = self.content_text
        return value


class KnowledgeChunkModel(Base):
    """A bounded retrieval unit derived from one operator document."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_document_index"),
        Index("ix_knowledge_chunks_tenant_case", "tenant_id", "case_id"),
    )

    chunk_id = Column(String(128), primary_key=True)
    document_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=True, index=True)
    chunk_index = Column(Integer, nullable=False)
    start_offset = Column(Integer, nullable=False)
    end_offset = Column(Integer, nullable=False)
    content_text = Column(Text, nullable=False)
    lexical_text = Column(Text, nullable=False, default="")
    content_sha256 = Column(String(64), nullable=False)
    term_frequencies = Column(JSON, nullable=False, default=dict)
    embedding = Column(Vector(VECTOR_DIMENSIONS).with_variant(JSON(), "sqlite"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self, *, include_content: bool = True) -> dict:
        value = {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "case_id": self.case_id,
            "chunk_index": self.chunk_index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "content_sha256": self.content_sha256,
        }
        if include_content:
            value["content"] = self.content_text
        return value


class CaseMemoryModel(Base):
    """Reviewed, durable retrospective for one investigation conversation."""

    __tablename__ = "case_memories"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", name="uq_case_memory_case_tenant"),
    )

    memory_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    summary_text = Column(Text, nullable=False, default="")
    highlights = Column(JSON, nullable=False, default=list)
    evidence_refs = Column(JSON, nullable=False, default=list)
    source_event_seq = Column(Integer, nullable=False, default=0)
    auto_capture = Column(Boolean, nullable=False, default=True)
    promoted_document_id = Column(String(128), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "summary_text": self.summary_text,
            "highlights": self.highlights or [],
            "evidence_refs": self.evidence_refs or [],
            "source_event_seq": self.source_event_seq,
            "auto_capture": self.auto_capture,
            "promoted_document_id": self.promoted_document_id,
            "generated_at": self.generated_at,
            "updated_at": self.updated_at,
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


class CollectionProposalModel(Base):
    __tablename__ = "collection_proposals"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", "proposal_id", name="uq_collection_proposal"),
        Index("ix_collection_proposals_case_status", "case_id", "status"),
    )

    proposal_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    agent_run_id = Column(String(128), nullable=True, index=True)
    cycle_id = Column(String(128), nullable=True, index=True)
    plan_step_id = Column(String(128), nullable=True, index=True)
    plan_revision = Column(Integer, nullable=True)
    collector_id = Column(String(128), nullable=False)
    collector_spec_version = Column(String(32), nullable=False)
    target_selector = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)
    time_window = Column(JSON, nullable=False, default=dict)
    information_goal = Column(Text, nullable=False)
    reason_summary = Column(Text, nullable=False, default="")
    expected_cost = Column(JSON, nullable=False, default=dict)
    expected_risk = Column(String(24), nullable=False)
    input_evidence_refs = Column(JSON, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="PROPOSED", index=True)
    validation_result = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id, "case_id": self.case_id,
            "tenant_id": self.tenant_id, "agent_run_id": self.agent_run_id,
            "cycle_id": self.cycle_id, "collector_id": self.collector_id,
            "plan_step_id": self.plan_step_id, "plan_revision": self.plan_revision,
            "collector_spec_version": self.collector_spec_version,
            "target_selector": self.target_selector or {}, "parameters": self.parameters or {},
            "time_window": self.time_window or {}, "information_goal": self.information_goal,
            "reason_summary": self.reason_summary, "expected_cost": self.expected_cost or {},
            "expected_risk": self.expected_risk,
            "input_evidence_refs": self.input_evidence_refs or [], "status": self.status,
            "validation_result": self.validation_result or {}, "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


class CollectionRequestModel(Base):
    __tablename__ = "collection_requests"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_collection_request_proposal"),
        UniqueConstraint("idempotency_key", name="uq_collection_request_idempotency"),
        Index("ix_collection_requests_case_status", "case_id", "status"),
        Index(
            "ix_collection_requests_case_tenant_status_created",
            "case_id", "tenant_id", "status", "created_at",
        ),
    )

    collection_request_id = Column(String(128), primary_key=True)
    proposal_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    collector_id = Column(String(128), nullable=False)
    collector_spec_version = Column(String(32), nullable=False)
    resolved_target_identity = Column(JSON, nullable=False, default=dict)
    effective_parameters = Column(JSON, nullable=False, default=dict)
    runtime_generation = Column(Integer, nullable=False, default=1)
    control_revision = Column(Integer, nullable=False, default=1)
    scope_revision = Column(Integer, nullable=False, default=1)
    plan_step_id = Column(String(128), nullable=True, index=True)
    plan_revision = Column(Integer, nullable=True)
    idempotency_key = Column(String(256), nullable=False)
    budget_reservation = Column(JSON, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="ACCEPTED", index=True)
    task_id = Column(String(128), nullable=True, index=True)
    attempt_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "collection_request_id": self.collection_request_id,
            "proposal_id": self.proposal_id, "case_id": self.case_id,
            "tenant_id": self.tenant_id, "collector_id": self.collector_id,
            "collector_spec_version": self.collector_spec_version,
            "resolved_target_identity": self.resolved_target_identity or {},
            "effective_parameters": self.effective_parameters or {},
            "runtime_generation": self.runtime_generation,
            "control_revision": self.control_revision, "scope_revision": self.scope_revision,
            "plan_step_id": self.plan_step_id, "plan_revision": self.plan_revision,
            "idempotency_key": self.idempotency_key,
            "budget_reservation": self.budget_reservation or {}, "status": self.status,
            "task_id": self.task_id, "attempt_ids": self.attempt_ids or [],
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class EvidenceReuseDecisionModel(Base):
    """Branch-local audit of an explicit collection-result reuse decision.

    ``CaseEvidenceModel`` and ``EvidenceProjectionModel`` remain the shared
    fact stores.  This table records only the decision made by one run/cycle;
    its presence must never make an Evidence item implicitly visible to a
    different branch.  The identity and revision snapshots are retained so a
    later review/scope change can explain why the decision is no longer valid.
    """

    __tablename__ = "evidence_reuse_decisions"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "investigation_run_id", "contract_digest",
            "probe_fingerprint", "evidence_id", "projection_hash",
            name="uq_evidence_reuse_decision_idempotency",
        ),
        Index(
            "ix_evidence_reuse_decisions_probe",
            "case_id", "tenant_id", "probe_fingerprint", "created_at",
        ),
        Index(
            "ix_evidence_reuse_decisions_run",
            "case_id", "tenant_id", "investigation_run_id", "created_at",
        ),
        Index(
            "ix_evidence_reuse_decisions_evidence",
            "case_id", "tenant_id", "evidence_id", "projection_hash",
        ),
    )

    decision_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    investigation_run_id = Column(String(128), nullable=True, index=True)
    cycle_id = Column(String(128), nullable=True, index=True)
    obligation_id = Column(String(256), nullable=True)
    contract_digest = Column(String(128), nullable=False, default="", server_default="")
    collector_id = Column(String(128), nullable=False)
    collector_spec_version = Column(String(32), nullable=False, default="unknown", server_default="unknown")
    probe_fingerprint = Column(String(128), nullable=False, index=True)
    result_fingerprint = Column(String(128), nullable=True)
    collection_request_id = Column(String(128), nullable=True, index=True)
    task_id = Column(String(128), nullable=True, index=True)
    evidence_id = Column(String(128), nullable=True, index=True)
    projection_id = Column(String(128), nullable=True)
    projection_hash = Column(String(128), nullable=True)
    target_identity_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    requested_time_window_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    effective_time_window_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    control_revision = Column(Integer, nullable=False, default=1, server_default="1")
    scope_revision = Column(Integer, nullable=False, default=1, server_default="1")
    runtime_generation = Column(Integer, nullable=False, default=1, server_default="1")
    evidence_review_revision = Column(Integer, nullable=True)
    lifecycle_status = Column(String(24), nullable=True)
    trust_state = Column(String(24), nullable=True)
    decision = Column(String(32), nullable=False)
    reason_codes_json = Column(JSON, nullable=False, default=list, server_default="[]")
    actor_id = Column(String(128), nullable=False, default="agent", server_default="agent")
    source = Column(String(64), nullable=False, default="collection_supervisor", server_default="collection_supervisor")
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_reason = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "usage_id": self.decision_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "investigation_run_id": self.investigation_run_id,
            "cycle_id": self.cycle_id,
            "obligation_id": self.obligation_id,
            "contract_digest": self.contract_digest or "",
            "collector_id": self.collector_id,
            "collector_spec_version": self.collector_spec_version,
            "probe_fingerprint": self.probe_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "collection_request_id": self.collection_request_id,
            "task_id": self.task_id,
            "evidence_id": self.evidence_id,
            "projection_id": self.projection_id,
            "projection_hash": self.projection_hash,
            "target_identity": self.target_identity_json or {},
            "requested_time_window": self.requested_time_window_json or {},
            "effective_time_window": self.effective_time_window_json or {},
            "control_revision": int(self.control_revision or 0),
            "scope_revision": int(self.scope_revision or 0),
            "runtime_generation": int(self.runtime_generation or 0),
            "evidence_review_revision": self.evidence_review_revision,
            "review_revision": self.evidence_review_revision,
            "lifecycle_status": self.lifecycle_status,
            "trust_state": self.trust_state,
            "decision": self.decision,
            "reason_codes": self.reason_codes_json or [],
            "actor_id": self.actor_id,
            "source": self.source,
            "invalidated_at": self.invalidated_at,
            "invalidated_reason": self.invalidated_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def usage_id(self) -> str:
        """Compatibility name used by branch-ledger consumers."""
        return self.decision_id

    @property
    def review_revision(self) -> int | None:
        return self.evidence_review_revision


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
        Index(
            "ix_evidence_projections_case_tenant_evidence_created",
            "case_id", "tenant_id", "evidence_id", "created_at",
        ),
        Index(
            "ix_evidence_projections_case_tenant_created",
            "case_id", "tenant_id", "created_at",
        ),
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
        UniqueConstraint(
            "evidence_id",
            "review_revision",
            name="uq_evidence_review_revision_v6",
        ),
    )

    review_revision_id = Column(String(128), primary_key=True)
    evidence_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    review_revision = Column(Integer, nullable=False, default=1)
    decision = Column(String(24), nullable=False)
    lifecycle_status = Column(String(24), nullable=False, default="ACTIVE", server_default="ACTIVE")
    trust_state = Column(String(24), nullable=False, default="UNREVIEWED", server_default="UNREVIEWED")
    derived_trust_score = Column(Integer, nullable=False, default=50, server_default="50")
    projection_hash = Column(String(64), nullable=True)
    reason_code = Column(String(64), nullable=True)
    reason = Column(Text, nullable=True)
    assessment_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    recommendation_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    impact_json = Column(JSON, nullable=False, default=dict, server_default="{}")
    overridden_recommendation = Column(Boolean, nullable=False, default=False, server_default="0")
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
            "lifecycle_status": self.lifecycle_status,
            "trust_state": self.trust_state,
            "derived_trust_score": self.derived_trust_score,
            "projection_hash": self.projection_hash,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "assessment": self.assessment_json or {},
            "recommendation": self.recommendation_json or {},
            "impact": self.impact_json or {},
            "overridden_recommendation": bool(self.overridden_recommendation),
            "reviewed_by": self.reviewed_by,
            "created_at": self.created_at,
        }


class EvidenceAnalysisRunModel(Base):
    __tablename__ = "evidence_analysis_runs"
    __table_args__ = (
        Index("ix_evidence_analysis_runs_case_status", "case_id", "status"),
        Index("ix_evidence_analysis_runs_turn", "runtime_turn_id"),
        UniqueConstraint(
            "case_id", "tenant_id", "input_fingerprint",
            name="uq_evidence_analysis_input_fingerprint",
        ),
    )

    analysis_run_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    mode = Column(String(16), nullable=False, default="SINGLE")
    evidence_inputs = Column(JSON, nullable=False, default=list)
    input_fingerprint = Column(String(64), nullable=False)
    model_config_id = Column(String(128), nullable=True)
    prompt_version = Column(String(64), nullable=False, default="evidence-analysis.v1")
    side_effect_policy = Column(String(24), nullable=False, default="READ_ONLY")
    facts = Column(JSON, nullable=False, default=list)
    anomalies = Column(JSON, nullable=False, default=list)
    interpretations = Column(JSON, nullable=False, default=list)
    conflicts = Column(JSON, nullable=False, default=list)
    limitations = Column(JSON, nullable=False, default=list)
    next_collection_proposals = Column(JSON, nullable=False, default=list)
    input_state = Column(String(32), nullable=False, default="CURRENT")
    status = Column(String(24), nullable=False, default="QUEUED", index=True)
    token_usage = Column(JSON, nullable=False, default=dict)
    latency_ms = Column(Integer, nullable=True)
    runtime_turn_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "analysis_run_id": self.analysis_run_id, "case_id": self.case_id,
            "tenant_id": self.tenant_id, "mode": self.mode,
            "evidence_inputs": self.evidence_inputs or [], "model_config_id": self.model_config_id,
            "input_fingerprint": self.input_fingerprint,
            "prompt_version": self.prompt_version, "side_effect_policy": self.side_effect_policy,
            "facts": self.facts or [], "anomalies": self.anomalies or [],
            "interpretations": self.interpretations or [], "conflicts": self.conflicts or [],
            "limitations": self.limitations or [],
            "next_collection_proposals": self.next_collection_proposals or [],
            "input_state": self.input_state, "status": self.status,
            "token_usage": self.token_usage or {}, "latency_ms": self.latency_ms,
            "runtime_turn_id": self.runtime_turn_id, "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class DomainOutboxModel(Base):
    __tablename__ = "domain_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_domain_outbox_dedupe"),
        Index("ix_domain_outbox_status_available", "status", "available_at"),
        Index("ix_domain_outbox_claim_expiry", "status", "claim_expires_at"),
    )

    outbox_id = Column(String(128), primary_key=True)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    aggregate_revision = Column(Integer, nullable=False, default=0)
    payload_schema_version = Column(String(32), nullable=False, default="1.0")
    payload = Column(JSON, nullable=False, default=dict)
    dedupe_key = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    claim_token = Column(String(128), nullable=True)
    claimed_by = Column(String(128), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=8)
    last_error = Column(Text, nullable=True)
    dispatch_outcome = Column(String(32), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    dead_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "outbox_id": self.outbox_id,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type,
            "aggregate_revision": self.aggregate_revision,
            "payload_schema_version": self.payload_schema_version,
            "payload": self.payload or {},
            "dedupe_key": self.dedupe_key,
            "status": self.status,
            "available_at": self.available_at,
            "claim_token": self.claim_token,
            "claimed_by": self.claimed_by,
            "claim_expires_at": self.claim_expires_at,
            "claimed_at": self.claimed_at,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "dispatch_outcome": self.dispatch_outcome,
            "delivered_at": self.delivered_at,
            "dead_at": self.dead_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class OutboxConsumerEffectModel(Base):
    """Durable idempotency receipt and effect record for one consumer."""

    __tablename__ = "outbox_consumer_effects"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "consumer_name",
            name="uq_outbox_consumer_event",
        ),
        UniqueConstraint(
            "consumer_name",
            "effect_key",
            name="uq_outbox_consumer_effect_key",
        ),
    )

    receipt_id = Column(String(128), primary_key=True)
    event_id = Column(String(128), nullable=False, index=True)
    consumer_name = Column(String(128), nullable=False, index=True)
    effect_key = Column(String(256), nullable=False)
    effect_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "event_id": self.event_id,
            "consumer_name": self.consumer_name,
            "effect_key": self.effect_key,
            "effect_payload": self.effect_payload or {},
            "created_at": self.created_at,
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

    wakeup_id = Column(String(128), nullable=False, index=True)
    # One coalesced wakeup may contain many outbox events. The outbox event is
    # the identity of this mapping; using wakeup_id as the primary key prevents
    # the second event in a batch from being attached.
    outbox_id = Column(String(128), nullable=False, primary_key=True)
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


# ── v6 Evidence dependency / Causal / Gap / Conclusion / Repair ─────


class EvidenceDependencyEdgeModel(Base):
    """Durable Evidence-to-inference dependency and invalidation ledger."""

    __tablename__ = "evidence_dependency_edges"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "source_kind", "source_id",
            "target_kind", "target_id", "relation",
            name="uq_evidence_dependency_edge",
        ),
        Index("ix_evidence_dependency_source", "case_id", "tenant_id", "source_id"),
        Index("ix_evidence_dependency_target", "case_id", "tenant_id", "target_kind", "target_id"),
    )

    dependency_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    source_kind = Column(String(24), nullable=False, default="EVIDENCE")
    source_id = Column(String(256), nullable=False)
    target_kind = Column(String(24), nullable=False)
    target_id = Column(String(256), nullable=False)
    relation = Column(String(32), nullable=False, default="SUPPORTS")
    support_weight = Column(Float, nullable=False, default=1.0)
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    invalidated_by_evidence_id = Column(String(128), nullable=True)
    invalidated_review_revision = Column(Integer, nullable=True)
    invalidated_reason = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "dependency_id": self.dependency_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "relation": self.relation,
            "support_weight": self.support_weight,
            "status": self.status,
            "invalidated_by_evidence_id": self.invalidated_by_evidence_id,
            "invalidated_review_revision": self.invalidated_review_revision,
            "invalidated_reason": self.invalidated_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConfidenceChainSnapshotModel(Base):
    """Versioned, explainable confidence ledger for any Case chain."""

    __tablename__ = "confidence_chain_snapshots"
    __table_args__ = (
        UniqueConstraint("case_id", "chain_type", "chain_id", "revision", name="uq_confidence_chain_snapshot"),
        Index("ix_confidence_chain_case_current", "case_id", "chain_type", "chain_id", "revision"),
    )

    snapshot_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    chain_type = Column(String(32), nullable=False)
    chain_id = Column(String(256), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="ACTIVE")
    computed_confidence = Column(Float, nullable=False, default=0.0)
    operator_requested_confidence = Column(Float, nullable=True)
    effective_confidence = Column(Float, nullable=False, default=0.0)
    confidence_cap = Column(Float, nullable=False, default=1.0)
    calculation_version = Column(String(64), nullable=False)
    confidence_reason = Column(Text, nullable=False, default="")
    invalidated_evidence_refs = Column(JSON, nullable=False, default=list)
    remaining_active_support = Column(JSON, nullable=False, default=list)
    ledger_json = Column(JSON, nullable=False, default=list)
    operator_adjustment_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "chain_type": self.chain_type,
            "chain_id": self.chain_id,
            "revision": self.revision,
            "status": self.status,
            "computed_confidence": self.computed_confidence,
            "operator_requested_confidence": self.operator_requested_confidence,
            "effective_confidence": self.effective_confidence,
            "confidence_cap": self.confidence_cap,
            "calculation_version": self.calculation_version,
            "confidence_reason": self.confidence_reason,
            "invalidated_evidence_refs": self.invalidated_evidence_refs or [],
            "remaining_active_support": self.remaining_active_support or [],
            "ledger": self.ledger_json or [],
            "operator_adjustment": self.operator_adjustment_json or {},
            "created_at": self.created_at,
        }


class ConfidenceAdjustmentModel(Base):
    """Immutable operator confidence adjustment audit record."""

    __tablename__ = "confidence_adjustments"
    __table_args__ = (
        Index("ix_confidence_adjustments_chain", "case_id", "chain_type", "chain_id", "created_at"),
    )

    adjustment_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    chain_type = Column(String(32), nullable=False)
    chain_id = Column(String(256), nullable=False)
    revision_before = Column(Integer, nullable=False)
    revision_after = Column(Integer, nullable=False)
    confidence_before = Column(Float, nullable=False)
    requested_confidence = Column(Float, nullable=False)
    effective_confidence = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    evidence_refs = Column(JSON, nullable=False, default=list)
    calculation_version = Column(String(64), nullable=False)
    actor_id = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "adjustment_id": self.adjustment_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "chain_type": self.chain_type,
            "chain_id": self.chain_id,
            "revision_before": self.revision_before,
            "revision_after": self.revision_after,
            "confidence_before": self.confidence_before,
            "requested_confidence": self.requested_confidence,
            "effective_confidence": self.effective_confidence,
            "reason": self.reason,
            "evidence_refs": self.evidence_refs or [],
            "calculation_version": self.calculation_version,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
        }


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
    graph_id = Column(String(128), primary_key=True, nullable=False, index=True)
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
    graph_id = Column(String(128), primary_key=True, nullable=False, index=True)
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
    dependency_status = Column(String(24), nullable=False, default="ACTIVE")
    invalidated_evidence_refs = Column(JSON, nullable=False, default=list)
    remaining_active_support = Column(JSON, nullable=False, default=list)
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
            "dependency_status": self.dependency_status,
            "invalidated_evidence_refs": self.invalidated_evidence_refs or [],
            "remaining_active_support": self.remaining_active_support or [],
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
    root_location_json = Column(JSON, nullable=False, default=dict)
    mechanism_json = Column(JSON, nullable=False, default=dict)
    confidence_reason = Column(Text, nullable=False, default="")
    evidence_gap_ids = Column(JSON, nullable=False, default=list)
    recommendation_ids = Column(JSON, nullable=False, default=list)
    limitations = Column(JSON, nullable=False, default=list)
    abstention_reason = Column(Text, nullable=True)
    report_text = Column(Text, nullable=True)
    created_from_cycle_id = Column(String(128), nullable=True)
    model_request_id = Column(String(128), nullable=True)
    verifier_version = Column(String(64), nullable=False, default="causal-report-verifier.v1")
    invalidated_claims = Column(JSON, nullable=False, default=list)
    remaining_active_support = Column(JSON, nullable=False, default=dict)
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
            "root_location": self.root_location_json or {},
            "mechanism": self.mechanism_json or {},
            "confidence_reason": self.confidence_reason,
            "evidence_gap_ids": self.evidence_gap_ids or [],
            "recommendation_ids": self.recommendation_ids or [],
            "limitations": self.limitations or [],
            "abstention_reason": self.abstention_reason,
            "report_text": self.report_text,
            "created_from_cycle_id": self.created_from_cycle_id,
            "model_request_id": self.model_request_id,
            "verifier_version": self.verifier_version,
            "invalidated_claims": self.invalidated_claims or [],
            "remaining_active_support": self.remaining_active_support or {},
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
    claim_status = Column(String(24), nullable=False, default="ACTIVE")
    invalidated_evidence_refs = Column(JSON, nullable=False, default=list)
    remaining_active_support = Column(JSON, nullable=False, default=list)
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
            "claim_status": self.claim_status,
            "invalidated_evidence_refs": self.invalidated_evidence_refs or [],
            "remaining_active_support": self.remaining_active_support or [],
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
