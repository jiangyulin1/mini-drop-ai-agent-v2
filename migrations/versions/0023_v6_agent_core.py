"""Add v6 canonical Agent core: run/cycle/model/projection/outbox/wakeup/campaign/causal.

Revision ID: 0023_v6_agent_core
Revises: 0022_case_evidence
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from server.app.models import Base

revision: str = "0023_v6_agent_core"
down_revision: Union[str, Sequence[str], None] = "0022_case_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

V6_TABLES = (
    "investigation_runs",
    "case_context_snapshots",
    "agent_cycles",
    "model_requests",
    "model_responses",
    "assistant_messages",
    "agent_proposals",
    "agent_decision_records",
    "evidence_projections",
    "evidence_review_revisions",
    "domain_outbox",
    "runtime_wakeups",
    "runtime_wakeup_sources",
    "operation_specs",
    "campaign_revisions",
    "acquisition_assignments",
    "execution_units",
    "causal_graph_revisions",
    "causal_nodes",
    "causal_edges",
    "evidence_gaps",
    "conclusion_revisions",
    "claim_evidence_bindings",
    "repair_recommendations",
    "deployment_assessments",
)


def _add_existing_table_columns(bind) -> None:
    # SQLite/PG shared migration path; each column is additive and nullable or
    # has a server default so an existing database can upgrade in place.
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "incident_cases" in tables:
        cols = {c["name"] for c in inspector.get_columns("incident_cases")}
        with op.batch_alter_table("incident_cases") as batch:
            if "control_revision" not in cols:
                batch.add_column(sa.Column("control_revision", sa.Integer(), nullable=False, server_default="1"))
            if "case_command_revision" not in cols:
                batch.add_column(sa.Column("case_command_revision", sa.Integer(), nullable=False, server_default="1"))
            if "deployment_epoch" not in cols:
                batch.add_column(sa.Column("deployment_epoch", sa.Integer(), nullable=False, server_default="1"))

    if "tasks" in tables:
        cols = {c["name"] for c in inspector.get_columns("tasks")}
        task_additions = (
            ("origin", sa.String(24)),
            ("visibility", sa.String(24)),
            ("case_id", sa.String(128)),
            ("case_title", sa.String(256)),
            ("turn_id", sa.String(128)),
            ("plan_step_id", sa.String(128)),
            ("step_revision_id", sa.String(128)),
            ("campaign_id", sa.String(128)),
            ("campaign_revision", sa.Integer()),
            ("assignment_id", sa.String(128)),
            ("execution_unit_id", sa.String(128)),
            ("risk", sa.String(24)),
            ("purpose", sa.Text()),
        )
        with op.batch_alter_table("tasks") as batch:
            for name, typ in task_additions:
                if name not in cols:
                    batch.add_column(sa.Column(name, typ, nullable=True))
        try:
            op.create_index("ix_tasks_case_id", "tasks", ["case_id"])
        except Exception:
            pass
        try:
            op.create_index("ix_tasks_execution_unit_id", "tasks", ["execution_unit_id"])
        except Exception:
            pass
        try:
            op.create_index("uq_task_execution_unit", "tasks", ["execution_unit_id"], unique=True)
        except Exception:
            pass

    if "case_evidence" in tables:
        cols = {c["name"] for c in inspector.get_columns("case_evidence")}
        with op.batch_alter_table("case_evidence") as batch:
            additions = (
                ("source_channel", sa.String(24), "COLLECTOR", False),
                ("data_origin", sa.String(24), "LIVE", False),
                ("investigation_run_id", sa.String(128), None, True),
                ("execution_unit_id", sa.String(128), None, True),
                ("source_call_id", sa.String(128), None, True),
                ("membership_snapshot_id", sa.String(128), None, True),
                ("resource_incarnation", sa.String(256), None, True),
                ("event_time_start", sa.DateTime(timezone=True), None, True),
                ("event_time_end", sa.DateTime(timezone=True), None, True),
                ("ingested_at", sa.DateTime(timezone=True), None, True),
                ("clock_id", sa.String(128), None, True),
                ("clock_offset_ms", sa.Integer(), None, True),
                ("clock_uncertainty_ms", sa.Integer(), None, True),
                ("artifact_schema", sa.String(64), None, True),
                ("schema_version", sa.String(32), None, True),
                ("producer_version", sa.String(64), None, True),
                ("raw_locator", sa.String(512), None, True),
                ("late_after_cancel", sa.Boolean(), "0", False),
                ("stale_for_current_revision", sa.Boolean(), "0", False),
            )
            for name, typ, server_default, nullable in additions:
                if name not in cols:
                    kw = {"nullable": nullable}
                    if server_default is not None:
                        kw["server_default"] = server_default
                    batch.add_column(sa.Column(name, typ, **kw))
        for name in ("investigation_run_id", "execution_unit_id"):
            try:
                op.create_index(f"ix_case_evidence_{name}", "case_evidence", [name])
            except Exception:
                pass

    if "agent_runtime_bindings" in tables:
        cols = {c["name"] for c in inspector.get_columns("agent_runtime_bindings")}
        if "deployment_epoch" not in cols:
            with op.batch_alter_table("agent_runtime_bindings") as batch:
                batch.add_column(sa.Column("deployment_epoch", sa.Integer(), nullable=False, server_default="1"))

    if "agent_runtime_turns" in tables:
        cols = {c["name"] for c in inspector.get_columns("agent_runtime_turns")}
        with op.batch_alter_table("agent_runtime_turns") as batch:
            for name, length in (("disposition", 40), ("side_effect_policy", 24), ("actor_id", 128), ("client_command_id", 128)):
                if name not in cols:
                    batch.add_column(sa.Column(name, sa.String(length), nullable=True))
            if "completed_at" not in cols:
                batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    if "agent_runtime_events" in tables:
        cols = {c["name"] for c in inspector.get_columns("agent_runtime_events")}
        with op.batch_alter_table("agent_runtime_events") as batch:
            for name in ("cycle_id", "model_request_id"):
                if name not in cols:
                    batch.add_column(sa.Column(name, sa.String(128), nullable=True))
            if "evaluation_run_id" not in cols:
                batch.add_column(sa.Column("evaluation_run_id", sa.String(128), nullable=True))
        for name in ("cycle_id", "model_request_id"):
            try:
                op.create_index(f"ix_agent_runtime_events_{name}", "agent_runtime_events", [name])
            except Exception:
                pass

    if "case_events" in tables:
        cols = {c["name"] for c in inspector.get_columns("case_events")}
        if "case_event_seq" not in cols:
            with op.batch_alter_table("case_events") as batch:
                batch.add_column(sa.Column("case_event_seq", sa.Integer(), nullable=True))
        try:
            op.create_index("ix_case_events_case_event_seq", "case_events", ["case_event_seq"])
        except Exception:
            pass


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    _add_existing_table_columns(bind)
    missing = [table for table in V6_TABLES if table not in existing]
    if missing:
        tables = {table: Base.metadata.tables[table] for table in missing}
        Base.metadata.create_all(bind=bind, tables=tables.values())


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for table in reversed(V6_TABLES):
        if table in existing:
            op.drop_table(table)
