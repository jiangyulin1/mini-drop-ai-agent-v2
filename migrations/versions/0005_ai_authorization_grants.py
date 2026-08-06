"""Add durable authorization grants for registered AI sources.

Revision ID: 0005_ai_authorization
Revises: 0004_task_trace_context
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_ai_authorization"
down_revision: Union[str, Sequence[str], None] = "0004_task_trace_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "authorization_grants" in inspector.get_table_names():
        return
    op.create_table(
        "authorization_grants",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_ids_json", sa.JSON(), nullable=True),
        sa.Column("operations_json", sa.JSON(), nullable=True),
        sa.Column("resource_scope_json", sa.JSON(), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=True),
        sa.Column("constraints_json", sa.JSON(), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uses_remaining", sa.Integer(), nullable=True),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_authorization_grants_principal_id", "authorization_grants", ["principal_id"])
    op.create_index("ix_authorization_grants_tenant_id", "authorization_grants", ["tenant_id"])
    op.create_index("ix_authorization_grants_case_id", "authorization_grants", ["case_id"])
    op.create_index("ix_authorization_grants_valid_until", "authorization_grants", ["valid_until"])
    op.create_index("ix_authorization_grants_status", "authorization_grants", ["status"])


def downgrade() -> None:
    op.drop_table("authorization_grants")
