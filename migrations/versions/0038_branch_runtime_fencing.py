"""Add branch-local runtime bindings and lineage columns."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0038_branch_runtime_fencing"
down_revision = "0037_branch_reasoning_scope"
branch_labels = None
depends_on = None


def _add_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if column.name not in {item["name"] for item in inspect(bind).get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    _add_column("agent_runtime_turns", sa.Column("branch_id", sa.String(128), nullable=True))
    _add_column("agent_cycles", sa.Column("branch_id", sa.String(128), nullable=True))
    _add_column("model_requests", sa.Column("branch_id", sa.String(128), nullable=True))
    bind = op.get_bind()
    for table in ("agent_runtime_turns", "agent_cycles", "model_requests"):
        names = {item["name"] for item in inspect(bind).get_indexes(table)}
        name = f"ix_{table}_branch_id"
        if name not in names:
            op.create_index(name, table, ["branch_id"])
    op.create_table(
        "agent_runtime_branch_bindings",
        sa.Column("binding_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
        sa.Column("branch_id", sa.String(128), nullable=False, index=True),
        sa.Column("runtime_type", sa.String(32), nullable=False),
        sa.Column("runtime_version", sa.String(64), nullable=False),
        sa.Column("runtime_session_id", sa.String(128), nullable=False),
        sa.Column("runtime_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
        sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_context_snapshot_id", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "tenant_id", "branch_id", name="uq_agent_runtime_binding_branch"),
    )
    op.create_index("ix_agent_runtime_branch_bindings_case", "agent_runtime_branch_bindings", ["case_id", "tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runtime_branch_bindings_case", table_name="agent_runtime_branch_bindings")
    op.drop_table("agent_runtime_branch_bindings")
    for table in ("agent_runtime_turns", "agent_cycles", "model_requests"):
        op.drop_index(f"ix_{table}_branch_id", table_name=table)
        op.drop_column(table, "branch_id")
