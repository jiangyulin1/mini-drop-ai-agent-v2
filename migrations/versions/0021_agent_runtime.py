"""Persist Agent Runtime bindings, turns and normalized events.

G1/G2: AcceptedTurn is not completion.  Mini-Drop owns the durable session
binding and replay-safe event sequence; the sidecar remains replaceable and
rebuilds from CaseContextSnapshot after restart.

Revision ID: 0021_agent_runtime
Revises: 0020_cluster_scope
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_agent_runtime"
down_revision: Union[str, Sequence[str], None] = "0020_cluster_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "agent_runtime_bindings" not in tables:
        op.create_table(
            "agent_runtime_bindings",
            sa.Column("case_id", sa.String(length=128), primary_key=True),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("runtime_type", sa.String(length=32), nullable=False),
            sa.Column("runtime_version", sa.String(length=64), nullable=False),
            sa.Column("runtime_session_id", sa.String(length=128), nullable=False),
            sa.Column("runtime_generation", sa.Integer(), nullable=False, default=1),
            sa.Column("status", sa.String(length=32), nullable=False, default="READY"),
            sa.Column("last_event_seq", sa.Integer(), nullable=False, default=0),
            sa.Column("last_context_snapshot_id", sa.String(length=128), nullable=True),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", name="uq_agent_runtime_binding_case",
            ),
        )
        op.create_index(
            "ix_agent_runtime_bindings_tenant_id",
            "agent_runtime_bindings",
            ["tenant_id"],
        )

    if "agent_runtime_turns" not in tables:
        op.create_table(
            "agent_runtime_turns",
            sa.Column("turn_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("runtime_session_id", sa.String(length=128), nullable=True),
            sa.Column("runtime_generation", sa.Integer(), nullable=False, default=1),
            sa.Column("user_message", sa.Text(), nullable=False),
            sa.Column("requested_mode", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, default="ACCEPTED"),
            sa.Column("accepted_mode", sa.String(length=32), nullable=False, default="deterministic"),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "turn_id", name="uq_agent_runtime_turn",
            ),
            sa.UniqueConstraint("idempotency_key", name="uq_agent_runtime_turn_idem"),
        )
        op.create_index(
            "ix_agent_runtime_turns_case_id", "agent_runtime_turns", ["case_id"],
        )
        op.create_index(
            "ix_agent_runtime_turns_tenant_id", "agent_runtime_turns", ["tenant_id"],
        )
        op.create_index(
            "ix_agent_runtime_turns_idempotency_key", "agent_runtime_turns", ["idempotency_key"],
        )

    if "agent_runtime_events" not in tables:
        op.create_table(
            "agent_runtime_events",
            sa.Column("event_id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("runtime_generation", sa.Integer(), nullable=False, default=1),
            sa.Column("event_seq", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False, default=dict),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "runtime_generation", "event_seq",
                name="uq_agent_runtime_event_seq",
            ),
            sa.UniqueConstraint("idempotency_key", name="uq_agent_runtime_event_idem"),
        )
        op.create_index(
            "ix_agent_runtime_events_case_id", "agent_runtime_events", ["case_id"],
        )
        op.create_index(
            "ix_agent_runtime_events_tenant_id", "agent_runtime_events", ["tenant_id"],
        )
        op.create_index(
            "ix_agent_runtime_events_idempotency_key", "agent_runtime_events", ["idempotency_key"],
        )


def downgrade() -> None:
    op.drop_table("agent_runtime_events")
    op.drop_table("agent_runtime_turns")
    op.drop_table("agent_runtime_bindings")
