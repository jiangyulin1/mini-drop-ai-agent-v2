"""Split Evidence lifecycle/trust state and persist governance impact.

Revision ID: 0032_evidence_governance
Revises: 0031_case_memory_knowledge
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_evidence_governance"
down_revision: Union[str, Sequence[str], None] = "0031_case_memory_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "case_evidence" in tables:
        columns = {item["name"] for item in inspector.get_columns("case_evidence")}
        additions = (
            sa.Column("lifecycle_status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
            sa.Column("review_trust_state", sa.String(length=24), nullable=False, server_default="UNREVIEWED"),
            sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("derived_trust_score", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("ui_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ui_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        with op.batch_alter_table("case_evidence") as batch:
            for column in additions:
                if column.name not in columns:
                    batch.add_column(column)
        op.execute(
            "UPDATE case_evidence SET "
            "lifecycle_status = CASE WHEN status IN ('EXCLUDED','INVALID','SUPERSEDED') THEN status ELSE 'ACTIVE' END, "
            "review_trust_state = CASE WHEN status = 'LOW_TRUST' THEN 'LOW_TRUST' ELSE 'UNREVIEWED' END"
        )
        for name, fields in (
            ("ix_case_evidence_lifecycle_status", ["lifecycle_status"]),
            ("ix_case_evidence_review_trust_state", ["review_trust_state"]),
        ):
            indexes = {item["name"] for item in inspector.get_indexes("case_evidence")}
            if name not in indexes:
                op.create_index(name, "case_evidence", fields)

    if "evidence_review_revisions" in tables:
        columns = {item["name"] for item in inspector.get_columns("evidence_review_revisions")}
        additions = (
            sa.Column("lifecycle_status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
            sa.Column("trust_state", sa.String(length=24), nullable=False, server_default="UNREVIEWED"),
            sa.Column("derived_trust_score", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("projection_hash", sa.String(length=64), nullable=True),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("assessment_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("recommendation_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("impact_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("overridden_recommendation", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        with op.batch_alter_table("evidence_review_revisions") as batch:
            for column in additions:
                if column.name not in columns:
                    batch.add_column(column)

    if "case_recovery_plans" in tables:
        columns = {item["name"] for item in inspector.get_columns("case_recovery_plans")}
        with op.batch_alter_table("case_recovery_plans") as batch:
            if "evidence_refs_json" not in columns:
                batch.add_column(sa.Column("evidence_refs_json", sa.JSON(), nullable=False, server_default="[]"))
            if "evidence_hold_json" not in columns:
                batch.add_column(sa.Column("evidence_hold_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("case_recovery_plans") as batch:
        batch.drop_column("evidence_hold_json")
        batch.drop_column("evidence_refs_json")
    with op.batch_alter_table("evidence_review_revisions") as batch:
        for name in (
            "overridden_recommendation", "impact_json", "recommendation_json",
            "assessment_json", "reason_code", "projection_hash",
            "derived_trust_score", "trust_state", "lifecycle_status",
        ):
            batch.drop_column(name)
    op.drop_index("ix_case_evidence_review_trust_state", table_name="case_evidence")
    op.drop_index("ix_case_evidence_lifecycle_status", table_name="case_evidence")
    with op.batch_alter_table("case_evidence") as batch:
        for name in (
            "ui_archived", "ui_hidden", "derived_trust_score", "review_revision",
            "review_trust_state", "lifecycle_status",
        ):
            batch.drop_column(name)
