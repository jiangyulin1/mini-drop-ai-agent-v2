"""Persist W3C trace context for asynchronous task execution.

Revision ID: 0004_task_trace_context
Revises: 0003_drop_execution
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_task_trace_context"
down_revision: Union[str, Sequence[str], None] = "0003_drop_execution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "tasks" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("tasks")}
    if "traceparent" not in columns:
        op.add_column("tasks", sa.Column("traceparent", sa.String(length=64), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "tasks" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("tasks")}
    if "traceparent" in columns:
        op.drop_column("tasks", "traceparent")

