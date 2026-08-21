"""Optional embeddings with a network-free lexical default."""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import requests


VECTOR_DIMENSIONS = 1536
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
LEXICAL_PROVIDER = "lexical"
_model = None
_model_lock = threading.Lock()


def embed_documents(values: Iterable[str]) -> list[list[float]]:
    texts = [str(value or "") for value in values]
    if not texts:
        return []
    provider = _provider()
    if provider == LEXICAL_PROVIDER:
        return [_zero_vector() for _ in texts]
    if provider == "openai_compatible":
        return _remote_embeddings(texts)
    model = _local_model()
    return [_fit_vector(vector.tolist()) for vector in model.passage_embed(texts)]


def embed_query(value: str) -> list[float]:
    provider = _provider()
    if provider == LEXICAL_PROVIDER:
        return _zero_vector()
    if provider == "openai_compatible":
        return _remote_embeddings([str(value or "")])[0]
    vector = next(iter(_local_model().query_embed(str(value or ""))))
    return _fit_vector(vector.tolist())


def embedding_status() -> dict[str, object]:
    provider = _provider()
    if provider == LEXICAL_PROVIDER:
        return {
            "provider": LEXICAL_PROVIDER,
            "model": None,
            "dimensions": VECTOR_DIMENSIONS,
            "ready": True,
            "vector_search_enabled": False,
        }
    if provider == "openai_compatible":
        ready = bool(
            os.getenv("MINI_DROP_EMBEDDING_BASE_URL", "").strip()
            and os.getenv("MINI_DROP_EMBEDDING_API_KEY", "").strip()
        )
        model = os.getenv("MINI_DROP_EMBEDDING_MODEL", "text-embedding-3-small")
    else:
        # Status checks must never import FastEmbed or trigger a model download.
        # A local provider becomes ready only after its lazy initialization has
        # completed successfully in this process.
        ready = _model is not None
        model = os.getenv("MINI_DROP_LOCAL_EMBEDDING_MODEL", DEFAULT_LOCAL_MODEL)
    return {
        "provider": provider,
        "model": model,
        "dimensions": VECTOR_DIMENSIONS,
        "ready": ready,
        "vector_search_enabled": ready,
    }


@lru_cache(maxsize=1)
def _provider() -> str:
    value = os.getenv("MINI_DROP_EMBEDDING_PROVIDER", "").strip().lower()
    if value in {"openai", "openai_compatible", "remote"}:
        return "openai_compatible"
    if value == "local":
        return "local"
    # Empty, disabled, lexical, and unknown values all fail closed to the
    # deterministic lexical path. Local model use must always be explicit.
    return LEXICAL_PROVIDER


def _local_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    "LOCAL_EMBEDDING_DEPENDENCY_MISSING: install the "
                    "'embedding-local' extra or rebuild the server image with "
                    "MINI_DROP_BAKE_LOCAL_EMBEDDING=1"
                ) from exc

            cache_dir = os.getenv("MINI_DROP_EMBEDDING_CACHE_DIR") or None
            baked_path = Path(cache_dir) / "fast-bge-small-zh-v1.5" if cache_dir else None
            _model = TextEmbedding(
                model_name=os.getenv("MINI_DROP_LOCAL_EMBEDDING_MODEL", DEFAULT_LOCAL_MODEL),
                cache_dir=cache_dir,
                threads=max(1, int(os.getenv("MINI_DROP_EMBEDDING_THREADS", "2"))),
                specific_model_path=str(baked_path) if baked_path and baked_path.is_dir() else None,
            )
    return _model


def _remote_embeddings(texts: list[str]) -> list[list[float]]:
    base_url = os.getenv("MINI_DROP_EMBEDDING_BASE_URL", "").rstrip("/")
    api_key = os.getenv("MINI_DROP_EMBEDDING_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("EMBEDDING_PROVIDER_NOT_CONFIGURED")
    response = requests.post(
        f"{base_url}/embeddings",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": os.getenv("MINI_DROP_EMBEDDING_MODEL", "text-embedding-3-small"),
            "input": texts,
            "dimensions": VECTOR_DIMENSIONS,
        },
        timeout=float(os.getenv("MINI_DROP_EMBEDDING_TIMEOUT_SEC", "60")),
    )
    response.raise_for_status()
    rows = sorted(response.json().get("data") or [], key=lambda item: int(item.get("index") or 0))
    if len(rows) != len(texts):
        raise RuntimeError("EMBEDDING_PROVIDER_INVALID_RESPONSE")
    return [_fit_vector(row.get("embedding") or []) for row in rows]


def _fit_vector(value: list[float]) -> list[float]:
    if len(value) > VECTOR_DIMENSIONS:
        return [float(item) for item in value[:VECTOR_DIMENSIONS]]
    return [float(item) for item in value] + [0.0] * (VECTOR_DIMENSIONS - len(value))


def _zero_vector() -> list[float]:
    return [0.0] * VECTOR_DIMENSIONS
