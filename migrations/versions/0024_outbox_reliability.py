"""Add durable outbox recovery, consumer idempotency, and schema contracts.

Revision ID: 0024_outbox_reliability
Revises: 0023_v6_agent_core
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_outbox_reliability"
down_revision: Union[str, Sequence[str], None] = "0023_v6_agent_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    outbox_columns = {
        column["name"] for column in inspector.get_columns("domain_outbox")
    }
    additions = (
        sa.Column("aggregate_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_schema_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("domain_outbox") as batch:
        for column in additions:
            if column.name not in outbox_columns:
                batch.add_column(column)

    inspector = sa.inspect(bind)
    index_names = {
        index["name"] for index in inspector.get_indexes("domain_outbox")
    }
    if "ix_domain_outbox_claim_expiry" not in index_names:
        op.create_index(
            "ix_domain_outbox_claim_expiry",
            "domain_outbox",
            ["status", "claim_expires_at"],
        )

    if "outbox_consumer_effects" not in tables:
        op.create_table(
            "outbox_consumer_effects",
            sa.Column("receipt_id", sa.String(128), primary_key=True),
            sa.Column("event_id", sa.String(128), nullable=False),
            sa.Column("consumer_name", sa.String(128), nullable=False),
            sa.Column("effect_key", sa.String(256), nullable=False),
            sa.Column("effect_payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "event_id",
                "consumer_name",
                name="uq_outbox_consumer_event",
            ),
            sa.UniqueConstraint(
                "consumer_name",
                "effect_key",
                name="uq_outbox_consumer_effect_key",
            ),
        )
        op.create_index(
            "ix_outbox_consumer_effects_event_id",
            "outbox_consumer_effects",
            ["event_id"],
        )
        op.create_index(
            "ix_outbox_consumer_effects_consumer_name",
            "outbox_consumer_effects",
            ["consumer_name"],
        )


def downgrade() -> None:
    op.drop_table("outbox_consumer_effects")
    op.drop_index("ix_domain_outbox_claim_expiry", table_name="domain_outbox")
    with op.batch_alter_table("domain_outbox") as batch:
        for name in (
            "dead_at",
            "delivered_at",
            "max_attempts",
            "claimed_at",
            "payload_schema_version",
            "aggregate_revision",
        ):
            batch.drop_column(name)
