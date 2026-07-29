"""Upgrade pre-Alembic installations to the release baseline.

Revision ID: 0002_release
Revises: 0001_baseline
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_release"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ADDITIVE_COLUMNS = {
    "tasks": [
        sa.Column("diagnosis_step_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    ],
    "artifacts": [
        sa.Column("sha256", sa.String(length=64), nullable=True),
    ],
    "diagnosis_sessions": [
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation_oracle_json", sa.JSON(), nullable=True),
    ],
    "diagnosis_probe_executions": [
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    ],
    "diagnosis_evidence": [
        sa.Column(
            "evidence_role",
            sa.String(length=32),
            nullable=False,
            server_default="incident",
        ),
    ],
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, columns in ADDITIVE_COLUMNS.items():
        if table not in tables:
            continue
        existing = {item["name"] for item in inspector.get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)

    inspector = sa.inspect(op.get_bind())
    if "tasks" in tables:
        indexes = {item["name"] for item in inspector.get_indexes("tasks")}
        if "ix_tasks_diagnosis_step_id" not in indexes:
            op.create_index(
                "ix_tasks_diagnosis_step_id",
                "tasks",
                ["diagnosis_step_id"],
                unique=True,
            )
        if "ix_tasks_idempotency_key" not in indexes:
            op.create_index(
                "ix_tasks_idempotency_key",
                "tasks",
                ["idempotency_key"],
                unique=True,
            )


def downgrade() -> None:
    # This compatibility migration may be applied to an unknown legacy schema.
    # Destructive automatic rollback could remove columns that predated Alembic.
    raise RuntimeError("0002_release is intentionally irreversible")
