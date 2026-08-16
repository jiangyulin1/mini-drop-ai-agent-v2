from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from server.app.models.base import Base


class AgentModel(Base):
    __tablename__ = "agents"

    id = Column(String(128), primary_key=True)
    hostname = Column(String(256), nullable=False)
    ip_addr = Column(String(64), nullable=False)
    version = Column(String(32), default="0.1.0")
    os_info = Column(String(256), default="unknown")
    capabilities = Column(JSON, default=list)
    status = Column(String(16), default="ONLINE")
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "hostname": self.hostname,
            "ip_addr": self.ip_addr,
            "version": self.version,
            "os_info": self.os_info,
            "capabilities": self.capabilities or [],
            "status": self.status,
            "last_heartbeat_at": self.last_heartbeat_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── Task ────────────────────────────────────────────────────────


class TaskModel(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("uq_task_execution_unit", "execution_unit_id", unique=True),
        Index("ix_tasks_case_id", "case_id"),
        Index("ix_tasks_execution_unit_id", "execution_unit_id"),
    )

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False)
    target_pid = Column(Integer, nullable=False)
    collector_type = Column(String(32), nullable=False)
    sample_rate = Column(Integer, default=99)
    duration_sec = Column(Integer, default=15)
    status = Column(String(16), nullable=False)
    status_reason = Column(Text, default="")
    # ``status`` remains the backwards-compatible aggregate state exposed by
    # the original API.  Collection and analysis are persisted separately so
    # a successful capture is not lost when a later analyzer attempt fails.
    collection_status = Column(String(16), nullable=False, default="PENDING")
    analysis_status = Column(String(16), nullable=False, default="WAITING")
    current_attempt_id = Column(String(128), nullable=True, index=True)
    row_version = Column(Integer, nullable=False, default=0)
    collection_deadline_at = Column(DateTime(timezone=True), nullable=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    traceparent = Column(String(64), nullable=True)
    request_params = Column(JSON, default=dict)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    diagnosis_step_id = Column(String(128), nullable=True, unique=True, index=True)
    # v6 unified execution lineage.  Case-derived Tasks MUST have an
    # execution_unit_id; standalone Drop tasks keep these fields null.
    origin = Column(String(24), nullable=True, default=None)
    visibility = Column(String(24), nullable=True, default=None)
    case_id = Column(String(128), nullable=True)
    case_title = Column(String(256), nullable=True)
    turn_id = Column(String(128), nullable=True)
    plan_step_id = Column(String(128), nullable=True)
    step_revision_id = Column(String(128), nullable=True)
    campaign_id = Column(String(128), nullable=True)
    campaign_revision = Column(Integer, nullable=True)
    assignment_id = Column(String(128), nullable=True)
    execution_unit_id = Column(String(128), nullable=True)
    risk = Column(String(24), nullable=True)
    purpose = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    agent = relationship("AgentModel", lazy="selectin")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "agent_id": self.agent_id,
            "target_pid": self.target_pid,
            "collector_type": self.collector_type,
            "sample_rate": self.sample_rate,
            "duration_sec": self.duration_sec,
            "status": self.status,
            "status_reason": self.status_reason or "",
            "collection_status": self.collection_status,
            "analysis_status": self.analysis_status,
            "current_attempt_id": self.current_attempt_id,
            "row_version": self.row_version,
            "collection_deadline_at": self.collection_deadline_at,
            "request_id": self.request_id,
            "traceparent": self.traceparent,
            "request_params": self.request_params or {},
            "idempotency_key": self.idempotency_key,
            "origin": self.origin,
            "visibility": self.visibility,
            "case_id": self.case_id,
            "case_title": self.case_title,
            "turn_id": self.turn_id,
            "plan_step_id": self.plan_step_id,
            "step_revision_id": self.step_revision_id,
            "campaign_id": self.campaign_id,
            "campaign_revision": self.campaign_revision,
            "assignment_id": self.assignment_id,
            "execution_unit_id": self.execution_unit_id,
            "risk": self.risk,
            "purpose": self.purpose,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class TaskAttemptModel(Base):
    """One durable execution attempt for a Task.

    Results are keyed by attempt id so an Agent can replay a completed result
    after a network failure without overwriting evidence from an earlier run.
    """

    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt_number"),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="DELIVERED")
    runner_version = Column(String(64), nullable=True)
    exit_code = Column(Integer, nullable=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    result_message = Column(Text, nullable=True)
    resource_usage_json = Column(JSON, default=dict)
    artifact_ids_json = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.id,
            "task_id": self.task_id,
            "attempt_no": self.attempt_no,
            "agent_id": self.agent_id,
            "status": self.status,
            "runner_version": self.runner_version,
            "exit_code": self.exit_code,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "result_message": self.result_message,
            "resource_usage": self.resource_usage_json or {},
            "artifact_ids": self.artifact_ids_json or [],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }


# ── 状态事件 ────────────────────────────────────────────────────


class StatusEventModel(Base):
    __tablename__ = "task_status_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    from_status = Column(String(16), nullable=True)
    to_status = Column(String(16), nullable=False)
    reason = Column(Text, nullable=False)
    actor = Column(String(16), nullable=False)
    meta_json = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason": self.reason,
            "actor": self.actor,
            "metadata": self.meta_json or {},
            "created_at": self.created_at,
        }


# ── 审计日志 ────────────────────────────────────────────────────


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    agent_id = Column(String(128), nullable=True)
    task_id = Column(String(128), nullable=True)
    meta_json = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "message": self.message,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "metadata": self.meta_json or {},
            "created_at": self.created_at,
        }


class AuthorizationGrantModel(Base):
    """Durable, revocable authorization envelope for AI source access."""

    __tablename__ = "authorization_grants"

    id = Column(String(128), primary_key=True)
    principal_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    source_ids_json = Column(JSON, default=list)
    operations_json = Column(JSON, default=list)
    resource_scope_json = Column(JSON, default=dict)
    mode = Column(String(32), nullable=False)
    case_id = Column(String(128), nullable=True, index=True)
    constraints_json = Column(JSON, default=dict)
    valid_until = Column(DateTime(timezone=True), nullable=False, index=True)
    uses_remaining = Column(Integer, nullable=True)
    query_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ACTIVE", index=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(128), nullable=True)

    def to_dict(self) -> dict:
        return {
            "grant_id": self.id,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "source_ids": self.source_ids_json or [],
            "operations": self.operations_json or [],
            "resource_scope": self.resource_scope_json or {},
            "mode": self.mode,
            "case_id": self.case_id,
            "constraints": self.constraints_json or {},
            "valid_until": self.valid_until,
            "uses_remaining": self.uses_remaining,
            "query_count": self.query_count or 0,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
        }


# ── AI Incident Case 协作层 ─────────────────────────────────────


