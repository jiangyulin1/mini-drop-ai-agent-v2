"""Add Case Supervisor runtime leases and queued commands.

Revision ID: 0016_case_supervisor
Revises: 0015_action_attempts
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_case_supervisor"
down_revision: Union[str, Sequence[str], None] = "0015_action_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _case_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["case_id", "tenant_id"],
        ["incident_cases.id", "incident_cases.tenant_id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_runtime_leases" not in tables:
        op.create_table(
            "case_runtime_leases",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("owner", sa.String(length=128), nullable=False),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", name="uq_case_runtime_lease",
            ),
            _case_fk("fk_lease_case_tenant"),
        )
        op.create_index("ix_case_runtime_leases_case_id", "case_runtime_leases", ["case_id"])
        op.create_index("ix_case_runtime_leases_tenant_id", "case_runtime_leases", ["tenant_id"])
        op.create_index("ix_case_runtime_leases_lease_until", "case_runtime_leases", ["lease_until"])

    if "case_commands" not in tables:
        op.create_table(
            "case_commands",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("command_type", sa.String(length=32), nullable=False),
            sa.Column("idempotency_key", sa.String(length=256), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "idempotency_key", name="uq_case_command_idem",
            ),
            _case_fk("fk_command_case_tenant"),
        )
        op.create_index("ix_case_commands_case_id", "case_commands", ["case_id"])
        op.create_index(
            "ix_case_commands_status", "case_commands", ["status"],
        )


def downgrade() -> None:
    op.drop_table("case_commands")
    op.drop_table("case_runtime_leases")
