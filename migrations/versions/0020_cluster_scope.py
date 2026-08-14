"""Persist membership snapshots and fanout collection runs.

E3.5 (plan section 3.5/3.6/8.13): Mini-Drop is the only freezer of cluster
membership.  A logical collection step fans out to many single-target Tasks;
the snapshot is immutable once captured and the run carries plan/scope
revisions so late results cannot pollute a newer member set.

Revision ID: 0020_cluster_scope
Revises: 0019_investigation_plans
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_cluster_scope"
down_revision: Union[str, Sequence[str], None] = "0019_investigation_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    # E3.5: 集群 Step 声明选择策略（不重建旧表，直接加列）
    step_cols = {column["name"] for column in inspector.get_columns("investigation_plan_steps")}
    if "selection_strategy" not in step_cols:
        op.add_column(
            "investigation_plan_steps",
            sa.Column("selection_strategy", sa.String(length=40), nullable=True),
        )
    if "membership_snapshots" not in tables:
        op.create_table(
            "membership_snapshots",
            sa.Column("snapshot_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("environment_id", sa.String(length=128), nullable=False, default=""),
            sa.Column("cluster_id", sa.String(length=128), nullable=False, default=""),
            sa.Column("topology_version", sa.String(length=64), nullable=False, default=""),
            sa.Column("scope_revision", sa.Integer(), nullable=False, default=1),
            sa.Column("members_json", sa.JSON(), nullable=False, default=list),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "snapshot_id", name="uq_membership_snapshot",
            ),
        )
        op.create_index(
            "ix_membership_snapshots_case_id", "membership_snapshots", ["case_id"],
        )
    if "fanout_collection_runs" not in tables:
        op.create_table(
            "fanout_collection_runs",
            sa.Column("run_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("plan_step_id", sa.String(length=128), nullable=False, default=""),
            sa.Column("plan_revision", sa.Integer(), nullable=False, default=0),
            sa.Column("scope_revision", sa.Integer(), nullable=False, default=1),
            sa.Column("snapshot_id", sa.String(length=128), nullable=False, default=""),
            sa.Column("strategy", sa.String(length=40), nullable=False, default="ALL_IN_SCOPE"),
            sa.Column("collector_id", sa.String(length=128), nullable=False, default="sys_metrics"),
            sa.Column("target_members_json", sa.JSON(), nullable=False, default=list),
            sa.Column("task_ids_json", sa.JSON(), nullable=False, default=list),
            sa.Column("member_task_map_json", sa.JSON(), nullable=False, default=dict),
            sa.Column("task_statuses_json", sa.JSON(), nullable=False, default=dict),
            sa.Column("status", sa.String(length=24), nullable=False, default="RUNNING"),
            sa.Column("coverage", sa.Float(), nullable=False, default=0.0),
            sa.Column("failed_count", sa.Integer(), nullable=False, default=0),
            sa.Column("quorum_met", sa.Boolean(), nullable=False, default=False),
            sa.Column("aggregate_json", sa.JSON(), nullable=False, default=dict),
            sa.Column("late_result_isolated_json", sa.JSON(), nullable=False, default=list),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "run_id", name="uq_fanout_run",
            ),
        )
        op.create_index(
            "ix_fanout_collection_runs_case_id", "fanout_collection_runs", ["case_id"],
        )


def downgrade() -> None:
    op.drop_table("fanout_collection_runs")
    op.drop_table("membership_snapshots")
