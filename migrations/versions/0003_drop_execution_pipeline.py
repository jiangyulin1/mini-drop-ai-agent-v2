"""Add durable collection attempts and lease-based analysis jobs.

Revision ID: 0003_drop_execution
Revises: 0002_release
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_drop_execution"
down_revision: Union[str, Sequence[str], None] = "0002_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    task_columns: set[str] = set()
    if "tasks" in tables:
        task_columns = {item["name"] for item in inspector.get_columns("tasks")}
        additions = [
            sa.Column("collection_status", sa.String(length=16), nullable=False, server_default="PENDING"),
            sa.Column("analysis_status", sa.String(length=16), nullable=False, server_default="WAITING"),
            sa.Column("current_attempt_id", sa.String(length=128), nullable=True),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("collection_deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("request_id", sa.String(length=64), nullable=True),
        ]
        for column in additions:
            if column.name not in task_columns:
                op.add_column("tasks", column)
        inspector = sa.inspect(op.get_bind())
        task_columns = {item["name"] for item in inspector.get_columns("tasks")}
        indexes = {item["name"] for item in inspector.get_indexes("tasks")}
        if "ix_tasks_current_attempt_id" not in indexes:
            op.create_index("ix_tasks_current_attempt_id", "tasks", ["current_attempt_id"], unique=False)
        if "ix_tasks_collection_deadline_at" not in indexes:
            op.create_index(
                "ix_tasks_collection_deadline_at", "tasks", ["collection_deadline_at"], unique=False,
            )
        if "ix_tasks_request_id" not in indexes:
            op.create_index("ix_tasks_request_id", "tasks", ["request_id"], unique=False)

        # Preserve the old aggregate status when the legacy table actually
        # has it. Some very early development databases only contained an id.
        if "status" in task_columns:
            op.execute(
                """
                UPDATE tasks SET collection_status = CASE status
                  WHEN 'PENDING' THEN 'PENDING'
                  WHEN 'RUNNING' THEN 'RUNNING'
                  WHEN 'UPLOADING' THEN 'UPLOADING'
                  WHEN 'ANALYZING' THEN 'COLLECTED'
                  WHEN 'DONE' THEN 'COLLECTED'
                  WHEN 'CANCELLED' THEN 'CANCELLED'
                  ELSE 'FAILED' END
                """
            )
            op.execute(
                """
                UPDATE tasks SET analysis_status = CASE status
                  WHEN 'ANALYZING' THEN 'PENDING'
                  WHEN 'DONE' THEN 'SUCCEEDED'
                  WHEN 'CANCELLED' THEN 'CANCELLED'
                  WHEN 'FAILED' THEN 'FAILED'
                  ELSE 'WAITING' END
                """
            )

    can_create_attempts = {"tasks", "agents"}.issubset(tables)
    if can_create_attempts and "task_attempts" not in tables:
        op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("task_id", sa.String(length=128), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=128), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="DELIVERED"),
        sa.Column("runner_version", sa.String(length=64), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.Column("resource_usage_json", sa.JSON(), nullable=True),
        sa.Column("artifact_ids_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt_number"),
        )
        op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"], unique=False)
        op.create_index("ix_task_attempts_agent_id", "task_attempts", ["agent_id"], unique=False)
        tables.add("task_attempts")

    if {"artifacts", "task_attempts"}.issubset(tables):
        artifact_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("artifacts")}
        if "attempt_id" not in artifact_columns:
            op.add_column("artifacts", sa.Column("attempt_id", sa.String(length=128), nullable=True))
        if "identity_key" not in artifact_columns:
            op.add_column("artifacts", sa.Column("identity_key", sa.String(length=64), nullable=True))
        inspector = sa.inspect(op.get_bind())
        foreign_keys = {item.get("name") for item in inspector.get_foreign_keys("artifacts")}
        if "fk_artifacts_attempt_id_task_attempts" not in foreign_keys:
            with op.batch_alter_table("artifacts") as batch_op:
                batch_op.create_foreign_key(
                    "fk_artifacts_attempt_id_task_attempts",
                    "task_attempts",
                    ["attempt_id"],
                    ["id"],
                )
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("artifacts")}
        if "ix_artifacts_attempt_id" not in indexes:
            op.create_index("ix_artifacts_attempt_id", "artifacts", ["attempt_id"], unique=False)
        if "ix_artifacts_identity_key" not in indexes:
            op.create_index("ix_artifacts_identity_key", "artifacts", ["identity_key"], unique=True)

    if {"tasks", "task_attempts"}.issubset(tables) and "analysis_jobs" not in tables:
        op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("task_id", sa.String(length=128), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("attempt_id", sa.String(length=128), sa.ForeignKey("task_attempts.id"), nullable=False),
        sa.Column("pipeline", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("analyzer_version", sa.String(length=64), nullable=True),
        sa.Column("input_artifact_ids_json", sa.JSON(), nullable=True),
        sa.Column("output_artifact_ids_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id", "attempt_id", "pipeline",
            name="uq_analysis_job_task_attempt_pipeline",
        ),
        )
        op.create_index("ix_analysis_jobs_task_id", "analysis_jobs", ["task_id"], unique=False)
        op.create_index("ix_analysis_jobs_attempt_id", "analysis_jobs", ["attempt_id"], unique=False)
        op.create_index("ix_analysis_jobs_status", "analysis_jobs", ["status"], unique=False)
        op.create_index(
            "ix_analysis_jobs_lease_expires_at", "analysis_jobs", ["lease_expires_at"], unique=False,
        )

    if "analyzer_workers" not in tables:
        op.create_table(
        "analyzer_workers",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_job_id", sa.String(length=128), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_analyzer_workers_current_job_id", "analyzer_workers", ["current_job_id"], unique=False,
        )
        op.create_index(
            "ix_analyzer_workers_last_heartbeat_at", "analyzer_workers", ["last_heartbeat_at"], unique=False,
        )


def downgrade() -> None:
    op.drop_table("analyzer_workers")
    op.drop_table("analysis_jobs")
    op.drop_index("ix_artifacts_identity_key", table_name="artifacts")
    op.drop_index("ix_artifacts_attempt_id", table_name="artifacts")
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_constraint("fk_artifacts_attempt_id_task_attempts", type_="foreignkey")
    op.drop_column("artifacts", "identity_key")
    op.drop_column("artifacts", "attempt_id")
    op.drop_table("task_attempts")
    op.drop_index("ix_tasks_collection_deadline_at", table_name="tasks")
    op.drop_index("ix_tasks_request_id", table_name="tasks")
    op.drop_index("ix_tasks_current_attempt_id", table_name="tasks")
    op.drop_column("tasks", "collection_deadline_at")
    op.drop_column("tasks", "request_id")
    op.drop_column("tasks", "row_version")
    op.drop_column("tasks", "current_attempt_id")
    op.drop_column("tasks", "analysis_status")
    op.drop_column("tasks", "collection_status")
