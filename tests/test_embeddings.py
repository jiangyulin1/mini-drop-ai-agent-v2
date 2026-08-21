from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest

from server.app.diagnosis import embeddings


@pytest.fixture(autouse=True)
def reset_embedding_runtime():
    original_model = embeddings._model
    embeddings._provider.cache_clear()
    embeddings._model = None
    yield
    embeddings._model = original_model
    embeddings._provider.cache_clear()


@pytest.mark.parametrize("configured", [None, "", "lexical", "disabled", "unexpected"])
def test_safe_providers_use_deterministic_zero_vectors_without_model_or_network(
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
):
    if configured is None:
        monkeypatch.delenv("MINI_DROP_EMBEDDING_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("MINI_DROP_EMBEDDING_PROVIDER", configured)
    embeddings._provider.cache_clear()

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("safe lexical mode attempted model or network access")

    monkeypatch.setattr(embeddings, "_local_model", unexpected_call)
    monkeypatch.setattr(embeddings, "_remote_embeddings", unexpected_call)

    documents = embeddings.embed_documents(["alpha", "beta"])
    query = embeddings.embed_query("alpha")

    assert len(documents) == 2
    assert documents[0] is not documents[1]
    assert all(len(vector) == embeddings.VECTOR_DIMENSIONS for vector in [*documents, query])
    assert all(value == 0.0 for vector in [*documents, query] for value in vector)
    assert embeddings.embedding_status() == {
        "provider": "lexical",
        "model": None,
        "dimensions": 1536,
        "ready": True,
        "vector_search_enabled": False,
    }


def test_local_status_does_not_initialize_model_and_reflects_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MINI_DROP_EMBEDDING_PROVIDER", "local")
    embeddings._provider.cache_clear()

    status = embeddings.embedding_status()

    assert status["provider"] == "local"
    assert status["ready"] is False
    assert status["vector_search_enabled"] is False
    assert embeddings._model is None

    @dataclass
    class FakeVector:
        values: list[float]

        def tolist(self) -> list[float]:
            return self.values

    class FakeModel:
        def passage_embed(self, values):
            return (FakeVector([float(index + 1)]) for index, _ in enumerate(values))

        def query_embed(self, _value):
            return iter([FakeVector([3.0])])

    embeddings._model = FakeModel()
    assert embeddings.embed_documents(["one", "two"])[1][0] == 2.0
    assert embeddings.embed_query("one")[0] == 3.0
    assert embeddings.embedding_status()["vector_search_enabled"] is True


def test_local_provider_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MINI_DROP_EMBEDDING_PROVIDER", "local")
    monkeypatch.setitem(sys.modules, "fastembed", None)
    embeddings._provider.cache_clear()

    with pytest.raises(RuntimeError, match="embedding-local"):
        embeddings.embed_query("query")


def test_openai_compatible_provider_keeps_remote_embedding_behavior(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MINI_DROP_EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MINI_DROP_EMBEDDING_BASE_URL", "https://embeddings.example/v1/")
    monkeypatch.setenv("MINI_DROP_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("MINI_DROP_EMBEDDING_MODEL", "test-embedding-model")
    embeddings._provider.cache_clear()
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [
                {"index": 1, "embedding": [2.0]},
                {"index": 0, "embedding": [1.0]},
            ]}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(embeddings.requests, "post", fake_post)

    vectors = embeddings.embed_documents(["first", "second"])

    assert [vector[0] for vector in vectors] == [1.0, 2.0]
    assert all(len(vector) == 1536 for vector in vectors)
    assert captured["url"] == "https://embeddings.example/v1/embeddings"
    assert captured["json"] == {
        "model": "test-embedding-model",
        "input": ["first", "second"],
        "dimensions": 1536,
    }
    assert embeddings.embedding_status() == {
        "provider": "openai_compatible",
        "model": "test-embedding-model",
        "dimensions": 1536,
        "ready": True,
        "vector_search_enabled": True,
    }
