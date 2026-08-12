"""Add queryable continuous profiling window indexes.

Revision ID: 0014_profile_windows
Revises: 0013_target_sessions
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_profile_windows"
down_revision: Union[str, Sequence[str], None] = "0013_target_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    signal_columns = {
        item["name"] for item in inspector.get_columns("target_signals")
    }
    if "profile_window_ids_json" not in signal_columns:
        with op.batch_alter_table("target_signals") as batch:
            batch.add_column(sa.Column("profile_window_ids_json", sa.JSON(), nullable=True))

    if "profile_windows" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "profile_windows",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("target_session_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("task_id", sa.String(length=128), nullable=False),
            sa.Column("agent_id", sa.String(length=128), nullable=False),
            sa.Column("target_pid", sa.Integer(), nullable=False),
            sa.Column("window_index", sa.Integer(), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("granularity", sa.String(length=24), nullable=False),
            sa.Column("artifact_refs_json", sa.JSON(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
            sa.ForeignKeyConstraint(
                ["target_session_id", "tenant_id"],
                ["diagnostic_target_sessions.id", "diagnostic_target_sessions.tenant_id"],
                name="fk_profile_window_session_tenant",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "target_session_id", "task_id", "window_index",
                name="uq_profile_window_target_task_index",
            ),
        )
        for column in (
            "target_session_id", "tenant_id", "task_id", "agent_id",
            "window_start", "window_end", "expires_at",
        ):
            op.create_index(f"ix_profile_windows_{column}", "profile_windows", [column])
        op.create_index(
            "ix_profile_window_target_time", "profile_windows",
            ["target_session_id", "tenant_id", "window_start", "window_end"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "profile_windows" in set(inspector.get_table_names()):
        op.drop_table("profile_windows")
    if "target_signals" in set(sa.inspect(op.get_bind()).get_table_names()):
        columns = {
            item["name"] for item in sa.inspect(op.get_bind()).get_columns("target_signals")
        }
        if "profile_window_ids_json" in columns:
            with op.batch_alter_table("target_signals") as batch:
                batch.drop_column("profile_window_ids_json")
