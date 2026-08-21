"""Add operator knowledge documents and per-Case retrospective memory.

Revision ID: 0031_case_memory_knowledge
Revises: 0030_causal_graph_scoped_ids
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from server.app.diagnosis.embeddings import VECTOR_DIMENSIONS


revision: str = "0031_case_memory_knowledge"
down_revision: Union[str, Sequence[str], None] = "0030_causal_graph_scoped_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"
    if is_postgresql:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="CASE"),
        sa.Column("kind", sa.String(24), nullable=False, server_default="DOCUMENT"),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("filename", sa.String(512), nullable=True),
        sa.Column("media_type", sa.String(128), nullable=False, server_default="text/plain"),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "content_sha256", "case_id", name="uq_knowledge_document_scope_hash"),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])
    op.create_index("ix_knowledge_documents_case_id", "knowledge_documents", ["case_id"])
    op.create_index("ix_knowledge_documents_status", "knowledge_documents", ["status"])
    op.create_index(
        "ix_knowledge_documents_tenant_scope",
        "knowledge_documents",
        ["tenant_id", "scope", "status"],
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.String(128), primary_key=True),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("lexical_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("term_frequencies", sa.JSON(), nullable=False),
        sa.Column("embedding", Vector(VECTOR_DIMENSIONS).with_variant(sa.JSON(), "sqlite"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_document_index"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])
    op.create_index("ix_knowledge_chunks_case_id", "knowledge_chunks", ["case_id"])
    op.create_index(
        "ix_knowledge_chunks_tenant_case",
        "knowledge_chunks",
        ["tenant_id", "case_id"],
    )
    if is_postgresql:
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_lexical_fts ON knowledge_chunks "
            "USING gin (to_tsvector('simple', lexical_text))"
        )
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    op.create_table(
        "case_memories",
        sa.Column("memory_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("highlights", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("source_event_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_capture", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("promoted_document_id", sa.String(128), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "tenant_id", name="uq_case_memory_case_tenant"),
    )
    op.create_index("ix_case_memories_case_id", "case_memories", ["case_id"])
    op.create_index("ix_case_memories_tenant_id", "case_memories", ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_lexical_fts")
    op.drop_index("ix_case_memories_tenant_id", table_name="case_memories")
    op.drop_index("ix_case_memories_case_id", table_name="case_memories")
    op.drop_table("case_memories")
    op.drop_index("ix_knowledge_chunks_tenant_case", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_case_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_tenant_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_documents_tenant_scope", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_case_id", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_tenant_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
