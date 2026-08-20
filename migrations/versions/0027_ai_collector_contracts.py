"""Add AI Collector proposal, request, and Evidence analysis contracts.

Revision ID: 0027_ai_collector_contracts
Revises: 0026_model_attempt_pi_audit
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_ai_collector_contracts"
down_revision: Union[str, Sequence[str], None] = "0026_model_attempt_pi_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_proposals",
        sa.Column("proposal_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("agent_run_id", sa.String(128), nullable=True),
        sa.Column("cycle_id", sa.String(128), nullable=True),
        sa.Column("collector_id", sa.String(128), nullable=False),
        sa.Column("collector_spec_version", sa.String(32), nullable=False),
        sa.Column("target_selector", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("time_window", sa.JSON(), nullable=False),
        sa.Column("information_goal", sa.Text(), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=False),
        sa.Column("expected_cost", sa.JSON(), nullable=False),
        sa.Column("expected_risk", sa.String(24), nullable=False),
        sa.Column("input_evidence_refs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("validation_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("case_id", "tenant_id", "proposal_id", name="uq_collection_proposal"),
    )
    op.create_index("ix_collection_proposals_case_id", "collection_proposals", ["case_id"])
    op.create_index("ix_collection_proposals_tenant_id", "collection_proposals", ["tenant_id"])
    op.create_index("ix_collection_proposals_agent_run_id", "collection_proposals", ["agent_run_id"])
    op.create_index("ix_collection_proposals_cycle_id", "collection_proposals", ["cycle_id"])
    op.create_index("ix_collection_proposals_status", "collection_proposals", ["status"])
    op.create_index("ix_collection_proposals_case_status", "collection_proposals", ["case_id", "status"])

    op.create_table(
        "collection_requests",
        sa.Column("collection_request_id", sa.String(128), primary_key=True),
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("collector_id", sa.String(128), nullable=False),
        sa.Column("collector_spec_version", sa.String(32), nullable=False),
        sa.Column("resolved_target_identity", sa.JSON(), nullable=False),
        sa.Column("effective_parameters", sa.JSON(), nullable=False),
        sa.Column("runtime_generation", sa.Integer(), nullable=False),
        sa.Column("control_revision", sa.Integer(), nullable=False),
        sa.Column("scope_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("budget_reservation", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=True),
        sa.Column("attempt_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("proposal_id", name="uq_collection_request_proposal"),
        sa.UniqueConstraint("idempotency_key", name="uq_collection_request_idempotency"),
    )
    op.create_index("ix_collection_requests_proposal_id", "collection_requests", ["proposal_id"])
    op.create_index("ix_collection_requests_case_id", "collection_requests", ["case_id"])
    op.create_index("ix_collection_requests_tenant_id", "collection_requests", ["tenant_id"])
    op.create_index("ix_collection_requests_status", "collection_requests", ["status"])
    op.create_index("ix_collection_requests_task_id", "collection_requests", ["task_id"])
    op.create_index("ix_collection_requests_case_status", "collection_requests", ["case_id", "status"])

    op.create_table(
        "evidence_analysis_runs",
        sa.Column("analysis_run_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("evidence_inputs", sa.JSON(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("model_config_id", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("side_effect_policy", sa.String(24), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("anomalies", sa.JSON(), nullable=False),
        sa.Column("interpretations", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("next_collection_proposals", sa.JSON(), nullable=False),
        sa.Column("input_state", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("runtime_turn_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "case_id", "tenant_id", "input_fingerprint",
            name="uq_evidence_analysis_input_fingerprint",
        ),
    )
    op.create_index("ix_evidence_analysis_runs_case_id", "evidence_analysis_runs", ["case_id"])
    op.create_index("ix_evidence_analysis_runs_tenant_id", "evidence_analysis_runs", ["tenant_id"])
    op.create_index("ix_evidence_analysis_runs_status", "evidence_analysis_runs", ["status"])
    op.create_index("ix_evidence_analysis_runs_case_status", "evidence_analysis_runs", ["case_id", "status"])
    op.create_index("ix_evidence_analysis_runs_turn", "evidence_analysis_runs", ["runtime_turn_id"])


def downgrade() -> None:
    op.drop_table("evidence_analysis_runs")
    op.drop_table("collection_requests")
    op.drop_table("collection_proposals")
