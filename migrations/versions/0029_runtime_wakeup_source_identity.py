"""Allow many outbox sources to map to one coalesced runtime wakeup.

Revision ID: 0029_wakeup_source_identity
Revises: 0028_plan_collection_lineage
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_wakeup_source_identity"
down_revision: Union[str, Sequence[str], None] = "0028_plan_collection_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rebuild instead of altering the primary key in place so the migration is
    # identical on PostgreSQL and SQLite. Existing rows are preserved exactly.
    bind = op.get_bind()
    constraint_name = (
        "uq_runtime_wakeup_source_outbox_v2"
        if bind.dialect.name == "postgresql"
        else "uq_runtime_wakeup_source_outbox"
    )
    op.create_table(
        "runtime_wakeup_sources_v2",
        sa.Column("wakeup_id", sa.String(128), nullable=False),
        sa.Column("outbox_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("evidence_watermark", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("outbox_id", name=constraint_name),
    )
    op.execute(sa.text(
        "INSERT INTO runtime_wakeup_sources_v2 "
        "(wakeup_id, outbox_id, source_ref, evidence_watermark, mapped_at) "
        "SELECT wakeup_id, outbox_id, source_ref, evidence_watermark, mapped_at "
        "FROM runtime_wakeup_sources"
    ))
    op.drop_table("runtime_wakeup_sources")
    op.rename_table("runtime_wakeup_sources_v2", "runtime_wakeup_sources")
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE runtime_wakeup_sources "
            "RENAME CONSTRAINT uq_runtime_wakeup_source_outbox_v2 "
            "TO uq_runtime_wakeup_source_outbox"
        ))
    op.create_index(
        "ix_runtime_wakeup_sources_wakeup_id",
        "runtime_wakeup_sources",
        ["wakeup_id"],
    )


def downgrade() -> None:
    # The old shape can retain only one source per wakeup. Keep the
    # lexicographically first outbox mapping deterministically.
    bind = op.get_bind()
    constraint_name = (
        "uq_runtime_wakeup_source_outbox_v1"
        if bind.dialect.name == "postgresql"
        else "uq_runtime_wakeup_source_outbox"
    )
    op.create_table(
        "runtime_wakeup_sources_v1",
        sa.Column("wakeup_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("outbox_id", sa.String(128), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("evidence_watermark", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("outbox_id", name=constraint_name),
    )
    op.execute(sa.text(
        "INSERT INTO runtime_wakeup_sources_v1 "
        "(wakeup_id, outbox_id, source_ref, evidence_watermark, mapped_at) "
        "SELECT s.wakeup_id, s.outbox_id, s.source_ref, s.evidence_watermark, s.mapped_at "
        "FROM runtime_wakeup_sources s "
        "WHERE s.outbox_id = (SELECT MIN(s2.outbox_id) "
        "FROM runtime_wakeup_sources s2 WHERE s2.wakeup_id = s.wakeup_id)"
    ))
    op.drop_index("ix_runtime_wakeup_sources_wakeup_id", table_name="runtime_wakeup_sources")
    op.drop_table("runtime_wakeup_sources")
    op.rename_table("runtime_wakeup_sources_v1", "runtime_wakeup_sources")
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(
            "ALTER TABLE runtime_wakeup_sources "
            "RENAME CONSTRAINT uq_runtime_wakeup_source_outbox_v1 "
            "TO uq_runtime_wakeup_source_outbox"
        ))
    op.create_index(
        "ix_runtime_wakeup_sources_outbox_id",
        "runtime_wakeup_sources",
        ["outbox_id"],
    )
