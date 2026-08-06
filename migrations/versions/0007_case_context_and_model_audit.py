"""Persist versioned Case context packets and model-call audit metadata.

Revision ID: 0007_case_context_audit
Revises: 0006_incident_cases
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_case_context_audit"
down_revision: Union[str, Sequence[str], None] = "0006_incident_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_context_packets" not in tables:
        op.create_table(
            "case_context_packets",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
            sa.Column("purpose", sa.String(length=128), nullable=False),
            sa.Column("iteration_no", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("projection_stats_json", sa.JSON(), nullable=True),
            sa.Column("source_versions_json", sa.JSON(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "id", "case_id", "tenant_id", name="uq_context_packet_case_tenant",
            ),
            sa.ForeignKeyConstraint(
                ["case_id", "tenant_id"],
                ["incident_cases.id", "incident_cases.tenant_id"],
                name="fk_context_packet_case_tenant",
                ondelete="CASCADE",
            ),
        )
        op.create_index("ix_case_context_packets_case_id", "case_context_packets", ["case_id"])
        op.create_index("ix_case_context_packets_tenant_id", "case_context_packets", ["tenant_id"])

    inspector = sa.inspect(op.get_bind())
    if "case_model_attempts" not in inspector.get_table_names():
        op.create_table(
            "case_model_attempts",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("context_packet_id", sa.String(length=128), nullable=False),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model", sa.String(length=128), nullable=False),
            sa.Column("model_snapshot", sa.String(length=128), nullable=True),
            sa.Column("prompt_version", sa.String(length=128), nullable=False),
            sa.Column("output_schema", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("response_hash", sa.String(length=64), nullable=True),
            sa.Column("error_code", sa.String(length=128), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["context_packet_id", "case_id", "tenant_id"],
                [
                    "case_context_packets.id",
                    "case_context_packets.case_id",
                    "case_context_packets.tenant_id",
                ],
                name="fk_model_attempt_context_case_tenant",
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_case_model_attempts_context_packet_id",
            "case_model_attempts",
            ["context_packet_id"],
        )
        op.create_index("ix_case_model_attempts_case_id", "case_model_attempts", ["case_id"])
        op.create_index("ix_case_model_attempts_tenant_id", "case_model_attempts", ["tenant_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_model_attempts" in tables:
        op.drop_table("case_model_attempts")
    if "case_context_packets" in tables:
        op.drop_table("case_context_packets")
