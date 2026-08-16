"""Complete canonical Evidence provenance and trust contract.

Revision ID: 0025_evidence_contract
Revises: 0024_outbox_reliability
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_evidence_contract"
down_revision: Union[str, Sequence[str], None] = "0024_outbox_reliability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("case_evidence") as batch:
        batch.add_column(sa.Column("source_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("completeness", sa.String(24), nullable=False, server_default="COMPLETE"))
        batch.add_column(sa.Column("trust_level", sa.String(24), nullable=False, server_default="INTERNAL"))
        batch.add_column(sa.Column("lineage_json", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("trace_id", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("case_evidence") as batch:
        batch.drop_column("trace_id")
        batch.drop_column("lineage_json")
        batch.drop_column("trust_level")
        batch.drop_column("completeness")
        batch.drop_column("sha256")
        batch.drop_column("size_bytes")
        batch.drop_column("source_id")
