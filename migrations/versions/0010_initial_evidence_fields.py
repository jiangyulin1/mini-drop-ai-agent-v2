"""Add initial evidence fields on diagnosis sessions.

Revision ID: 0010_initial_evidence_fields
Revises: 0009_initial_task_evidence
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_initial_evidence_fields"
down_revision: Union[str, Sequence[str], None] = "0009_initial_task_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("diagnosis_sessions")}
    if "initial_evidence_loaded_json" not in columns:
        op.add_column(
            "diagnosis_sessions",
            sa.Column("initial_evidence_loaded_json", sa.JSON(), nullable=True),
        )
    if "initial_evidence_count" not in columns:
        op.add_column(
            "diagnosis_sessions",
            sa.Column("initial_evidence_count", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("diagnosis_sessions")}
    if "initial_evidence_count" in columns:
        op.drop_column("diagnosis_sessions", "initial_evidence_count")
    if "initial_evidence_loaded_json" in columns:
        op.drop_column("diagnosis_sessions", "initial_evidence_loaded_json")
