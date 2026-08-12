"""Add global system controls (Red Button, capability key rotation epoch).

Revision ID: 0017_system_controls
Revises: 0016_case_supervisor
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_system_controls"
down_revision: Union[str, Sequence[str], None] = "0016_case_supervisor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "system_controls" not in tables:
        op.create_table(
            "system_controls",
            sa.Column("control_name", sa.String(length=64), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("value_json", sa.JSON(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("system_controls")
