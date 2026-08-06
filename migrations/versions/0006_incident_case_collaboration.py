"""Add the tenant-scoped Incident Case collaboration layer.

Revision ID: 0006_incident_cases
Revises: 0005_ai_authorization
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_incident_cases"
down_revision: Union[str, Sequence[str], None] = "0005_ai_authorization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "diagnosis_sessions" in tables:
        diagnosis_columns = {
            item["name"] for item in inspector.get_columns("diagnosis_sessions")
        }
        if "paused_from_status" not in diagnosis_columns:
            op.add_column(
                "diagnosis_sessions",
                sa.Column("paused_from_status", sa.String(length=32), nullable=True),
            )
    if "incident_cases" not in tables:
        op.create_table(
            "incident_cases",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column(
                "diagnosis_session_id",
                sa.String(length=128),
                sa.ForeignKey("diagnosis_sessions.id"),
                nullable=True,
            ),
            sa.Column(
                "source_task_id",
                sa.String(length=128),
                sa.ForeignKey("tasks.id"),
                nullable=True,
            ),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("problem_description", sa.Text(), nullable=False),
            sa.Column("recovery_goal", sa.Text(), nullable=False),
            sa.Column("run_mode", sa.String(length=32), nullable=False),
            sa.Column("environment", sa.String(length=64), nullable=False),
            sa.Column("target_scope_json", sa.JSON(), nullable=True),
            sa.Column("time_range_json", sa.JSON(), nullable=True),
            sa.Column("state", sa.String(length=40), nullable=False),
            sa.Column("state_reason", sa.String(length=128), nullable=False),
            sa.Column("impact_json", sa.JSON(), nullable=True),
            sa.Column("current_finding_json", sa.JSON(), nullable=True),
            sa.Column("current_activity_json", sa.JSON(), nullable=True),
            sa.Column("need_user_json", sa.JSON(), nullable=True),
            sa.Column("recovery_json", sa.JSON(), nullable=True),
            sa.Column("scope_revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("id", "tenant_id", name="uq_incident_case_tenant"),
        )
        op.create_index("ix_incident_cases_tenant_id", "incident_cases", ["tenant_id"])
        op.create_index("ix_incident_cases_created_by", "incident_cases", ["created_by"])
        op.create_index(
            "ix_incident_cases_diagnosis_session_id",
            "incident_cases",
            ["diagnosis_session_id"],
        )
        op.create_index("ix_incident_cases_source_task_id", "incident_cases", ["source_task_id"])
        op.create_index("ix_incident_cases_state", "incident_cases", ["state"])

    inspector = sa.inspect(op.get_bind())
    if "case_events" not in inspector.get_table_names():
        op.create_table(
            "case_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("actor_id", sa.String(length=128), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["case_id", "tenant_id"],
                ["incident_cases.id", "incident_cases.tenant_id"],
                name="fk_case_event_case_tenant",
                ondelete="CASCADE",
            ),
        )
        op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
        op.create_index("ix_case_events_tenant_id", "case_events", ["tenant_id"])
        op.create_index("ix_case_events_event_type", "case_events", ["event_type"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_events" in tables:
        op.drop_table("case_events")
    if "incident_cases" in tables:
        op.drop_table("incident_cases")
    if "diagnosis_sessions" in tables:
        columns = {
            item["name"] for item in sa.inspect(op.get_bind()).get_columns("diagnosis_sessions")
        }
        if "paused_from_status" in columns:
            op.drop_column("diagnosis_sessions", "paused_from_status")
