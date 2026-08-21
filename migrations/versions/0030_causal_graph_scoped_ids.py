"""Scope causal node and edge identifiers to one graph revision.

Revision ID: 0030_causal_graph_scoped_ids
Revises: 0029_wakeup_source_identity
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_causal_graph_scoped_ids"
down_revision: Union[str, Sequence[str], None] = "0029_wakeup_source_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NAMING_CONVENTION = {"pk": "pk_%(table_name)s"}


def _replace_primary_key(table_name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    primary_key = sa.inspect(bind).get_pk_constraint(table_name)
    constraint_name = primary_key.get("name") or f"pk_{table_name}"
    with op.batch_alter_table(
        table_name,
        recreate="always",
        naming_convention=_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(constraint_name, type_="primary")
        batch_op.create_primary_key(f"pk_{table_name}", columns)


def upgrade() -> None:
    _replace_primary_key("causal_nodes", ["graph_id", "node_id"])
    _replace_primary_key("causal_edges", ["graph_id", "edge_id"])


def downgrade() -> None:
    _replace_primary_key("causal_edges", ["edge_id"])
    _replace_primary_key("causal_nodes", ["node_id"])
