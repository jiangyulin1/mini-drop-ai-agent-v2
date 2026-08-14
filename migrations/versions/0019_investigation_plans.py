"""Persist investigation plans, plan steps and evidence reviews.

E2 (plan section 5.4/5.5/10.2): gives the workbench real domain semantics —
users can delete, reorder, retarget and cancel planned steps, and the plan is
the authority the Supervisor schedules from.  Steps carry plan/scope revisions
so a stale, late tool call returns STALE_PLAN instead of creating a Task.

Revision ID: 0019_investigation_plans
Revises: 0018_case_resource_attachments
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_investigation_plans"
down_revision: Union[str, Sequence[str], None] = "0018_case_resource_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "investigation_plans" not in tables:
        op.create_table(
            "investigation_plans",
            sa.Column("plan_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("plan_revision", sa.Integer(), nullable=False, default=0),
            sa.Column("scope_revision", sa.Integer(), nullable=False, default=0),
            sa.Column("goal", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, default="ACTIVE"),
            sa.Column("source", sa.String(length=40), nullable=False, default="deterministic"),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("row_version", sa.Integer(), nullable=False, default=0),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "plan_revision", name="uq_plan_case_revision",
            ),
        )
        op.create_index(
            "ix_investigation_plans_case_id", "investigation_plans", ["case_id"],
        )
    if "investigation_plan_steps" not in tables:
        op.create_table(
            "investigation_plan_steps",
            sa.Column("step_id", sa.String(length=128), primary_key=True),
            sa.Column("plan_id", sa.String(length=128), nullable=False),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("plan_revision", sa.Integer(), nullable=False, default=0),
            sa.Column("scope_revision", sa.Integer(), nullable=False, default=0),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("collector_id", sa.String(length=128), nullable=True),
            sa.Column("target_refs_json", sa.JSON(), nullable=True),
            sa.Column("purpose", sa.String(length=500), nullable=True),
            sa.Column("hypothesis_refs_json", sa.JSON(), nullable=True),
            sa.Column("expected_information", sa.String(length=500), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, default=0),
            sa.Column("priority_source", sa.String(length=16), nullable=False, default="AI"),
            sa.Column("user_locked", sa.Boolean(), nullable=False, default=False),
            sa.Column("depends_on_json", sa.JSON(), nullable=True),
            sa.Column("risk", sa.String(length=24), nullable=False, default="READ_LOW"),
            sa.Column("status", sa.String(length=32), nullable=False, default="DRAFT"),
            sa.Column("task_ids_json", sa.JSON(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, default=1),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["plan_id"],
                ["investigation_plans.plan_id"],
                name="fk_plan_step_plan",
            ),
        )
        op.create_index(
            "ix_investigation_plan_steps_case_id", "investigation_plan_steps", ["case_id"],
        )
    if "evidence_reviews" not in tables:
        op.create_table(
            "evidence_reviews",
            sa.Column("review_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("evidence_id", sa.String(length=128), nullable=False),
            sa.Column("decision", sa.String(length=20), nullable=False),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("reason", sa.String(length=1000), nullable=True),
            sa.Column("actor_id", sa.String(length=128), nullable=False),
            sa.Column("review_revision", sa.Integer(), nullable=False, default=1),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "evidence_id", "review_revision",
                name="uq_evidence_review_revision",
            ),
        )
        op.create_index(
            "ix_evidence_reviews_case_id", "evidence_reviews", ["case_id"],
        )
    if "collection_decisions" not in tables:
        op.create_table(
            "collection_decisions",
            sa.Column("decision_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("requested_collector", sa.String(length=128), nullable=False),
            sa.Column("purpose", sa.String(length=500), nullable=True),
            sa.Column("result", sa.String(length=32), nullable=False),
            sa.Column("reused_task_ids_json", sa.JSON(), nullable=True),
            sa.Column("new_plan_step_ids_json", sa.JSON(), nullable=True),
            sa.Column("reason_codes_json", sa.JSON(), nullable=True),
            sa.Column("estimated_cost_json", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_collection_decisions_case_id", "collection_decisions", ["case_id"],
        )


def downgrade() -> None:
    op.drop_table("collection_decisions")
    op.drop_table("evidence_reviews")
    op.drop_table("investigation_plan_steps")
    op.drop_table("investigation_plans")
