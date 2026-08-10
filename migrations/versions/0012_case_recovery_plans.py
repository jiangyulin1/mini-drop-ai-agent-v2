"""Add durable Case recovery plans.

Revision ID: 0012_case_recovery_plans
Revises: 0011_change_registration
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_case_recovery_plans"
down_revision: Union[str, Sequence[str], None] = "0011_change_registration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "case_recovery_plans" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "case_recovery_plans",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("diagnosis_session_id", sa.String(length=128), nullable=True),
        sa.Column("action_id", sa.String(length=128), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=True),
        sa.Column("value_after_fix", sa.Text(), nullable=True),
        sa.Column("verification_method", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=True),
        sa.Column("dry_run_attempt_id", sa.String(length=128), nullable=True),
        sa.Column("dry_run_json", sa.JSON(), nullable=True),
        sa.Column("execution_json", sa.JSON(), nullable=True),
        sa.Column("verification_json", sa.JSON(), nullable=True),
        sa.Column("rollback_json", sa.JSON(), nullable=True),
        sa.Column("requires_approval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["incident_cases.id", "incident_cases.tenant_id"],
            name="fk_recovery_plan_case_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("dry_run_attempt_id", name="uq_case_recovery_plans_dry_run_attempt_id"),
    )
    op.create_index("ix_case_recovery_plans_diagnosis_session_id", "case_recovery_plans", ["diagnosis_session_id"])
    op.create_index("ix_case_recovery_plans_action_id", "case_recovery_plans", ["action_id"])
    op.create_index("ix_case_recovery_plans_status", "case_recovery_plans", ["status"])
    op.create_index(
        "ix_recovery_plan_case_status", "case_recovery_plans", ["case_id", "tenant_id", "status"],
    )


def downgrade() -> None:
    if "case_recovery_plans" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("case_recovery_plans")
