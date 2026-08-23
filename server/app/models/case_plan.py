from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
)

from server.app.models.base import Base


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
    control_revision = Column(Integer, nullable=False, default=1, server_default="1")
    case_command_revision = Column(Integer, nullable=False, default=1, server_default="1")
    deployment_epoch = Column(Integer, nullable=False, default=1, server_default="1")
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
            "control_revision": self.control_revision,
            "case_command_revision": self.case_command_revision,
            "deployment_epoch": self.deployment_epoch,
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


class CaseResourceAttachmentModel(Base):
    """Unified data-entry binding: a ResourceRef attached to a Case (E1).

    Replaces the multi-way split (initial_task_ids / source_task_id /
    target_scope.evidence_task_ids / source_collection_ids) with one
    tenant-scoped row so a Task, a Collection batch or a conversation `@`
    reference can be proven to enter the next diagnosis.
    """

    __tablename__ = "case_resource_attachments"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "resource_type", "resource_id",
            name="uq_attachment_case_resource",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    resource_type = Column(String(40), nullable=False)
    resource_id = Column(String(128), nullable=False)
    resource_revision = Column(Integer, nullable=True)
    label = Column(String(256), nullable=False)
    source = Column(String(40), nullable=False)
    purpose = Column(Text, nullable=True)
    attached_by = Column(String(128), nullable=False)
    status = Column(String(40), nullable=False, default="PENDING_VALIDATION")
    scope_match = Column(String(20), nullable=False, default="UNKNOWN")
    time_match = Column(String(20), nullable=False, default="UNKNOWN")
    freshness = Column(String(20), nullable=False, default="UNKNOWN")
    quality = Column(String(20), nullable=False, default="UNKNOWN")
    evidence_ids_json = Column(JSON, default=list)
    rejection_reason = Column(String(128), nullable=True)
    supersedes_json = Column(JSON, default=list)
    row_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "attachment_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "resource_ref": {
                "type": self.resource_type,
                "id": self.resource_id,
                "revision": self.resource_revision,
            },
            "label": self.label,
            "source": self.source,
            "purpose": self.purpose,
            "attached_by": self.attached_by,
            "status": self.status,
            "scope_match": self.scope_match,
            "time_match": self.time_match,
            "freshness": self.freshness,
            "quality": self.quality,
            "evidence_ids": self.evidence_ids_json or [],
            "rejection_reason": self.rejection_reason,
            "supersedes": self.supersedes_json or [],
            "row_version": self.row_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class InvestigationPlanModel(Base):
    """Persistent, versioned investigation plan (E2, plan 5.4)."""

    __tablename__ = "investigation_plans"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "plan_revision", name="uq_plan_case_revision",
        ),
    )

    plan_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    plan_revision = Column(Integer, nullable=False, default=0)
    scope_revision = Column(Integer, nullable=False, default=0)
    goal = Column(String(500), nullable=False)
    status = Column(String(24), nullable=False, default="ACTIVE")
    source = Column(String(40), nullable=False, default="deterministic")
    created_by = Column(String(128), nullable=False)
    row_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "plan_revision": self.plan_revision,
            "scope_revision": self.scope_revision,
            "goal": self.goal,
            "status": self.status,
            "source": self.source,
            "created_by": self.created_by,
            "row_version": self.row_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class InvestigationPlanStepModel(Base):
    """A single plan step with its own state machine and revisions (E2)."""

    __tablename__ = "investigation_plan_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id"],
            ["investigation_plans.plan_id"],
            name="fk_plan_step_plan",
        ),
    )

    step_id = Column(String(128), primary_key=True)
    plan_id = Column(String(128), nullable=False)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    plan_revision = Column(Integer, nullable=False, default=0)
    scope_revision = Column(Integer, nullable=False, default=0)
    kind = Column(String(32), nullable=False)
    collector_id = Column(String(128), nullable=True)
    target_refs_json = Column(JSON, nullable=True)
    purpose = Column(String(500), nullable=True)
    hypothesis_refs_json = Column(JSON, nullable=True)
    expected_information = Column(String(500), nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    priority_source = Column(String(16), nullable=False, default="AI")
    user_locked = Column(Boolean, nullable=False, default=False)
    depends_on_json = Column(JSON, nullable=True)
    risk = Column(String(24), nullable=False, default="READ_LOW")
    # E3.5：集群 Step 的选择策略（ALL_IN_SCOPE/REPRESENTATIVE/OUTLIERS/...）
    selection_strategy = Column(String(40), nullable=True)
    status = Column(String(32), nullable=False, default="DRAFT")
    task_ids_json = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "plan_id": self.plan_id,
            "case_id": self.case_id,
            "plan_revision": self.plan_revision,
            "scope_revision": self.scope_revision,
            "kind": self.kind,
            "collector_id": self.collector_id,
            "target_refs": self.target_refs_json or [],
            "purpose": self.purpose,
            "hypothesis_refs": self.hypothesis_refs_json or [],
            "expected_information": self.expected_information,
            "priority": self.priority,
            "priority_source": self.priority_source,
            "user_locked": self.user_locked,
            "depends_on": self.depends_on_json or [],
            "risk": self.risk,
            "selection_strategy": self.selection_strategy,
            "status": self.status,
            "task_ids": self.task_ids_json or [],
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MembershipSnapshotModel(Base):
    """E3.5: 冻结的集群成员快照；调查期间成员变化不修改历史快照。"""

    __tablename__ = "membership_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "snapshot_id", name="uq_membership_snapshot",
        ),
    )

    snapshot_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    environment_id = Column(String(128), nullable=False, default="")
    cluster_id = Column(String(128), nullable=False, default="")
    topology_version = Column(String(64), nullable=False, default="")
    scope_revision = Column(Integer, nullable=False, default=1)
    members_json = Column(JSON, nullable=False, default=list)
    captured_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "environment_id": self.environment_id,
            "cluster_id": self.cluster_id,
            "topology_version": self.topology_version,
            "scope_revision": self.scope_revision,
            "members": self.members_json or [],
            "captured_at": self.captured_at,
        }


class FanoutCollectionRunModel(Base):
    """E3.5: 一个逻辑采集步骤展开出的多个单目标 Task 及聚合结果。"""

    __tablename__ = "fanout_collection_runs"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "run_id", name="uq_fanout_run",
        ),
    )

    run_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    plan_step_id = Column(String(128), nullable=False, default="")
    plan_revision = Column(Integer, nullable=False, default=0)
    scope_revision = Column(Integer, nullable=False, default=1)
    snapshot_id = Column(String(128), nullable=False, default="")
    strategy = Column(String(40), nullable=False, default="ALL_IN_SCOPE")
    collector_id = Column(String(128), nullable=False, default="sys_metrics")
    target_members_json = Column(JSON, nullable=False, default=list)
    task_ids_json = Column(JSON, nullable=False, default=list)
    member_task_map_json = Column(JSON, nullable=False, default=dict)
    task_statuses_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="RUNNING")
    coverage = Column(Float, nullable=False, default=0.0)
    failed_count = Column(Integer, nullable=False, default=0)
    quorum_met = Column(Boolean, nullable=False, default=False)
    aggregate_json = Column(JSON, nullable=False, default=dict)
    late_result_isolated_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "plan_step_id": self.plan_step_id,
            "plan_revision": self.plan_revision,
            "scope_revision": self.scope_revision,
            "snapshot_id": self.snapshot_id,
            "strategy": self.strategy,
            "collector_id": self.collector_id,
            "target_members": self.target_members_json or [],
            "task_ids": self.task_ids_json or [],
            "member_task_map": self.member_task_map_json or {},
            "task_statuses": self.task_statuses_json or {},
            "status": self.status,
            "coverage": self.coverage or 0.0,
            "failed_count": self.failed_count or 0,
            "quorum_met": self.quorum_met,
            "aggregate": self.aggregate_json or {},
            "late_result_isolated": self.late_result_isolated_json or [],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class EvidenceReviewModel(Base):
    """User/system decision about an evidence item (E2, plan 5.5)."""

    __tablename__ = "evidence_reviews"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "evidence_id", "review_revision",
            name="uq_evidence_review_revision",
        ),
    )

    review_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    evidence_id = Column(String(128), nullable=False)
    decision = Column(String(20), nullable=False)
    reason_code = Column(String(64), nullable=True)
    reason = Column(String(1000), nullable=True)
    actor_id = Column(String(128), nullable=False)
    review_revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "review_id": self.review_id,
            "case_id": self.case_id,
            "evidence_id": self.evidence_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "review_revision": self.review_revision,
            "created_at": self.created_at,
        }


class CollectionDecisionModel(Base):
    """Recorded reuse/recollect decision (E2, plan 5.3)."""

    __tablename__ = "collection_decisions"

    decision_id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    requested_collector = Column(String(128), nullable=False)
    purpose = Column(String(500), nullable=True)
    result = Column(String(32), nullable=False)
    reused_task_ids_json = Column(JSON, nullable=True)
    new_plan_step_ids_json = Column(JSON, nullable=True)
    reason_codes_json = Column(JSON, nullable=True)
    estimated_cost_json = Column(JSON, nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "case_id": self.case_id,
            "requested_collector": self.requested_collector,
            "purpose": self.purpose,
            "result": self.result,
            "reused_task_ids": self.reused_task_ids_json or [],
            "new_plan_step_ids": self.new_plan_step_ids_json or [],
            "reason_codes": self.reason_codes_json or [],
            "estimated_cost": self.estimated_cost_json or {},
            "created_by": self.created_by,
            "created_at": self.created_at,
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
    case_event_seq = Column(Integer, nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    actor_id = Column(String(128), nullable=False)
    payload_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "case_event_seq": self.case_event_seq,
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
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    evidence_hold_json = Column(JSON, nullable=False, default=dict)
    requires_approval = Column(Integer, nullable=False, default=1)
    approved_by = Column(String(128), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    evidence_refs_json = Column(JSON, nullable=False, default=list, server_default="[]")
    evidence_hold_json = Column(JSON, nullable=False, default=dict, server_default="{}")
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
            "evidence_refs": self.evidence_refs_json or [],
            "evidence_hold": self.evidence_hold_json or {},
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
    cache_read_tokens = Column(Integer, nullable=True)
    cache_write_tokens = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    retry_count = Column(Integer, nullable=True, default=0)
    response_hash = Column(String(64), nullable=True)
    error_code = Column(String(128), nullable=True)
    turn_id = Column(String(128), nullable=True)
    context_snapshot_id = Column(String(128), nullable=True)
    config_fingerprint = Column(String(128), nullable=True)
    tool_catalog_version = Column(String(128), nullable=True)
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
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost": self.cost,
            "retry_count": self.retry_count,
            "response_hash": self.response_hash,
            "error_code": self.error_code,
            "turn_id": self.turn_id,
            "context_snapshot_id": self.context_snapshot_id,
            "config_fingerprint": self.config_fingerprint,
            "tool_catalog_version": self.tool_catalog_version,
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


class ActionAttemptModel(Base):
    """Durable record of one registered action attempt lifecycle.

    Phases: dry_run / execute / verify / rollback. Idempotent per
    (case_id, tenant_id, operation_key, phase) so Control restarts cannot
    duplicate a logical action attempt.
    """

    __tablename__ = "action_attempts"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "operation_key", "phase",
            name="uq_case_action_phase",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_action_attempt_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    action_id = Column(String(128), nullable=False, index=True)
    operation_key = Column(String(256), nullable=False, index=True)
    phase = Column(String(32), nullable=False)
    parameters_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)
    row_version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "action_id": self.action_id,
            "operation_key": self.operation_key,
            "phase": self.phase,
            "parameters": self.parameters_json or {},
            "result": self.result_json or {},
            "row_version": self.row_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CaseRuntimeLeaseModel(Base):
    """Short-lived lease so only one Control copy advances a Case at a time."""

    __tablename__ = "case_runtime_leases"
    __table_args__ = (
        UniqueConstraint("case_id", "tenant_id", name="uq_case_runtime_lease"),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_lease_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False, index=True)
    owner = Column(String(128), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False, index=True)
    row_version = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "lease_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "owner": self.owner,
            "lease_until": self.lease_until,
            "row_version": self.row_version,
        }


class CaseCommandModel(Base):
    """Queued user/system command for a Case (pause/resume/stop/correction/approval)."""

    __tablename__ = "case_commands"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "tenant_id", "idempotency_key", name="uq_case_command_idem",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_command_case_tenant",
            ondelete="CASCADE",
        ),
    )

    id = Column(String(128), primary_key=True)
    case_id = Column(String(128), nullable=False, index=True)
    tenant_id = Column(String(128), nullable=False)
    command_type = Column(String(32), nullable=False)
    idempotency_key = Column(String(256), nullable=False)
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    payload_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "command_id": self.id,
            "case_id": self.case_id,
            "tenant_id": self.tenant_id,
            "command_type": self.command_type,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "payload": self.payload_json or {},
            "created_at": self.created_at,
            "processed_at": self.processed_at,
        }


class SystemControlModel(Base):
    """Global governance controls (Red Button, capability key rotation epoch)."""

    __tablename__ = "system_controls"

    control_name = Column(String(64), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    value_json = Column(JSON, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "control_name": self.control_name,
            "enabled": bool(self.enabled),
            "value": self.value_json or {},
            "updated_at": self.updated_at,
        }


# ── 产物 ───────────────────────────────────────────────────────




# ── Monotonic CaseEvent sequence ─────────────────────────────────────
# case_event_seq is assigned inside the insert transaction so historical and
# future event writers share one cursor.  It is intentionally not a client
# default: the next value must be computed from the same table snapshot.
@event.listens_for(CaseEventModel, "before_insert")
def _assign_case_event_seq(mapper, connection, target) -> None:
    if target.case_event_seq is not None:
        return
    current = connection.execute(
        select(func.max(CaseEventModel.case_event_seq)).where(
            CaseEventModel.case_id == target.case_id,
            CaseEventModel.tenant_id == target.tenant_id,
        )
    ).scalar()
    target.case_event_seq = int(current or 0) + 1
