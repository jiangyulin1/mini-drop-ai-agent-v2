"""Normalize Case hypotheses, edges and investigation iterations.

Revision ID: 0008_case_investigation
Revises: 0007_case_context_audit
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_case_investigation"
down_revision: Union[str, Sequence[str], None] = "0007_case_context_audit"
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
    if "case_hypothesis_nodes" not in tables:
        op.create_table(
            "case_hypothesis_nodes",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
            sa.Column("statement", sa.Text(), nullable=False),
            sa.Column("root_entity", sa.String(length=256), nullable=True),
            sa.Column("mechanism", sa.String(length=128), nullable=True),
            sa.Column("affected_entities_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("supporting_evidence_refs_json", sa.JSON(), nullable=True),
            sa.Column("contradicting_evidence_refs_json", sa.JSON(), nullable=True),
            sa.Column("missing_evidence_json", sa.JSON(), nullable=True),
            sa.Column("alternatives_json", sa.JSON(), nullable=True),
            sa.Column("score_components_json", sa.JSON(), nullable=True),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "hypothesis_id", name="uq_case_hypothesis",
            ),
            _case_fk("fk_hypothesis_case_tenant"),
        )
        op.create_index("ix_case_hypothesis_nodes_case_id", "case_hypothesis_nodes", ["case_id"])
        op.create_index("ix_case_hypothesis_nodes_tenant_id", "case_hypothesis_nodes", ["tenant_id"])
        op.create_index(
            "ix_case_hypothesis_nodes_hypothesis_id",
            "case_hypothesis_nodes",
            ["hypothesis_id"],
        )
        op.create_index("ix_case_hypothesis_nodes_status", "case_hypothesis_nodes", ["status"])

    if "case_hypothesis_edges" not in tables:
        op.create_table(
            "case_hypothesis_edges",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("source_hypothesis_id", sa.String(length=128), nullable=False),
            sa.Column("target_hypothesis_id", sa.String(length=128), nullable=False),
            sa.Column("relation", sa.String(length=32), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            _case_fk("fk_hypothesis_edge_case_tenant"),
        )
        op.create_index("ix_case_hypothesis_edges_case_id", "case_hypothesis_edges", ["case_id"])
        op.create_index("ix_case_hypothesis_edges_tenant_id", "case_hypothesis_edges", ["tenant_id"])

    if "case_investigation_iterations" not in tables:
        op.create_table(
            "case_investigation_iterations",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("iteration_no", sa.Integer(), nullable=False),
            sa.Column(
                "context_packet_id",
                sa.String(length=128),
                sa.ForeignKey("case_context_packets.id"),
                nullable=True,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("input_evidence_refs_json", sa.JSON(), nullable=True),
            sa.Column("hypothesis_changes_json", sa.JSON(), nullable=True),
            sa.Column("candidate_actions_json", sa.JSON(), nullable=True),
            sa.Column("selected_action_json", sa.JSON(), nullable=True),
            sa.Column("policy_decision_json", sa.JSON(), nullable=True),
            sa.Column("cost_json", sa.JSON(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("stop_decision_json", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "iteration_no", name="uq_case_iteration_no",
            ),
            _case_fk("fk_iteration_case_tenant"),
        )
        op.create_index(
            "ix_case_investigation_iterations_case_id",
            "case_investigation_iterations",
            ["case_id"],
        )
        op.create_index(
            "ix_case_investigation_iterations_tenant_id",
            "case_investigation_iterations",
            ["tenant_id"],
        )
        op.create_index(
            "ix_case_investigation_iterations_context_packet_id",
            "case_investigation_iterations",
            ["context_packet_id"],
        )
        op.create_index(
            "ix_case_investigation_iterations_status",
            "case_investigation_iterations",
            ["status"],
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "case_investigation_iterations" in tables:
        op.drop_table("case_investigation_iterations")
    if "case_hypothesis_edges" in tables:
        op.drop_table("case_hypothesis_edges")
    if "case_hypothesis_nodes" in tables:
        op.drop_table("case_hypothesis_nodes")
