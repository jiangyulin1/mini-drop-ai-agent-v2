"""Unify data entry on ResourceRef + EvidenceAttachment.

E1 (plan section 10.2): replace the multi-way association split
(initial_task_ids / source_task_id / target_scope.evidence_task_ids /
source_collection_ids) with one tenant-scoped attachment table so that a Task,
a Collection batch or a conversation `@` reference can be proven to enter the
next diagnosis.  Backfills existing case.initial_task_ids/source_task_id.

Revision ID: 0018_case_resource_attachments
Revises: 0017_system_controls
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_case_resource_attachments"
down_revision: Union[str, Sequence[str], None] = "0017_system_controls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "case_resource_attachments" not in tables:
        op.create_table(
            "case_resource_attachments",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("case_id", sa.String(length=128), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("resource_type", sa.String(length=40), nullable=False),
            sa.Column("resource_id", sa.String(length=128), nullable=False),
            sa.Column("resource_revision", sa.Integer(), nullable=True),
            sa.Column("label", sa.String(length=256), nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=True),
            sa.Column("attached_by", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("scope_match", sa.String(length=20), nullable=False),
            sa.Column("time_match", sa.String(length=20), nullable=False),
            sa.Column("freshness", sa.String(length=20), nullable=False),
            sa.Column("quality", sa.String(length=20), nullable=False),
            sa.Column("evidence_ids_json", sa.JSON(), nullable=True),
            sa.Column("rejection_reason", sa.String(length=128), nullable=True),
            sa.Column("supersedes_json", sa.JSON(), nullable=True),
            sa.Column("row_version", sa.Integer(), nullable=False, default=0),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "case_id", "tenant_id", "resource_type", "resource_id",
                name="uq_attachment_case_resource",
            ),
        )
        op.create_index(
            "ix_case_resource_attachments_case_id",
            "case_resource_attachments", ["case_id"],
        )
        op.create_index(
            "ix_case_resource_attachments_tenant_id",
            "case_resource_attachments", ["tenant_id"],
        )

    # 回填旧字段：initial_task_ids / source_task_id 投影为只读 Attachment
    columns = {item["name"] for item in inspector.get_columns("incident_cases")}
    if "initial_task_ids" in columns:
        bind = op.get_bind()
        rows = bind.execute(sa.text(
            "SELECT id, tenant_id, created_by, initial_task_ids, source_task_id "
            "FROM incident_cases"
        )).fetchall()
        for case_id, tenant_id, created_by, initial_task_ids, source_task_id in rows:
            initial_task_ids = _as_json_list(initial_task_ids)
            existing = set(bind.execute(sa.text(
                "SELECT resource_type, resource_id FROM case_resource_attachments "
                "WHERE case_id = :c AND tenant_id = :t"
            ), {"c": case_id, "t": tenant_id}).fetchall())
            seen = set()
            for task_id in initial_task_ids:
                key = ("task", str(task_id))
                if key in seen or key in existing:
                    continue
                seen.add(key)
                bind.execute(sa.text(
                    "INSERT INTO case_resource_attachments ("
                    "id, case_id, tenant_id, resource_type, resource_id, resource_revision, "
                    "label, source, purpose, attached_by, status, scope_match, time_match, "
                    "freshness, quality, evidence_ids_json, rejection_reason, supersedes_json, "
                    "row_version, created_at, updated_at) VALUES ("
                    ":id, :case_id, :tenant_id, :resource_type, :resource_id, :resource_revision, "
                    ":label, :source, :purpose, :attached_by, :status, :scope_match, :time_match, "
                    ":freshness, :quality, :evidence_ids_json, :rejection_reason, :supersedes_json, "
                    ":row_version, :created_at, :updated_at)"
                ), {
                    "id": f"attach-{case_id}-task-{task_id}",
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    "resource_type": "task",
                    "resource_id": str(task_id),
                    "resource_revision": 1,
                    "label": str(task_id),
                    "source": "legacy_backfill",
                    "purpose": "迁移回填：initial_task_ids 投影为只读 Attachment",
                    "attached_by": created_by,
                    "status": "ACCEPTED",
                    "scope_match": "MATCH",
                    "time_match": "MATCH",
                    "freshness": "FRESH",
                    "quality": "COMPLETE",
                    "evidence_ids_json": "[]",
                    "rejection_reason": None,
                    "supersedes_json": "[]",
                    "row_version": 0,
                    "created_at": _now(),
                    "updated_at": _now(),
                })
            if source_task_id and ("task", str(source_task_id)) not in existing:
                bind.execute(sa.text(
                    "INSERT INTO case_resource_attachments ("
                    "id, case_id, tenant_id, resource_type, resource_id, resource_revision, "
                    "label, source, purpose, attached_by, status, scope_match, time_match, "
                    "freshness, quality, evidence_ids_json, rejection_reason, supersedes_json, "
                    "row_version, created_at, updated_at) VALUES ("
                    ":id, :case_id, :tenant_id, :resource_type, :resource_id, :resource_revision, "
                    ":label, :source, :purpose, :attached_by, :status, :scope_match, :time_match, "
                    ":freshness, :quality, :evidence_ids_json, :rejection_reason, :supersedes_json, "
                    ":row_version, :created_at, :updated_at)"
                ), {
                    "id": f"attach-{case_id}-src-{source_task_id}",
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    "resource_type": "task",
                    "resource_id": str(source_task_id),
                    "resource_revision": 1,
                    "label": str(source_task_id),
                    "source": "legacy_backfill",
                    "purpose": "迁移回填：source_task_id 投影为只读 Attachment",
                    "attached_by": created_by,
                    "status": "ACCEPTED",
                    "scope_match": "MATCH",
                    "time_match": "MATCH",
                    "freshness": "FRESH",
                    "quality": "COMPLETE",
                    "evidence_ids_json": "[]",
                    "rejection_reason": None,
                    "supersedes_json": "[]",
                    "row_version": 0,
                    "created_at": _now(),
                    "updated_at": _now(),
                })


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_json_list(value: Any) -> list[str]:
    """SQLite JSON 列读回是 TEXT；反序列化避免把字符串按字符迭代。"""
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return [str(item) for item in parsed if item is not None] if isinstance(parsed, list) else []
    return []


def downgrade() -> None:
    op.drop_table("case_resource_attachments")
