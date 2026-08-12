"""Add long-lived diagnostic target sessions and normalized signals.

Revision ID: 0013_target_sessions
Revises: 0012_case_recovery_plans
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_target_sessions"
down_revision: Union[str, Sequence[str], None] = "0012_case_recovery_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "diagnostic_target_sessions" not in tables:
        op.create_table(
            "diagnostic_target_sessions",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("service_id", sa.String(length=128), nullable=False),
            sa.Column("environment", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=256), nullable=False),
            sa.Column("target_scope_json", sa.JSON(), nullable=True),
            sa.Column("baseline_json", sa.JSON(), nullable=True),
            sa.Column("signal_policy_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latest_signal_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("id", "tenant_id", name="uq_target_session_tenant"),
            sa.UniqueConstraint(
                "tenant_id", "environment", "service_id",
                name="uq_target_session_tenant_environment_service",
            ),
        )
        op.create_index("ix_diagnostic_target_sessions_tenant_id", "diagnostic_target_sessions", ["tenant_id"])
        op.create_index("ix_diagnostic_target_sessions_service_id", "diagnostic_target_sessions", ["service_id"])
        op.create_index("ix_diagnostic_target_sessions_environment", "diagnostic_target_sessions", ["environment"])
        op.create_index("ix_diagnostic_target_sessions_status", "diagnostic_target_sessions", ["status"])
    if "target_signals" not in tables:
        op.create_table(
            "target_signals",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("target_session_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("signal_type", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("dedupe_key", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("triggered_case_id", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["target_session_id", "tenant_id"],
                ["diagnostic_target_sessions.id", "diagnostic_target_sessions.tenant_id"],
                name="fk_target_signal_session_tenant",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("target_session_id", "dedupe_key", name="uq_target_signal_dedupe"),
        )
        for column in ("target_session_id", "tenant_id", "signal_type", "severity", "observed_at", "triggered_case_id"):
            op.create_index(f"ix_target_signals_{column}", "target_signals", [column])

    incident_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("incident_cases")}
    if "target_session_id" not in incident_columns:
        with op.batch_alter_table("incident_cases") as batch:
            batch.add_column(sa.Column("target_session_id", sa.String(length=128), nullable=True))
            batch.create_foreign_key(
                "fk_incident_case_target_session", "diagnostic_target_sessions",
                ["target_session_id"], ["id"],
            )
            batch.create_index("ix_incident_cases_target_session_id", ["target_session_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "incident_cases" in set(inspector.get_table_names()):
        columns = {item["name"] for item in inspector.get_columns("incident_cases")}
        if "target_session_id" in columns:
            with op.batch_alter_table("incident_cases") as batch:
                batch.drop_index("ix_incident_cases_target_session_id")
                batch.drop_constraint("fk_incident_case_target_session", type_="foreignkey")
                batch.drop_column("target_session_id")
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "target_signals" in tables:
        op.drop_table("target_signals")
    if "diagnostic_target_sessions" in tables:
        op.drop_table("diagnostic_target_sessions")
