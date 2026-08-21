from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app
from server.app.models import Base


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", "tenant-a")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "deterministic")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("MINI_DROP_EMBEDDING_PROVIDER", "local")
    from server.app.diagnosis import embeddings
    from server.app.diagnosis import knowledge_memory

    embeddings._provider.cache_clear()

    def fake_vectors(values):
        rows = []
        for value in values:
            vector = [0.0] * 1536
            for token in knowledge_memory._tokens(value):
                vector[sum(ord(char) for char in token) % 1536] += 1.0
            rows.append(vector)
        return rows

    monkeypatch.setattr(knowledge_memory, "embed_documents", fake_vectors)
    monkeypatch.setattr(knowledge_memory, "embed_query", lambda value: fake_vectors([value])[0])
    reset_engine()
    init_db()
    yield TestClient(app)
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()
    embeddings._provider.cache_clear()


def _create_case(client: TestClient) -> dict:
    response = client.post("/api/v1/cases", json={
        "title": "checkout CPU 调查",
        "problem_description": "checkout 服务 CPU 持续超过 90%",
        "recovery_goal": "形成可验证结论",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {"service_id": "checkout"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_case_document_and_memory_are_retrieved_as_chunks_not_prompt_context(client: TestClient):
    case = _create_case(client)
    case_id = case["case_id"]
    uploaded = client.post(
        "/api/v1/knowledge-documents/upload",
        params={
            "filename": "checkout-runbook.md",
            "scope": "CASE",
            "case_id": case_id,
        },
        content="# Checkout Runbook\nCPU 高时先检查 serializer 热点和请求并发。",
        headers={"content-type": "text/markdown"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["data"]["scope"] == "CASE"
    assert uploaded.json()["data"]["chunk_count"] == 1
    assert uploaded.json()["data"]["index_status"] == "READY"

    memory = client.post(f"/api/v1/cases/{case_id}/memory/refresh")
    assert memory.status_code == 200, memory.text
    assert "checkout 服务 CPU" in memory.json()["data"]["summary_text"]
    assert memory.json()["data"]["auto_capture"] is True

    from server.app.main import _build_runtime_case_context
    snapshot = _build_runtime_case_context(case, "tenant-a")
    assert snapshot.knowledge_context == []

    retrieval = client.post(
        "/internal/agent/tools/search-knowledge",
        json={"case_id": case_id, "query": "serializer CPU", "limit": 5},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert retrieval.status_code == 200, retrieval.text
    result = retrieval.json()["data"]
    assert result["retrieval_mode"] == "chunked_on_demand"
    assert result["knowledge_is_evidence"] is False
    runbook = next(item for item in result["items"] if item["title"] == "checkout-runbook.md")
    assert runbook["chunk_id"].startswith("chunk-")
    assert runbook["document_id"] == uploaded.json()["data"]["document_id"]
    assert runbook["start_offset"] == 0
    assert "serializer" in runbook["content"]
    assert "不能替代当前运行时 Evidence" in runbook["caveat"]


def test_long_document_is_chunked_with_stable_spans(client: TestClient):
    case_id = _create_case(client)["case_id"]
    content = "\n\n".join(f"第 {index} 段：checkout serializer 调优步骤与发布核验。" for index in range(100))
    created = client.post("/api/v1/knowledge-documents/text", json={
        "title": "长篇运行手册", "content": content, "scope": "CASE", "case_id": case_id,
    })
    assert created.status_code == 200, created.text
    document_id = created.json()["data"]["document_id"]
    assert created.json()["data"]["chunk_count"] > 1
    detail = client.get(f"/api/v1/knowledge-documents/{document_id}")
    chunks = detail.json()["data"]["chunks"]
    assert len(chunks) == created.json()["data"]["chunk_count"]
    assert all(chunk["content_sha256"] for chunk in chunks)
    assert all(chunk["start_offset"] < chunk["end_offset"] for chunk in chunks)


def test_global_knowledge_lifecycle_and_memory_promotion(client: TestClient):
    case = _create_case(client)
    case_id = case["case_id"]
    created = client.post("/api/v1/knowledge-documents/text", json={
        "title": "CPU 值班说明",
        "content": "checkout CPU 高时对比发布窗口，再核验火焰图。",
        "scope": "GLOBAL",
    })
    assert created.status_code == 200, created.text
    document_id = created.json()["data"]["document_id"]

    listed = client.get("/api/v1/knowledge-documents", params={"case_id": case_id})
    assert listed.status_code == 200
    assert any(item["document_id"] == document_id for item in listed.json()["data"]["items"])

    archived = client.delete(f"/api/v1/knowledge-documents/{document_id}")
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "ARCHIVED"

    promoted = client.post(f"/api/v1/cases/{case_id}/memory/promote")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["data"]["kind"] == "MEMORY"
    assert promoted.json()["data"]["scope"] == "GLOBAL"

    disabled = client.patch(f"/api/v1/cases/{case_id}/memory", json={"auto_capture": False})
    assert disabled.status_code == 200
    assert disabled.json()["data"]["auto_capture"] is False


def test_upload_rejects_unsupported_binary(client: TestClient):
    case_id = _create_case(client)["case_id"]
    response = client.post(
        "/api/v1/knowledge-documents/upload",
        params={"filename": "screen.png", "scope": "CASE", "case_id": case_id},
        content=b"not-an-image",
        headers={"content-type": "image/png"},
    )
    assert response.status_code == 400
    assert "支持 TXT" in response.json()["detail"]


def test_archived_documents_are_excluded_from_retrieval(client: TestClient):
    case_id = _create_case(client)["case_id"]
    created = client.post("/api/v1/knowledge-documents/text", json={
        "title": "私有手册", "content": "checkout secret serializer token", "scope": "CASE", "case_id": case_id,
    }).json()["data"]
    client.delete(f"/api/v1/knowledge-documents/{created['document_id']}")
    response = client.post(
        "/internal/agent/tools/search-knowledge",
        json={"case_id": case_id, "query": "secret serializer", "limit": 10},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    ids = {item["document_id"] for item in response.json()["data"]["items"]}
    assert created["document_id"] not in ids


def test_lexical_default_ranks_directly_without_query_embedding(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from server.app.diagnosis import embeddings, knowledge_memory

    monkeypatch.setenv("MINI_DROP_EMBEDDING_PROVIDER", "lexical")
    embeddings._provider.cache_clear()

    def unexpected_query_embedding(_value: str) -> list[float]:
        raise AssertionError("lexical retrieval must not call embed_query")

    monkeypatch.setattr(knowledge_memory, "embed_query", unexpected_query_embedding)
    case_id = _create_case(client)["case_id"]
    documents = [
        ("双命中", "serializer 热点造成 CPU 上升。"),
        ("单命中", "serializer 出现额外分配。"),
        ("不相关", "磁盘队列长度保持稳定。"),
    ]
    for title, content in documents:
        response = client.post("/api/v1/knowledge-documents/text", json={
            "title": title,
            "content": content,
            "scope": "CASE",
            "case_id": case_id,
        })
        assert response.status_code == 200, response.text

    response = client.post("/api/v1/knowledge-search", json={
        "case_id": case_id,
        "query": "serializer CPU",
        "limit": 3,
    })

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["retrieval_mode"] == "lexical"
    results = payload["items"]
    assert [item["title"] for item in results[:2]] == ["双命中", "单命中"]
    assert results[0]["score"] > results[1]["score"] > results[2]["score"]
    embeddings._provider.cache_clear()
