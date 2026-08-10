"""Add service change registration for change-correlation.

Revision ID: 0011_change_registration
Revises: 0010_initial_evidence_fields
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_change_registration"
down_revision: Union[str, Sequence[str], None] = "0010_initial_evidence_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "service_changes" not in tables:
        op.create_table(
            "service_changes",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("service_id", sa.String(length=128), nullable=False),
            sa.Column("environment", sa.String(length=64), nullable=False),
            sa.Column("change_type", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("id", "tenant_id", name="uq_service_change_tenant"),
        )
        op.create_index(
            "ix_service_changes_tenant_service",
            "service_changes", ["tenant_id", "service_id", "changed_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "service_changes" in tables:
        op.drop_index("ix_service_changes_tenant_service", table_name="service_changes")
        op.drop_table("service_changes")
