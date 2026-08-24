"""Scope durable reasoning state to an investigation branch.

Existing rows remain Case-wide (NULL branch_id). New branch runs write an
explicit branch_id, allowing both the legacy read path and the Evidence-native
branch workspace to coexist during the transition.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0037_branch_reasoning_scope"
down_revision = "0036_agent_collection_mode"
branch_labels = None
depends_on = None


def _add_branch(table: str) -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns(table)}
    if "branch_id" not in columns:
        op.add_column(table, sa.Column("branch_id", sa.String(128), nullable=True))
    indexes = {item["name"] for item in inspect(bind).get_indexes(table)}
    index_name = f"ix_{table}_branch_id"
    if index_name not in indexes:
        op.create_index(index_name, table, ["branch_id"])


def _replace_unique(table: str, name: str, columns: list[str]) -> None:
    existing = {item.get("name") for item in inspect(op.get_bind()).get_unique_constraints(table)}
    with op.batch_alter_table(table) as batch:
        if name in existing:
            batch.drop_constraint(name, type_="unique")
        batch.create_unique_constraint(name, columns)


def upgrade() -> None:
    _add_branch("case_hypothesis_nodes")
    _add_branch("case_hypothesis_edges")
    _add_branch("evidence_dependency_edges")
    _add_branch("causal_graph_revisions")
    _add_branch("evidence_gaps")
    _add_branch("conclusion_revisions")
    _add_branch("assistant_messages")
    with op.batch_alter_table("assistant_messages") as batch:
        batch.drop_constraint("uq_assistant_message", type_="unique")
        batch.create_unique_constraint("uq_assistant_message", ["case_id", "branch_id", "message_id"])

    # The branch column is nullable so legacy Case-scoped rows continue to
    # satisfy their old semantics.  Batch mode works for both SQLite and
    # PostgreSQL and lets us replace constraints without data loss.
    _replace_unique("case_hypothesis_nodes", "uq_case_hypothesis", ["case_id", "tenant_id", "branch_id", "hypothesis_id"])
    _replace_unique("evidence_dependency_edges", "uq_evidence_dependency_edge", ["case_id", "tenant_id", "source_kind", "source_id", "branch_id", "target_kind", "target_id", "relation"])
    _replace_unique("causal_graph_revisions", "uq_causal_graph_revision", ["case_id", "branch_id", "graph_revision"])
    _replace_unique("evidence_gaps", "uq_evidence_gap", ["case_id", "branch_id", "gap_id"])
    _replace_unique("conclusion_revisions", "uq_conclusion_revision", ["case_id", "branch_id", "revision"])


def downgrade() -> None:
    # Downgrade is intentionally conservative: nullable branch data is cleared
    # before restoring the legacy uniqueness constraints.
    for table in (
        "case_hypothesis_nodes", "case_hypothesis_edges", "evidence_dependency_edges", "causal_graph_revisions",
        "evidence_gaps", "conclusion_revisions",
    ):
        op.execute(sa.text(f"UPDATE {table} SET branch_id = NULL"))
    with op.batch_alter_table("case_hypothesis_nodes") as batch:
        batch.drop_constraint("uq_case_hypothesis", type_="unique")
        batch.create_unique_constraint("uq_case_hypothesis", ["case_id", "tenant_id", "hypothesis_id"])
    with op.batch_alter_table("evidence_dependency_edges") as batch:
        batch.drop_constraint("uq_evidence_dependency_edge", type_="unique")
        batch.create_unique_constraint(
            "uq_evidence_dependency_edge",
            ["case_id", "tenant_id", "source_kind", "source_id", "target_kind", "target_id", "relation"],
        )
    with op.batch_alter_table("causal_graph_revisions") as batch:
        batch.drop_constraint("uq_causal_graph_revision", type_="unique")
        batch.create_unique_constraint("uq_causal_graph_revision", ["case_id", "graph_revision"])
    with op.batch_alter_table("evidence_gaps") as batch:
        batch.drop_constraint("uq_evidence_gap", type_="unique")
        batch.create_unique_constraint("uq_evidence_gap", ["case_id", "gap_id"])
    with op.batch_alter_table("conclusion_revisions") as batch:
        batch.drop_constraint("uq_conclusion_revision", type_="unique")
        batch.create_unique_constraint("uq_conclusion_revision", ["case_id", "revision"])
    for table in (
        "case_hypothesis_nodes", "case_hypothesis_edges", "evidence_dependency_edges", "causal_graph_revisions",
        "evidence_gaps", "conclusion_revisions",
        "assistant_messages",
    ):
        op.drop_index(f"ix_{table}_branch_id", table_name=table)
        op.drop_column(table, "branch_id")
