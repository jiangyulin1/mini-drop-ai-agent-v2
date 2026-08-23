"""Persist Evidence dependency edges and lifecycle invalidation projections.

Revision ID: 0033_evidence_dependency_propagation
Revises: 0032_evidence_governance
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_evidence_dep"
down_revision: Union[str, Sequence[str], None] = "0032_evidence_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "evidence_dependency_edges" not in tables:
        op.create_table(
            "evidence_dependency_edges",
            sa.Column("dependency_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("source_kind", sa.String(24), nullable=False, server_default="EVIDENCE"),
            sa.Column("source_id", sa.String(256), nullable=False),
            sa.Column("target_kind", sa.String(24), nullable=False),
            sa.Column("target_id", sa.String(256), nullable=False),
            sa.Column("relation", sa.String(32), nullable=False, server_default="SUPPORTS"),
            sa.Column("support_weight", sa.Float(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
            sa.Column("invalidated_by_evidence_id", sa.String(128), nullable=True),
            sa.Column("invalidated_review_revision", sa.Integer(), nullable=True),
            sa.Column("invalidated_reason", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "source_kind", "source_id",
                "target_kind", "target_id", "relation",
                name="uq_evidence_dependency_edge",
            ),
        )
        op.create_index(
            "ix_evidence_dependency_source", "evidence_dependency_edges",
            ["case_id", "tenant_id", "source_id"],
        )
        op.create_index(
            "ix_evidence_dependency_target", "evidence_dependency_edges",
            ["case_id", "tenant_id", "target_kind", "target_id"],
        )
        op.create_index("ix_evidence_dependency_edges_case_id", "evidence_dependency_edges", ["case_id"])
        op.create_index("ix_evidence_dependency_edges_tenant_id", "evidence_dependency_edges", ["tenant_id"])
        op.create_index("ix_evidence_dependency_edges_status", "evidence_dependency_edges", ["status"])

    additions = {
        "case_hypothesis_nodes": {
            "invalidated_evidence_refs_json": sa.Column("invalidated_evidence_refs_json", sa.JSON(), nullable=True, server_default="[]"),
            "remaining_active_support_json": sa.Column("remaining_active_support_json", sa.JSON(), nullable=True, server_default="[]"),
        },
        "causal_edges": {
            "dependency_status": sa.Column("dependency_status", sa.String(24), nullable=False, server_default="ACTIVE"),
            "invalidated_evidence_refs": sa.Column("invalidated_evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            "remaining_active_support": sa.Column("remaining_active_support", sa.JSON(), nullable=False, server_default="[]"),
        },
        "claim_evidence_bindings": {
            "claim_status": sa.Column("claim_status", sa.String(24), nullable=False, server_default="ACTIVE"),
            "invalidated_evidence_refs": sa.Column("invalidated_evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            "remaining_active_support": sa.Column("remaining_active_support", sa.JSON(), nullable=False, server_default="[]"),
        },
        "conclusion_revisions": {
            "invalidated_claims": sa.Column("invalidated_claims", sa.JSON(), nullable=False, server_default="[]"),
            "remaining_active_support": sa.Column("remaining_active_support", sa.JSON(), nullable=False, server_default="{}"),
        },
    }
    for table, columns in additions.items():
        if table not in tables:
            continue
        existing = {item["name"] for item in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for name, column in columns.items():
                if name not in existing:
                    batch.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, columns in (
        ("conclusion_revisions", ("remaining_active_support", "invalidated_claims")),
        ("claim_evidence_bindings", ("remaining_active_support", "invalidated_evidence_refs", "claim_status")),
        ("causal_edges", ("remaining_active_support", "invalidated_evidence_refs", "dependency_status")),
        ("case_hypothesis_nodes", ("remaining_active_support_json", "invalidated_evidence_refs_json")),
    ):
        if table not in tables:
            continue
        existing = {item["name"] for item in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for name in columns:
                if name in existing:
                    batch.drop_column(name)
    if "evidence_dependency_edges" in tables:
        op.drop_index("ix_evidence_dependency_edges_status", table_name="evidence_dependency_edges")
        op.drop_index("ix_evidence_dependency_edges_tenant_id", table_name="evidence_dependency_edges")
        op.drop_index("ix_evidence_dependency_edges_case_id", table_name="evidence_dependency_edges")
        op.drop_index("ix_evidence_dependency_target", table_name="evidence_dependency_edges")
        op.drop_index("ix_evidence_dependency_source", table_name="evidence_dependency_edges")
        op.drop_table("evidence_dependency_edges")
