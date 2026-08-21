"""Operator-facing knowledge documents and per-Case retrospective memory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from server.app.diagnosis.embeddings import embedding_status
from server.app.diagnosis.knowledge_memory import (
    create_knowledge_document,
    extract_document_text,
    get_case_memory,
    get_knowledge_document,
    get_knowledge_chunk,
    list_knowledge_documents,
    promote_case_memory,
    refresh_case_memory,
    retrieve_case_memory,
    retrieve_user_knowledge,
    update_case_memory,
    update_knowledge_document,
)
from server.app.http.auth import (
    request_principal,
    request_tenant,
    require_role,
)
from server.app.runtime_services import repo
from server.app.schemas import APIResponse


router = APIRouter()


class KnowledgeTextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=200_000)
    scope: str = "CASE"
    case_id: str | None = None


class KnowledgeUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    status: str | None = None


class MemoryUpdateRequest(BaseModel):
    auto_capture: bool


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    case_id: str
    limit: int = Field(default=8, ge=1, le=20)


def _case_or_404(case_id: str, tenant_id: str) -> dict[str, Any]:
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return case


def _bad_request(error: ValueError) -> HTTPException:
    labels = {
        "DOCUMENT_TOO_LARGE": "文档不能超过 5 MB",
        "UNSUPPORTED_DOCUMENT_TYPE": "支持 TXT、Markdown、JSON、YAML、CSV、LOG、XML 和 DOCX",
        "INVALID_DOCX": "DOCX 文件损坏或无法读取",
        "DOCUMENT_HAS_NO_TEXT": "文档中没有可索引文本",
        "DOCUMENT_ENCODING_UNSUPPORTED": "文本编码不受支持，请使用 UTF-8、UTF-16 或 GB18030",
        "INVALID_KNOWLEDGE_SCOPE": "知识范围无效",
        "CASE_ID_REQUIRED": "会话知识必须关联 Case",
        "KNOWLEDGE_CONTENT_REQUIRED": "知识内容不能为空",
        "INVALID_KNOWLEDGE_STATUS": "知识状态无效",
    }
    return HTTPException(status_code=400, detail=labels.get(str(error), str(error)))


@router.get("/api/v1/knowledge-documents")
def list_documents(request: Request, case_id: str | None = None) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    if case_id:
        _case_or_404(case_id, tenant_id)
    items = list_knowledge_documents(tenant_id, case_id)
    return APIResponse(data={"items": items, "total": len(items)})


@router.post("/api/v1/knowledge-search")
def search_documents(payload: KnowledgeSearchRequest, request: Request) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    _case_or_404(payload.case_id, tenant_id)
    items = retrieve_user_knowledge(
        payload.query, tenant_id=tenant_id, case_id=payload.case_id, limit=payload.limit,
    ) + retrieve_case_memory(
        payload.query, tenant_id=tenant_id, case_id=payload.case_id, limit=min(payload.limit, 3),
    )
    items = sorted(items, key=lambda item: -float(item.get("score") or 0))[:payload.limit]
    provider = str(embedding_status().get("provider") or "lexical")
    return APIResponse(data={
        "items": items,
        "query": payload.query,
        "retrieval_mode": (
            "lexical"
            if provider == "lexical"
            else "postgres_fts_pgvector_hybrid"
        ),
        "knowledge_is_evidence": False,
    })


@router.post("/api/v1/knowledge-documents/text")
def create_text_document(payload: KnowledgeTextRequest, request: Request) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    scope = payload.scope.upper()
    case_id = payload.case_id if scope == "CASE" else None
    if case_id:
        _case_or_404(case_id, tenant_id)
    try:
        document = create_knowledge_document(
            tenant_id=tenant_id,
            case_id=case_id,
            scope=scope,
            title=payload.title,
            filename=None,
            media_type="text/markdown",
            content_text=payload.content,
            created_by=request_principal(request),
        )
    except ValueError as error:
        raise _bad_request(error) from error
    if case_id:
        repo.record_case_event(
            case_id, tenant_id, event_type="knowledge_document_added",
            payload={"document_id": document["document_id"], "title": document["title"]},
            actor_id=request_principal(request),
        )
    return APIResponse(data=document)


@router.post("/api/v1/knowledge-documents/upload")
async def upload_document(
    request: Request,
    filename: str,
    scope: str = "CASE",
    case_id: str | None = None,
    title: str | None = None,
) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    normalized_scope = scope.upper()
    scoped_case = case_id if normalized_scope == "CASE" else None
    if scoped_case:
        _case_or_404(scoped_case, tenant_id)
    content = await request.body()
    try:
        text = extract_document_text(filename, content)
        document = create_knowledge_document(
            tenant_id=tenant_id,
            case_id=scoped_case,
            scope=normalized_scope,
            title=title or filename or "未命名知识",
            filename=filename,
            media_type=request.headers.get("content-type") or "application/octet-stream",
            content_text=text,
            created_by=request_principal(request),
        )
    except ValueError as error:
        raise _bad_request(error) from error
    if scoped_case:
        repo.record_case_event(
            scoped_case, tenant_id, event_type="knowledge_document_added",
            payload={"document_id": document["document_id"], "title": document["title"]},
            actor_id=request_principal(request),
        )
    return APIResponse(data=document)


@router.patch("/api/v1/knowledge-documents/{document_id}")
def update_document(document_id: str, payload: KnowledgeUpdateRequest, request: Request) -> APIResponse:
    require_role(request, "operator")
    try:
        document = update_knowledge_document(
            document_id, request_tenant(), status=payload.status, title=payload.title,
        )
    except ValueError as error:
        raise _bad_request(error) from error
    if document is None:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return APIResponse(data=document)


@router.get("/api/v1/knowledge-documents/{document_id}")
def read_document(document_id: str, request: Request) -> APIResponse:
    require_role(request, "operator")
    document = get_knowledge_document(document_id, request_tenant())
    if document is None:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return APIResponse(data=document)


@router.get("/api/v1/knowledge-chunks/{chunk_id}")
def read_chunk(chunk_id: str, request: Request, case_id: str | None = None) -> APIResponse:
    require_role(request, "operator")
    if case_id:
        _case_or_404(case_id, request_tenant())
    chunk = get_knowledge_chunk(chunk_id, request_tenant(), case_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="知识片段不存在或不在当前会话范围")
    return APIResponse(data=chunk)


@router.delete("/api/v1/knowledge-documents/{document_id}")
def archive_document(document_id: str, request: Request) -> APIResponse:
    require_role(request, "operator")
    document = update_knowledge_document(document_id, request_tenant(), status="ARCHIVED")
    if document is None:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return APIResponse(data=document)


@router.get("/api/v1/cases/{case_id}/memory")
def read_memory(case_id: str, request: Request) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    _case_or_404(case_id, tenant_id)
    return APIResponse(data=get_case_memory(repo, case_id, tenant_id))


@router.post("/api/v1/cases/{case_id}/memory/refresh")
def refresh_memory(case_id: str, request: Request) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    _case_or_404(case_id, tenant_id)
    memory = refresh_case_memory(repo, case_id, tenant_id)
    repo.record_case_event(
        case_id, tenant_id, event_type="case_memory_refreshed",
        payload={"memory_id": memory["memory_id"], "source_event_seq": memory["source_event_seq"]},
        actor_id=request_principal(request),
    )
    return APIResponse(data=memory)


@router.patch("/api/v1/cases/{case_id}/memory")
def configure_memory(case_id: str, payload: MemoryUpdateRequest, request: Request) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    _case_or_404(case_id, tenant_id)
    memory = get_case_memory(repo, case_id, tenant_id)
    memory = update_case_memory(case_id, tenant_id, auto_capture=payload.auto_capture) or memory
    return APIResponse(data=memory)


@router.post("/api/v1/cases/{case_id}/memory/promote")
def promote_memory(case_id: str, request: Request) -> APIResponse:
    require_role(request, "operator")
    tenant_id = request_tenant()
    _case_or_404(case_id, tenant_id)
    document = promote_case_memory(repo, case_id, tenant_id, request_principal(request))
    repo.record_case_event(
        case_id, tenant_id, event_type="case_memory_promoted",
        payload={"document_id": document["document_id"]},
        actor_id=request_principal(request),
    )
    return APIResponse(data=document)
