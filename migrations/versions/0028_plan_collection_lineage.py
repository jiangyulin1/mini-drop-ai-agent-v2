"""Link Plan steps to collection proposals and requests.

Revision ID: 0028_plan_collection_lineage
Revises: 0027_ai_collector_contracts
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_plan_collection_lineage"
down_revision: Union[str, Sequence[str], None] = "0027_ai_collector_contracts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("collection_proposals", sa.Column("plan_step_id", sa.String(128), nullable=True))
    op.add_column("collection_proposals", sa.Column("plan_revision", sa.Integer(), nullable=True))
    op.create_index("ix_collection_proposals_plan_step_id", "collection_proposals", ["plan_step_id"])
    op.add_column("collection_requests", sa.Column("plan_step_id", sa.String(128), nullable=True))
    op.add_column("collection_requests", sa.Column("plan_revision", sa.Integer(), nullable=True))
    op.create_index("ix_collection_requests_plan_step_id", "collection_requests", ["plan_step_id"])


def downgrade() -> None:
    op.drop_index("ix_collection_requests_plan_step_id", table_name="collection_requests")
    op.drop_column("collection_requests", "plan_revision")
    op.drop_column("collection_requests", "plan_step_id")
    op.drop_index("ix_collection_proposals_plan_step_id", table_name="collection_proposals")
    op.drop_column("collection_proposals", "plan_revision")
    op.drop_column("collection_proposals", "plan_step_id")
