"""Add versioned explainable confidence ledgers and operator adjustments.

Revision ID: 0034_confidence_ledger
Revises: 0033_evidence_dep
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_confidence_ledger"
down_revision: Union[str, Sequence[str], None] = "0033_evidence_dep"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "conclusion_revisions" in tables:
        existing = {item["name"] for item in inspector.get_columns("conclusion_revisions")}
        with op.batch_alter_table("conclusion_revisions") as batch:
            if "root_location_json" not in existing:
                batch.add_column(sa.Column("root_location_json", sa.JSON(), nullable=False, server_default="{}"))
            if "mechanism_json" not in existing:
                batch.add_column(sa.Column("mechanism_json", sa.JSON(), nullable=False, server_default="{}"))
            if "confidence_reason" not in existing:
                batch.add_column(sa.Column("confidence_reason", sa.Text(), nullable=False, server_default=""))
    if "confidence_chain_snapshots" not in tables:
        op.create_table(
            "confidence_chain_snapshots",
            sa.Column("snapshot_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("chain_type", sa.String(32), nullable=False),
            sa.Column("chain_id", sa.String(256), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
            sa.Column("computed_confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("operator_requested_confidence", sa.Float(), nullable=True),
            sa.Column("effective_confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confidence_cap", sa.Float(), nullable=False, server_default="1"),
            sa.Column("calculation_version", sa.String(64), nullable=False),
            sa.Column("confidence_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("invalidated_evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("remaining_active_support", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("ledger_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("operator_adjustment_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("case_id", "chain_type", "chain_id", "revision", name="uq_confidence_chain_snapshot"),
        )
        op.create_index("ix_confidence_chain_snapshots_case_id", "confidence_chain_snapshots", ["case_id"])
        op.create_index("ix_confidence_chain_snapshots_tenant_id", "confidence_chain_snapshots", ["tenant_id"])
        op.create_index("ix_confidence_chain_case_current", "confidence_chain_snapshots", ["case_id", "chain_type", "chain_id", "revision"])
    if "confidence_adjustments" not in tables:
        op.create_table(
            "confidence_adjustments",
            sa.Column("adjustment_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("chain_type", sa.String(32), nullable=False),
            sa.Column("chain_id", sa.String(256), nullable=False),
            sa.Column("revision_before", sa.Integer(), nullable=False),
            sa.Column("revision_after", sa.Integer(), nullable=False),
            sa.Column("confidence_before", sa.Float(), nullable=False),
            sa.Column("requested_confidence", sa.Float(), nullable=False),
            sa.Column("effective_confidence", sa.Float(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("calculation_version", sa.String(64), nullable=False),
            sa.Column("actor_id", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_confidence_adjustments_case_id", "confidence_adjustments", ["case_id"])
        op.create_index("ix_confidence_adjustments_tenant_id", "confidence_adjustments", ["tenant_id"])
        op.create_index("ix_confidence_adjustments_chain", "confidence_adjustments", ["case_id", "chain_type", "chain_id", "created_at"])

    # Keep explicit branch-local reuse decisions separate from shared
    # Evidence.  This is intentionally added to the current compatibility
    # revision: older deployments may already be stamped at 0034, and
    # ``server.app.migration.upgrade_database`` creates the table check-first
    # for those databases as well.
    if "evidence_reuse_decisions" not in tables:
        op.create_table(
            "evidence_reuse_decisions",
            sa.Column("decision_id", sa.String(128), primary_key=True),
            sa.Column("case_id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(128), nullable=False),
            sa.Column("investigation_run_id", sa.String(128), nullable=True),
            sa.Column("cycle_id", sa.String(128), nullable=True),
            sa.Column("obligation_id", sa.String(256), nullable=True),
            sa.Column("contract_digest", sa.String(128), nullable=False, server_default=""),
            sa.Column("collector_id", sa.String(128), nullable=False),
            sa.Column("collector_spec_version", sa.String(32), nullable=False, server_default="unknown"),
            sa.Column("probe_fingerprint", sa.String(128), nullable=False),
            sa.Column("result_fingerprint", sa.String(128), nullable=True),
            sa.Column("collection_request_id", sa.String(128), nullable=True),
            sa.Column("task_id", sa.String(128), nullable=True),
            sa.Column("evidence_id", sa.String(128), nullable=True),
            sa.Column("projection_id", sa.String(128), nullable=True),
            sa.Column("projection_hash", sa.String(128), nullable=True),
            sa.Column("target_identity_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("requested_time_window_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("effective_time_window_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("control_revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("scope_revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("runtime_generation", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("evidence_review_revision", sa.Integer(), nullable=True),
            sa.Column("lifecycle_status", sa.String(24), nullable=True),
            sa.Column("trust_state", sa.String(24), nullable=True),
            sa.Column("decision", sa.String(32), nullable=False),
            sa.Column("reason_codes_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("actor_id", sa.String(128), nullable=False, server_default="agent"),
            sa.Column("source", sa.String(64), nullable=False, server_default="collection_supervisor"),
            sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("invalidated_reason", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "investigation_run_id", "contract_digest",
                "probe_fingerprint", "evidence_id", "projection_hash",
                name="uq_evidence_reuse_decision_idempotency",
            ),
        )
        op.create_index(
            "ix_evidence_reuse_decisions_case_id", "evidence_reuse_decisions", ["case_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_tenant_id", "evidence_reuse_decisions", ["tenant_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_investigation_run_id",
            "evidence_reuse_decisions", ["investigation_run_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_cycle_id", "evidence_reuse_decisions", ["cycle_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_collection_request_id",
            "evidence_reuse_decisions", ["collection_request_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_task_id", "evidence_reuse_decisions", ["task_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_evidence_id",
            "evidence_reuse_decisions", ["evidence_id"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_probe_fingerprint",
            "evidence_reuse_decisions", ["probe_fingerprint"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_probe", "evidence_reuse_decisions",
            ["case_id", "tenant_id", "probe_fingerprint", "created_at"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_run", "evidence_reuse_decisions",
            ["case_id", "tenant_id", "investigation_run_id", "created_at"],
        )
        op.create_index(
            "ix_evidence_reuse_decisions_evidence", "evidence_reuse_decisions",
            ["case_id", "tenant_id", "evidence_id", "projection_hash"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "evidence_reuse_decisions" in set(inspector.get_table_names()):
        for index_name in (
            "ix_evidence_reuse_decisions_evidence",
            "ix_evidence_reuse_decisions_run",
            "ix_evidence_reuse_decisions_probe",
            "ix_evidence_reuse_decisions_probe_fingerprint",
            "ix_evidence_reuse_decisions_evidence_id",
            "ix_evidence_reuse_decisions_task_id",
            "ix_evidence_reuse_decisions_collection_request_id",
            "ix_evidence_reuse_decisions_cycle_id",
            "ix_evidence_reuse_decisions_investigation_run_id",
            "ix_evidence_reuse_decisions_tenant_id",
            "ix_evidence_reuse_decisions_case_id",
        ):
            op.drop_index(index_name, table_name="evidence_reuse_decisions")
        op.drop_table("evidence_reuse_decisions")
    with op.batch_alter_table("conclusion_revisions") as batch:
        batch.drop_column("confidence_reason")
        batch.drop_column("mechanism_json")
        batch.drop_column("root_location_json")
    op.drop_index("ix_confidence_adjustments_chain", table_name="confidence_adjustments")
    op.drop_index("ix_confidence_adjustments_tenant_id", table_name="confidence_adjustments")
    op.drop_index("ix_confidence_adjustments_case_id", table_name="confidence_adjustments")
    op.drop_table("confidence_adjustments")
    op.drop_index("ix_confidence_chain_case_current", table_name="confidence_chain_snapshots")
    op.drop_index("ix_confidence_chain_snapshots_tenant_id", table_name="confidence_chain_snapshots")
    op.drop_index("ix_confidence_chain_snapshots_case_id", table_name="confidence_chain_snapshots")
    op.drop_table("confidence_chain_snapshots")
