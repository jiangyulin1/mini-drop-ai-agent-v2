"""Add canonical per-Case Evidence store (G3).

Revision ID: 0022_case_evidence
Revises: 0021_agent_runtime
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_case_evidence"
down_revision: Union[str, Sequence[str], None] = "0021_agent_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_evidence" not in tables:
        op.create_table(
            "case_evidence",
            sa.Column("evidence_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("attachment_id", sa.String(length=128), nullable=True),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("artifact_id", sa.Integer(), nullable=True),
            sa.Column("artifact_type", sa.String(length=32), nullable=True),
            sa.Column("collector_id", sa.String(length=64), nullable=True),
            sa.Column("source_type", sa.String(length=32), nullable=False, default="task_artifact"),
            sa.Column("target_ref", sa.String(length=256), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("projection_hash", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, default="ACTIVE"),
            sa.Column("quality", sa.String(length=20), nullable=False, default="UNKNOWN"),
            sa.Column("freshness", sa.String(length=20), nullable=False, default="UNKNOWN"),
            sa.Column("time_window_json", sa.JSON(), nullable=False, default=dict),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "evidence_id", name="uq_case_evidence",
            ),
        )
        op.create_index("ix_case_evidence_case_id", "case_evidence", ["case_id"])
        op.create_index("ix_case_evidence_tenant_id", "case_evidence", ["tenant_id"])
        op.create_index("ix_case_evidence_task_id", "case_evidence", ["task_id"])
        op.create_index("ix_case_evidence_status", "case_evidence", ["status"])


def downgrade() -> None:
    op.drop_table("case_evidence")
