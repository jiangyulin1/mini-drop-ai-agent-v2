"""SQLAlchemy ORM 模型定义。

与 InMemoryRepository 的数据类结构对齐，
通过 SQLAlchemy 2.0 DeclarativeBase 映射。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Agent ────────────────────────────────────────────────────────


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


class IncidentCaseModel(Base):
    """Tenant-scoped user collaboration aggregate over a diagnosis session."""

    __tablename__ = "incident_cases"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_incident_case_tenant"),
    )

    id = Column(String(128), primary_key=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    created_by = Column(String(128), nullable=False, index=True)
    diagnosis_session_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=True, index=True,
    )
    target_session_id = Column(
        String(128), ForeignKey(
            "diagnostic_target_sessions.id", name="fk_incident_case_target_session",
        ), nullable=True, index=True,
    )
    source_task_id = Column(String(128), ForeignKey("tasks.id"), nullable=True, index=True)
    # 数据驱动入口：同一事故窗口、同一明确实例范围内的已完成 Task 证据。
    initial_task_ids = Column(JSON, default=list)
    title = Column(String(256), nullable=False)
    problem_description = Column(Text, nullable=False)
    recovery_goal = Column(Text, nullable=False)
    run_mode = Column(String(32), nullable=False)
    environment = Column(String(64), nullable=False)
    target_scope_json = Column(JSON, default=dict)
    time_range_json = Column(JSON, default=dict)
    state = Column(String(40), nullable=False, index=True)
    state_reason = Column(String(128), nullable=False)
    impact_json = Column(JSON, default=dict)
    current_finding_json = Column(JSON, default=dict)
    current_activity_json = Column(JSON, default=dict)
    need_user_json = Column(JSON, default=dict)
    recovery_json = Column(JSON, default=dict)
    scope_revision = Column(Integer, nullable=False, default=1)
    row_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "case_id": self.id,
            "tenant_id": self.tenant_id,
            "created_by": self.created_by,
            "diagnosis_session_id": self.diagnosis_session_id,
            "target_session_id": self.target_session_id,
            "source_task_id": self.source_task_id,
            "initial_task_ids": self.initial_task_ids or [],
            "title": self.title,
            "problem_description": self.problem_description,
            "recovery_goal": self.recovery_goal,
            "run_mode": self.run_mode,
            "environment": self.environment,
            "target_scope": self.target_scope_json or {},
            "time_range": self.time_range_json or {},
            "state": self.state,
            "state_reason": self.state_reason,
            "summary": {
                "impact": self.impact_json or {},
                "current_finding": self.current_finding_json or {},
                "what_ai_is_doing": self.current_activity_json or {},
                "need_you": self.need_user_json or {},
                "recovery": self.recovery_json or {},
            },
            "scope_revision": self.scope_revision,
            "row_version": self.row_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stopped_at": self.stopped_at,
            "resolved_at": self.resolved_at,
        }


class DiagnosticTargetSessionModel(Base):
    """Long-lived tenant target that accumulates signals and incident Cases."""

    __tablename__ = "diagnostic_target_sessions"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_target_session_tenant"),
        UniqueConstraint(
            "tenant_id", "environment", "service_id",
            name="uq_target_session_tenant_environment_service",
        ),
    )

    id = Column(String(128), primary_key=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    service_id = Column(String(128), nullable=False, index=True)
    environment = Column(String(64), nullable=False, index=True)
    display_name = Column(String(256), nullable=False)
    target_scope_json = Column(JSON, default=dict)
    baseline_json = Column(JSON, default=dict)
    signal_policy_json = Column(JSON, default=dict)
    status = Column(String(24), nullable=False, index=True)
    row_version = Column(Integer, nullable=False, default=0)
    latest_signal_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "target_session_id": self.id,
            "tenant_id": self.tenant_id,
            "service_id": self.service_id,
            "environment": self.environment,
            "display_name": self.display_name,
            "target_scope": self.target_scope_json or {},
            "baseline": self.baseline_json or {},
            "signal_policy": self.signal_policy_json or {},
            "status": self.status,
            "row_version": self.row_version,
            "latest_signal_at": self.latest_signal_at,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TargetSignalModel(Base):
    """Immutable normalized signal received by a long-lived target session."""

    __tablename__ = "target_signals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_session_id", "tenant_id"],
            ["diagnostic_target_sessions.id", "diagnostic_target_sessions.tenant_id"],
            name="fk_target_signal_session_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "target_session_id", "dedupe_key", name="uq_target_signal_dedupe",
        ),
    )

    id = Column(String(128), primary_key=True)
    target_session_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    signal_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    payload_json = Column(JSON, default=dict)
    profile_window_ids_json = Column(JSON, default=list)
    dedupe_key = Column(String(128), nullable=False)
    status = Column(String(24), nullable=False)
    triggered_case_id = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "signal_id": self.id,
            "target_session_id": self.target_session_id,
            "tenant_id": self.tenant_id,
            "signal_type": self.signal_type,
            "severity": self.severity,
            "observed_at": self.observed_at,
            "payload": self.payload_json or {},
            "profile_window_ids": self.profile_window_ids_json or [],
            "dedupe_key": self.dedupe_key,
            "status": self.status,
            "triggered_case_id": self.triggered_case_id,
            "created_at": self.created_at,
        }


class ProfileWindowModel(Base):
    """Queryable index over a continuous profiling capture window."""

    __tablename__ = "profile_windows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_session_id", "tenant_id"],
            ["diagnostic_target_sessions.id", "diagnostic_target_sessions.tenant_id"],
            name="fk_profile_window_session_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "target_session_id", "task_id", "window_index",
            name="uq_profile_window_target_task_index",
        ),
        Index(
            "ix_profile_window_target_time",
            "target_session_id", "tenant_id", "window_start", "window_end",
        ),
    )

    id = Column(String(128), primary_key=True)
    target_session_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    agent_id = Column(String(128), nullable=False, index=True)
    target_pid = Column(Integer, nullable=False)
    window_index = Column(Integer, nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False, index=True)
    window_end = Column(DateTime(timezone=True), nullable=False, index=True)
    granularity = Column(String(24), nullable=False, default="detail")
    artifact_refs_json = Column(JSON, default=list)
    meta_json = Column("metadata", JSON, default=dict)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "profile_window_id": self.id,
            "target_session_id": self.target_session_id,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "target_pid": self.target_pid,
            "window_index": self.window_index,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "granularity": self.granularity,
            "artifact_refs": self.artifact_refs_json or [],
            "metadata": self.meta_json or {},
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


class CaseEventModel(Base):
    """Immutable, tenant-bound Case timeline event."""

    __tablename__ = "case_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_case_event_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    actor_id = Column(String(128), nullable=False)
    payload_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "payload": self.payload_json or {},
            "created_at": self.created_at,
        }


class CaseRecoveryPlanModel(Base):
    """Durable Case recovery workflow from proposal through verification/rollback."""

    __tablename__ = "case_recovery_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_recovery_plan_case_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "dry_run_attempt_id", name="uq_case_recovery_plans_dry_run_attempt_id",
        ),
        Index("ix_recovery_plan_case_status", "case_id", "tenant_id", "status"),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False)
    tenant_id = Column(String(128), nullable=False)
    diagnosis_session_id = Column(String(128), nullable=True, index=True)
    action_id = Column(String(128), nullable=False, index=True)
    parameters_json = Column(JSON, default=dict)
    value_after_fix = Column(Text, default="")
    verification_method = Column(Text, default="")
    status = Column(String(40), nullable=False, index=True)
    policy_json = Column(JSON, default=dict)
    dry_run_attempt_id = Column(String(128), nullable=True)
    dry_run_json = Column(JSON, default=dict)
    execution_json = Column(JSON, default=dict)
    verification_json = Column(JSON, default=dict)
    rollback_json = Column(JSON, default=dict)
    requires_approval = Column(Integer, nullable=False, default=1)
    approved_by = Column(String(128), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    row_version = Column(Integer, nullable=False, default=0)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "recovery_plan_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "diagnosis_session_id": self.diagnosis_session_id,
            "action_id": self.action_id,
            "parameters": self.parameters_json or {},
            "value_after_fix": self.value_after_fix or "",
            "verification_method": self.verification_method or "",
            "status": self.status,
            "policy": self.policy_json or {},
            "dry_run_attempt_id": self.dry_run_attempt_id,
            "dry_run": self.dry_run_json or {},
            "execution": self.execution_json or {},
            "verification": self.verification_json or {},
            "rollback": self.rollback_json or {},
            "requires_approval": bool(self.requires_approval),
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "rejection_reason": self.rejection_reason,
            "row_version": self.row_version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ServiceChangeModel(Base):
    """用户登记的发布/配置变更（变更登记，C 方案，见 docs/ai_diagnosis_agent_design.md §7）。

    供 AI 做"变更前 vs 变更后"对比与回归关联；也能由 AI 走 Need You 追问后回填。
    """

    __tablename__ = "service_changes"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_service_change_tenant"),
        Index(
            "ix_service_changes_tenant_service",
            "tenant_id",
            "service_id",
            "changed_at",
        ),
    )

    id = Column(String(128), primary_key=True)
    tenant_id = Column(String(128), nullable=False)
    service_id = Column(String(128), nullable=False)
    environment = Column(String(64), nullable=False, default="unknown")
    change_type = Column(String(32), nullable=False)  # release/config/feature_flag/scale/other
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    changed_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "change_id": self.id,
            "tenant_id": self.tenant_id,
            "service_id": self.service_id,
            "environment": self.environment,
            "change_type": self.change_type,
            "title": self.title,
            "description": self.description,
            "changed_at": self.changed_at,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


class ContextPacketModel(Base):
    """Immutable, versioned projection used as one Case model-call input."""

    __tablename__ = "case_context_packets"
    __table_args__ = (
        UniqueConstraint(
            "id", "case_id", "tenant_id", name="uq_context_packet_case_tenant",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_context_packet_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    schema_version = Column(String(64), nullable=False)
    purpose = Column(String(128), nullable=False)
    iteration_no = Column(Integer, nullable=False)
    payload_json = Column(JSON, nullable=False)
    projection_stats_json = Column(JSON, default=dict)
    source_versions_json = Column(JSON, default=dict)
    content_hash = Column(String(64), nullable=False)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "context_packet_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "schema_version": self.schema_version,
            "purpose": self.purpose,
            "iteration_no": self.iteration_no,
            "payload": self.payload_json or {},
            "projection_stats": self.projection_stats_json or {},
            "source_versions": self.source_versions_json or {},
            "content_hash": self.content_hash,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


class ModelAttemptModel(Base):
    """Auditable model-call metadata; raw reasoning and credentials are never stored."""

    __tablename__ = "case_model_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["context_packet_id", "case_id", "tenant_id"],
            [
                "case_context_packets.id",
                "case_context_packets.case_id",
                "case_context_packets.tenant_id",
            ],
            name="fk_model_attempt_context_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    context_packet_id = Column(String(128), nullable=False, index=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    model_snapshot = Column(String(128), nullable=True)
    prompt_version = Column(String(128), nullable=False)
    output_schema = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    latency_ms = Column(Integer, nullable=False, default=0)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    response_hash = Column(String(64), nullable=True)
    error_code = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "model_attempt_id": self.id,
            "context_packet_id": self.context_packet_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "provider": self.provider,
            "model": self.model,
            "model_snapshot": self.model_snapshot,
            "prompt_version": self.prompt_version,
            "output_schema": self.output_schema,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "response_hash": self.response_hash,
            "error_code": self.error_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class CaseHypothesisNodeModel(Base):
    """Normalized Case hypothesis with explicit support, contradiction and gaps."""

    __tablename__ = "case_hypothesis_nodes"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "hypothesis_id", name="uq_case_hypothesis",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_hypothesis_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    hypothesis_id = Column(String(128), nullable=False, index=True)
    statement = Column(Text, nullable=False)
    root_entity = Column(String(256), nullable=True)
    mechanism = Column(String(128), nullable=True)
    affected_entities_json = Column(JSON, default=list)
    status = Column(String(32), nullable=False, index=True)
    supporting_evidence_refs_json = Column(JSON, default=list)
    contradicting_evidence_refs_json = Column(JSON, default=list)
    missing_evidence_json = Column(JSON, default=list)
    alternatives_json = Column(JSON, default=list)
    score_components_json = Column(JSON, default=dict)
    source = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "statement": self.statement,
            "root_entity": self.root_entity,
            "mechanism": self.mechanism,
            "affected_entities": self.affected_entities_json or [],
            "status": self.status,
            "supporting_evidence_refs": self.supporting_evidence_refs_json or [],
            "contradicting_evidence_refs": self.contradicting_evidence_refs_json or [],
            "missing_evidence": self.missing_evidence_json or [],
            "alternatives": self.alternatives_json or [],
            "score_components": self.score_components_json or {},
            "source": self.source,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CaseHypothesisEdgeModel(Base):
    __tablename__ = "case_hypothesis_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_hypothesis_edge_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    source_hypothesis_id = Column(String(128), nullable=False)
    target_hypothesis_id = Column(String(128), nullable=False)
    relation = Column(String(32), nullable=False)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "source": self.source_hypothesis_id,
            "target": self.target_hypothesis_id,
            "relation": self.relation,
            "metadata": self.metadata_json or {},
            "created_at": self.created_at,
        }


class InvestigationIterationModel(Base):
    """One auditable Case investigation decision and its observed outcome."""

    __tablename__ = "case_investigation_iterations"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "iteration_no", name="uq_case_iteration_no",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_iteration_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    iteration_no = Column(Integer, nullable=False)
    context_packet_id = Column(
        String(128), ForeignKey("case_context_packets.id"), nullable=True, index=True,
    )
    status = Column(String(32), nullable=False, index=True)
    input_evidence_refs_json = Column(JSON, default=list)
    hypothesis_changes_json = Column(JSON, default=list)
    candidate_actions_json = Column(JSON, default=list)
    selected_action_json = Column(JSON, default=dict)
    policy_decision_json = Column(JSON, default=dict)
    cost_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)
    stop_decision_json = Column(JSON, default=dict)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "iteration_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "iteration_no": self.iteration_no,
            "context_packet_id": self.context_packet_id,
            "status": self.status,
            "input_evidence_refs": self.input_evidence_refs_json or [],
            "hypothesis_changes": self.hypothesis_changes_json or [],
            "candidate_actions": self.candidate_actions_json or [],
            "selected_action": self.selected_action_json or {},
            "policy_decision": self.policy_decision_json or {},
            "cost": self.cost_json or {},
            "result": self.result_json or {},
            "stop_decision": self.stop_decision_json or {},
            "created_by": self.created_by,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


# ── 产物 ───────────────────────────────────────────────────────


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    attempt_id = Column(String(128), ForeignKey("task_attempts.id"), nullable=True, index=True)
    identity_key = Column(String(64), nullable=True, unique=True, index=True)
    artifact_type = Column(String(32), nullable=False)
    bucket = Column(String(64), default="mini-drop")
    object_key = Column(String(512), nullable=False)
    filename = Column(String(256), nullable=True)
    local_path = Column(String(512), nullable=True)
    content_type = Column(String(128), default="application/octet-stream")
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String(64), nullable=True)
    meta_json = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "attempt_id": self.attempt_id,
            "artifact_type": self.artifact_type,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "filename": self.filename,
            "local_path": self.local_path,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "metadata": self.meta_json or {},
            "created_at": self.created_at,
        }


class AnalysisJobModel(Base):
    """Lease-based asynchronous analysis work item."""

    __tablename__ = "analysis_jobs"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "attempt_id", "pipeline",
            name="uq_analysis_job_task_attempt_pipeline",
        ),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    attempt_id = Column(String(128), ForeignKey("task_attempts.id"), nullable=False, index=True)
    pipeline = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING", index=True)
    priority = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    analyzer_version = Column(String(64), nullable=True)
    input_artifact_ids_json = Column(JSON, default=list)
    output_artifact_ids_json = Column(JSON, default=list)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "analysis_job_id": self.id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "pipeline": self.pipeline,
            "status": self.status,
            "priority": self.priority,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "analyzer_version": self.analyzer_version,
            "input_artifact_ids": self.input_artifact_ids_json or [],
            "output_artifact_ids": self.output_artifact_ids_json or [],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }


class AnalyzerWorkerModel(Base):
    """Readiness heartbeat for an independently deployed Analyzer process."""

    __tablename__ = "analyzer_workers"

    id = Column(String(128), primary_key=True)
    version = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False)
    current_job_id = Column(String(128), nullable=True, index=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "worker_id": self.id,
            "version": self.version,
            "status": self.status,
            "current_job_id": self.current_job_id,
            "last_heartbeat_at": self.last_heartbeat_at,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }


# ── 智能归因 ───────────────────────────────────────────────────


class DiagnosisRunModel(Base):
    __tablename__ = "diagnosis_runs"

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    model_name = Column(String(64), nullable=False)
    summary = Column(Text, default="")
    validated = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status,
            "model_name": self.model_name,
            "summary": self.summary or "",
            "validated": bool(self.validated),
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class DiagnosisToolResultModel(Base):
    __tablename__ = "diagnosis_tool_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    tool_name = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    evidence_ref = Column(String(128), nullable=False)
    input_json = Column(JSON, default=dict)
    output_json = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "input": self.input_json or {},
            "output": self.output_json or {},
            "error_message": self.error_message,
            "created_at": self.created_at,
        }


class DiagnosisReportModel(Base):
    __tablename__ = "diagnosis_reports"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    report_json = Column(JSON, default=dict)
    ranked_causes_json = Column(JSON, default=list)
    confidence = Column(Integer, default=0)
    not_enough_evidence = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "report": self.report_json or {},
            "ranked_causes": self.ranked_causes_json or [],
            "confidence": (self.confidence or 0) / 1000,
            "not_enough_evidence": bool(self.not_enough_evidence),
            "created_at": self.created_at,
        }


class RepairPlanModel(Base):
    __tablename__ = "repair_plans"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    cause_id = Column(String(128), nullable=False)
    risk_level = Column(String(32), nullable=False)
    actions_json = Column(JSON, default=list)
    executed_actions_json = Column(JSON, default=list)
    requires_user_confirm = Column(Integer, default=1)
    status = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "cause_id": self.cause_id,
            "risk_level": self.risk_level,
            "actions": self.actions_json or [],
            "executed_actions": self.executed_actions_json or [],
            "requires_user_confirm": bool(self.requires_user_confirm),
            "status": self.status,
            "created_at": self.created_at,
        }


class RCAFeedbackModel(Base):
    __tablename__ = "rca_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    task_id = Column(String(128), nullable=False, index=True)
    predicted_cause_id = Column(String(128), nullable=False)
    feedback_label = Column(String(32), nullable=False)
    corrected_cause_id = Column(String(128), nullable=True)
    feedback_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class RCAFeedbackWeightModel(Base):
    __tablename__ = "rca_feedback_weights"

    candidate_id = Column(String(128), primary_key=True)
    positive_count = Column(Integer, default=0)
    negative_count = Column(Integer, default=0)
    partial_count = Column(Integer, default=0)
    weight_delta = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False)


# ── Agent 指标快照 ───────────────────────────────────────────────


class AgentMetricSnapshotModel(Base):
    """Agent 周期性资源开销快照，用于趋势分析和容量规划。"""

    __tablename__ = "agent_metric_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False, index=True)
    cpu_percent = Column(Integer, default=0)
    rss_mb = Column(Integer, default=0)
    read_kb_s = Column(Integer, default=0)
    write_kb_s = Column(Integer, default=0)
    children_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "cpu_percent": self.cpu_percent,
            "rss_mb": self.rss_mb,
            "read_kb_s": self.read_kb_s,
            "write_kb_s": self.write_kb_s,
            "children_count": self.children_count,
            "created_at": self.created_at,
        }


# ── AI 集群诊断控制层 ────────────────────────────────────────────


class TopologySnapshotModel(Base):
    """诊断创建时冻结的服务/实例/宿主机拓扑。"""

    __tablename__ = "topology_snapshots"

    id = Column(String(128), primary_key=True)
    effective_at = Column(DateTime(timezone=True), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    nodes_json = Column(JSON, default=list)
    edges_json = Column(JSON, default=list)
    source_versions_json = Column(JSON, default=dict)
    confidence_summary_json = Column(JSON, default=dict)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.id,
            "effective_at": self.effective_at,
            "generated_at": self.generated_at,
            "nodes": self.nodes_json or [],
            "edges": self.edges_json or [],
            "source_versions": self.source_versions_json or {},
            "confidence_summary": self.confidence_summary_json or {},
        }


class DiagnosisSessionModel(Base):
    """独立于单个采集 Task 的、可恢复的诊断工作流。"""

    __tablename__ = "diagnosis_sessions"

    id = Column(String(128), primary_key=True)
    creator_id = Column(String(128), nullable=False)
    raw_query = Column(Text, nullable=False)
    normalized_intent_json = Column(JSON, default=dict)
    target_scope_json = Column(JSON, default=dict)
    requested_time_range_json = Column(JSON, default=dict)
    effective_time_range_json = Column(JSON, default=dict)
    topology_snapshot_id = Column(
        String(128), ForeignKey("topology_snapshots.id"), nullable=True, index=True,
    )
    baseline_snapshot_id = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False)
    policy_profile = Column(String(64), nullable=False)
    risk_budget_json = Column(JSON, default=dict)
    resource_budget_json = Column(JSON, default=dict)
    budget_used_json = Column(JSON, default=dict)
    hypothesis_graph_json = Column(JSON, default=dict)
    evaluation_oracle_json = Column(JSON, default=dict)
    child_task_ids_json = Column(JSON, default=list)
    conclusion_versions_json = Column(JSON, default=list)
    # 数据驱动入口：initial_tasks 装载为初始证据的记录
    initial_evidence_loaded_json = Column(JSON, default=list)
    initial_evidence_count = Column(Integer, default=0)
    model_version = Column(String(128), nullable=False)
    planner_version = Column(String(64), nullable=False)
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    paused_from_status = Column(String(32), nullable=True)
    row_version = Column(Integer, nullable=False, default=0)
    deadline_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "diagnosis_id": self.id,
            "creator_id": self.creator_id,
            "raw_query": self.raw_query,
            "initial_evidence_loaded": self.initial_evidence_loaded_json or [],
            "initial_evidence_count": self.initial_evidence_count or 0,
            "normalized_intent": self.normalized_intent_json or {},
            "target_scope": self.target_scope_json or {},
            "requested_time_range": self.requested_time_range_json or {},
            "effective_time_range": self.effective_time_range_json or {},
            "topology_snapshot_id": self.topology_snapshot_id,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "status": self.status,
            "policy_profile": self.policy_profile,
            "risk_budget": self.risk_budget_json or {},
            "resource_budget": self.resource_budget_json or {},
            "budget_used": self.budget_used_json or {},
            "hypothesis_graph": self.hypothesis_graph_json or {},
            "evaluation_oracle": self.evaluation_oracle_json or {},
            "child_task_ids": self.child_task_ids_json or [],
            "conclusion_versions": self.conclusion_versions_json or [],
            "model_version": self.model_version,
            "planner_version": self.planner_version,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
            "paused_from_status": self.paused_from_status,
            "row_version": self.row_version,
            "deadline_at": self.deadline_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DiagnosisEventModel(Base):
    __tablename__ = "diagnosis_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    event_type = Column(String(64), nullable=False)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    payload_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "event_type": self.event_type,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "payload": self.payload_json or {},
            "created_at": self.created_at,
        }


class ProbeExecutionModel(Base):
    """一次受控探针计划/审批/执行记录；step id 同时作为幂等键。"""

    __tablename__ = "diagnosis_probe_executions"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    probe_id = Column(String(128), nullable=False)
    target_json = Column(JSON, default=dict)
    parameters_json = Column(JSON, default=dict)
    reason = Column(Text, nullable=False)
    risk_level = Column(String(8), nullable=False)
    status = Column(String(32), nullable=False)
    requires_approval = Column(Integer, default=0)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=True, index=True)
    approved_by = Column(String(128), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    retry_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "step_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "probe_id": self.probe_id,
            "target": self.target_json or {},
            "parameters": self.parameters_json or {},
            "reason": self.reason,
            "risk_level": self.risk_level,
            "status": self.status,
            "requires_approval": bool(self.requires_approval),
            "task_id": self.task_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class DiagnosisOutboxModel(Base):
    """Transactional intent to create the one Task belonging to a probe step."""

    __tablename__ = "diagnosis_task_outbox"

    id = Column(String(160), primary_key=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True)
    step_id = Column(String(128), ForeignKey("diagnosis_probe_executions.id"), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="PENDING")
    attempt = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class DiagnosisEvidenceModel(Base):
    """可追溯到 Task/Artifact 的不可变证据摘要。"""

    __tablename__ = "diagnosis_evidence"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    source_type = Column(String(32), nullable=False)
    source_system = Column(String(64), nullable=False)
    evidence_role = Column(String(32), nullable=False, default="incident")
    target_json = Column(JSON, default=dict)
    event_time_range_json = Column(JSON, default=dict)
    ingestion_time = Column(DateTime(timezone=True), nullable=False)
    query_or_probe = Column(String(256), nullable=False)
    raw_artifact_ref = Column(String(512), nullable=True)
    derived_artifact_ref = Column(String(512), nullable=True)
    derivation_version = Column(String(64), nullable=False)
    observed_value_json = Column(JSON, default=dict)
    baseline_value_json = Column(JSON, default=dict)
    anomaly_score_json = Column(JSON, default=dict)
    data_quality_json = Column(JSON, default=dict)
    integrity_hash = Column(String(80), nullable=False)
    claim_links_json = Column(JSON, default=list)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "source_type": self.source_type,
            "source_system": self.source_system,
            "evidence_role": self.evidence_role,
            "target": self.target_json or {},
            "event_time_range": self.event_time_range_json or {},
            "ingestion_time": self.ingestion_time,
            "query_or_probe": self.query_or_probe,
            "raw_artifact_ref": self.raw_artifact_ref,
            "derived_artifact_ref": self.derived_artifact_ref,
            "derivation_version": self.derivation_version,
            "observed_value": self.observed_value_json or {},
            "baseline_value": self.baseline_value_json or {},
            "anomaly_score": self.anomaly_score_json or {},
            "data_quality": self.data_quality_json or {},
            "integrity_hash": self.integrity_hash,
            "claim_links": self.claim_links_json or [],
        }


class DiagnosisNodeRunModel(Base):
    """显式诊断流水线节点的可恢复运行记录。"""

    __tablename__ = "diagnosis_node_runs"
    __table_args__ = (UniqueConstraint("diagnosis_id", "node_name", name="uq_diagnosis_node_name"),)

    id = Column(String(256), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    node_name = Column(String(64), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    input_refs_json = Column(JSON, default=list)
    output_refs_json = Column(JSON, default=list)
    metrics_json = Column(JSON, default=dict)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    implementation_version = Column(String(64), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "node_run_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "node_name": self.node_name,
            "sequence": self.sequence,
            "status": self.status,
            "attempt": self.attempt,
            "input_refs": self.input_refs_json or [],
            "output_refs": self.output_refs_json or [],
            "metrics": self.metrics_json or {},
            "error_code": self.error_code,
            "error_message": self.error_message,
            "implementation_version": self.implementation_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }
