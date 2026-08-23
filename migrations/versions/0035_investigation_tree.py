"""Add the durable investigation-tree audit projection.

The tree is deliberately an audit/index layer around the existing AgentCycle,
Evidence and revision records.  It does not replace Evidence authority or
grant cross-branch visibility.

Revision ID: 0035_investigation_tree
Revises: 0034_confidence_ledger
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_investigation_tree"
down_revision: Union[str, Sequence[str], None] = "0034_confidence_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    def create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
        indexes = {item.get("name") for item in sa.inspect(bind).get_indexes(table)}
        if name not in indexes:
            op.create_index(name, table, columns)

    if "investigation_tree_nodes" not in tables:
        op.create_table(
            "investigation_tree_nodes",
            sa.Column("node_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("run_id", sa.String(128), nullable=False),
            sa.Column("parent_node_id", sa.String(128), nullable=True),
            sa.Column("branch_id", sa.String(128), nullable=False),
            sa.Column("node_type", sa.String(32), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
            sa.Column("statement", sa.Text(), nullable=False, server_default=""),
            sa.Column("hypothesis_id", sa.String(128), nullable=True),
            sa.Column("obligation_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("evidence_refs_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("invalidated_evidence_refs_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("replay_of_node_id", sa.String(128), nullable=True),
            sa.Column("invalidated_reason", sa.String(128), nullable=True),
            sa.Column("created_by", sa.String(128), nullable=False, server_default="agent"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("case_id", "tenant_id", "node_id", name="uq_investigation_tree_node"),
        )
        op.create_index(
            "ix_investigation_tree_nodes_case_id", "investigation_tree_nodes", ["case_id"]
        )
        op.create_index(
            "ix_investigation_tree_nodes_tenant_id", "investigation_tree_nodes", ["tenant_id"]
        )
        op.create_index(
            "ix_investigation_tree_nodes_run_status", "investigation_tree_nodes",
            ["case_id", "tenant_id", "run_id", "status"],
        )
        op.create_index(
            "ix_investigation_tree_nodes_parent", "investigation_tree_nodes",
            ["case_id", "tenant_id", "parent_node_id"],
        )

    # Keep ORM metadata and the migration graph aligned.  These indexes are
    # additive and the conditional helper also handles databases that already
    # received them from the compatibility startup path.
    if "case_evidence" in tables:
        create_index_if_missing(
            "ix_case_evidence_case_tenant_task", "case_evidence",
            ["case_id", "tenant_id", "task_id"],
        )
        create_index_if_missing(
            "ix_case_evidence_case_tenant_status_created", "case_evidence",
            ["case_id", "tenant_id", "status", "created_at"],
        )
    if "collection_requests" in tables:
        create_index_if_missing(
            "ix_collection_requests_case_tenant_status_created", "collection_requests",
            ["case_id", "tenant_id", "status", "created_at"],
        )

    if "investigation_tree_dependencies" not in tables:
        op.create_table(
            "investigation_tree_dependencies",
            sa.Column("dependency_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("node_id", sa.String(128), nullable=False),
            sa.Column("target_kind", sa.String(32), nullable=False),
            sa.Column("target_id", sa.String(256), nullable=False),
            sa.Column("relation", sa.String(32), nullable=False, server_default="REQUIRES"),
            sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
            sa.Column("invalidated_reason", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "node_id", "target_kind", "target_id", "relation",
                name="uq_investigation_tree_dependency",
            ),
        )
        op.create_index(
            "ix_investigation_tree_dependencies_case_id", "investigation_tree_dependencies", ["case_id"]
        )
        op.create_index(
            "ix_investigation_tree_dependencies_tenant_id", "investigation_tree_dependencies", ["tenant_id"]
        )
        op.create_index(
            "ix_investigation_tree_dependencies_node_id", "investigation_tree_dependencies", ["node_id"]
        )
        op.create_index(
            "ix_investigation_tree_dependencies_target", "investigation_tree_dependencies",
            ["case_id", "tenant_id", "target_kind", "target_id"],
        )

    if "investigation_tree_events" not in tables:
        op.create_table(
            "investigation_tree_events",
            sa.Column("event_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("run_id", sa.String(128), nullable=False),
            sa.Column("node_id", sa.String(128), nullable=False),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("from_status", sa.String(32), nullable=True),
            sa.Column("to_status", sa.String(32), nullable=False),
            sa.Column("reason", sa.String(256), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("actor_id", sa.String(128), nullable=False, server_default="agent"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_investigation_tree_events_case_id", "investigation_tree_events", ["case_id"]
        )
        op.create_index(
            "ix_investigation_tree_events_tenant_id", "investigation_tree_events", ["tenant_id"]
        )
        op.create_index(
            "ix_investigation_tree_events_run_created", "investigation_tree_events",
            ["case_id", "tenant_id", "run_id", "created_at"],
        )
        op.create_index(
            "ix_investigation_tree_events_node_created", "investigation_tree_events",
            ["node_id", "created_at"],
        )

    if "investigation_tree_nodes" in tables | {"investigation_tree_nodes"}:
        create_index_if_missing("ix_investigation_tree_nodes_run_id", "investigation_tree_nodes", ["run_id"])
        create_index_if_missing("ix_investigation_tree_nodes_parent_node_id", "investigation_tree_nodes", ["parent_node_id"])
        create_index_if_missing("ix_investigation_tree_nodes_branch_id", "investigation_tree_nodes", ["branch_id"])
        create_index_if_missing("ix_investigation_tree_nodes_hypothesis_id", "investigation_tree_nodes", ["hypothesis_id"])
        create_index_if_missing("ix_investigation_tree_nodes_status", "investigation_tree_nodes", ["status"])
    if "investigation_tree_dependencies" in tables | {"investigation_tree_dependencies"}:
        create_index_if_missing("ix_investigation_tree_dependencies_status", "investigation_tree_dependencies", ["status"])
    if "investigation_tree_events" in tables | {"investigation_tree_events"}:
        create_index_if_missing("ix_investigation_tree_events_node_id", "investigation_tree_events", ["node_id"])
        create_index_if_missing("ix_investigation_tree_events_run_id", "investigation_tree_events", ["run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table in (
        "investigation_tree_events",
        "investigation_tree_dependencies",
        "investigation_tree_nodes",
    ):
        if table in tables:
            op.drop_table(table)
