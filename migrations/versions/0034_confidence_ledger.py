"""Add versioned explainable confidence ledgers and operator adjustments.

Revision ID: 0034_confidence_ledger
Revises: 0033_evidence_dep
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_confidence_ledger"
down_revision: Union[str, Sequence[str], None] = "0033_evidence_dep"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "conclusion_revisions" in tables:
        existing = {item["name"] for item in inspector.get_columns("conclusion_revisions")}
        with op.batch_alter_table("conclusion_revisions") as batch:
            if "root_location_json" not in existing:
                batch.add_column(sa.Column("root_location_json", sa.JSON(), nullable=False, server_default="{}"))
            if "mechanism_json" not in existing:
                batch.add_column(sa.Column("mechanism_json", sa.JSON(), nullable=False, server_default="{}"))
            if "confidence_reason" not in existing:
                batch.add_column(sa.Column("confidence_reason", sa.Text(), nullable=False, server_default=""))
    if "confidence_chain_snapshots" not in tables:
        op.create_table(
            "confidence_chain_snapshots",
            sa.Column("snapshot_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("chain_type", sa.String(32), nullable=False),
            sa.Column("chain_id", sa.String(256), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
            sa.Column("computed_confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("operator_requested_confidence", sa.Float(), nullable=True),
            sa.Column("effective_confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confidence_cap", sa.Float(), nullable=False, server_default="1"),
            sa.Column("calculation_version", sa.String(64), nullable=False),
            sa.Column("confidence_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("invalidated_evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("remaining_active_support", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("ledger_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("operator_adjustment_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("case_id", "chain_type", "chain_id", "revision", name="uq_confidence_chain_snapshot"),
        )
        op.create_index("ix_confidence_chain_snapshots_case_id", "confidence_chain_snapshots", ["case_id"])
        op.create_index("ix_confidence_chain_snapshots_tenant_id", "confidence_chain_snapshots", ["tenant_id"])
        op.create_index("ix_confidence_chain_case_current", "confidence_chain_snapshots", ["case_id", "chain_type", "chain_id", "revision"])
    if "confidence_adjustments" not in tables:
        op.create_table(
            "confidence_adjustments",
            sa.Column("adjustment_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("chain_type", sa.String(32), nullable=False),
            sa.Column("chain_id", sa.String(256), nullable=False),
            sa.Column("revision_before", sa.Integer(), nullable=False),
            sa.Column("revision_after", sa.Integer(), nullable=False),
            sa.Column("confidence_before", sa.Float(), nullable=False),
            sa.Column("requested_confidence", sa.Float(), nullable=False),
            sa.Column("effective_confidence", sa.Float(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("calculation_version", sa.String(64), nullable=False),
            sa.Column("actor_id", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_confidence_adjustments_case_id", "confidence_adjustments", ["case_id"])
        op.create_index("ix_confidence_adjustments_tenant_id", "confidence_adjustments", ["tenant_id"])
        op.create_index("ix_confidence_adjustments_chain", "confidence_adjustments", ["case_id", "chain_type", "chain_id", "created_at"])


def downgrade() -> None:
    with op.batch_alter_table("conclusion_revisions") as batch:
        batch.drop_column("confidence_reason")
        batch.drop_column("mechanism_json")
        batch.drop_column("root_location_json")
    op.drop_index("ix_confidence_adjustments_chain", table_name="confidence_adjustments")
    op.drop_index("ix_confidence_adjustments_tenant_id", table_name="confidence_adjustments")
    op.drop_index("ix_confidence_adjustments_case_id", table_name="confidence_adjustments")
    op.drop_table("confidence_adjustments")
    op.drop_index("ix_confidence_chain_case_current", table_name="confidence_chain_snapshots")
    op.drop_index("ix_confidence_chain_snapshots_tenant_id", table_name="confidence_chain_snapshots")
    op.drop_index("ix_confidence_chain_snapshots_case_id", table_name="confidence_chain_snapshots")
    op.drop_table("confidence_chain_snapshots")
