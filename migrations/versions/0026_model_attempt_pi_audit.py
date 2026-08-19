"""Extend model-attempt audit for Pi runtime token/cost telemetry.

Revision ID: 0026_model_attempt_pi_audit
Revises: 0025_evidence_contract
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026_model_attempt_pi_audit"
down_revision: Union[str, Sequence[str], None] = "0025_evidence_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("case_model_attempts") as batch:
        batch.add_column(sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cost", sa.Float(), nullable=True))
        batch.add_column(sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"))
        batch.add_column(sa.Column("turn_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("context_snapshot_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("config_fingerprint", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("tool_catalog_version", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("case_model_attempts") as batch:
        batch.drop_column("tool_catalog_version")
        batch.drop_column("config_fingerprint")
        batch.drop_column("context_snapshot_id")
        batch.drop_column("turn_id")
        batch.drop_column("retry_count")
        batch.drop_column("cost")
        batch.drop_column("cache_write_tokens")
        batch.drop_column("cache_read_tokens")
