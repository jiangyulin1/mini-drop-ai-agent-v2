"""Add data-driven entry: initial_task_ids on incident cases.

Revision ID: 0009_initial_task_evidence
Revises: 0008_case_investigation
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_initial_task_evidence"
down_revision: Union[str, Sequence[str], None] = "0008_case_investigation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("incident_cases")}
    if "initial_task_ids" not in columns:
        op.add_column(
            "incident_cases",
            sa.Column("initial_task_ids", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("incident_cases")}
    if "initial_task_ids" in columns:
        op.drop_column("incident_cases", "initial_task_ids")
