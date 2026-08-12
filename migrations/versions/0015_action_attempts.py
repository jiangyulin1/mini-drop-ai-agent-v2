"""Persist durable registered action attempts (dry-run/execute/verify/rollback).

Revision ID: 0015_action_attempts
Revises: 0014_profile_windows
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_action_attempts"
down_revision: Union[str, Sequence[str], None] = "0014_profile_windows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "action_attempts" not in tables:
        op.create_table(
            "action_attempts",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("action_id", sa.String(length=128), nullable=False),
            sa.Column("operation_key", sa.String(length=256), nullable=False),
            sa.Column("phase", sa.String(length=32), nullable=False),
            sa.Column("parameters_json", sa.JSON(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "operation_key", "phase",
                name="uq_case_action_phase",
            ),
            sa.ForeignKeyConstraint(
                ["case_id", "tenant_id"],
                ["incident_cases.id", "incident_cases.tenant_id"],
                name="fk_action_attempt_case_tenant",
                ondelete="CASCADE",
            ),
        )
        op.create_index("ix_action_attempts_case_id", "action_attempts", ["case_id"])
        op.create_index("ix_action_attempts_tenant_id", "action_attempts", ["tenant_id"])
        op.create_index("ix_action_attempts_action_id", "action_attempts", ["action_id"])
        op.create_index(
            "ix_action_attempts_operation_key", "action_attempts", ["operation_key"],
        )


def downgrade() -> None:
    op.drop_index("ix_action_attempts_operation_key", table_name="action_attempts")
    op.drop_index("ix_action_attempts_action_id", table_name="action_attempts")
    op.drop_index("ix_action_attempts_tenant_id", table_name="action_attempts")
    op.drop_index("ix_action_attempts_case_id", table_name="action_attempts")
    op.drop_table("action_attempts")
