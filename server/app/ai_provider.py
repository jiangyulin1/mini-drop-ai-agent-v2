"""AI provider configuration and OpenAI-compatible chat client.

The runtime is intentionally vendor-neutral. Any provider that exposes an
OpenAI-compatible `/v1/chat/completions` endpoint can be used by setting URL,
API key and model through environment variables.
"""

from __future__ import annotations

import os
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from server.app.common_utils import env_bool

FeatureName = Literal["nlp", "rca", "summarize"]
_MODEL_AUDIT: ContextVar[dict[str, Any] | None] = ContextVar("model_audit", default=None)


@dataclass(frozen=True)
class AISettings:
    enabled: str
    provider: str
    base_url: str
    api_key: str
    model: str
    nlp_enabled: bool
    rca_enabled: bool
    summarize_enabled: bool


def get_ai_settings() -> AISettings:
    mode = os.getenv("MINI_DROP_AI_ENABLED", "full").strip().lower()
    provider = _first_non_empty("MINI_DROP_AI_PROVIDER", "DEEPSEEK_PROVIDER", default="deepseek")
    base_url = _first_non_empty("MINI_DROP_AI_BASE_URL", "DEEPSEEK_API_BASE", default="https://api.deepseek.com")
    api_key = _first_non_empty("MINI_DROP_AI_API_KEY", "DEEPSEEK_API_KEY", default="")
    model = _first_non_empty("MINI_DROP_AI_MODEL", "DEEPSEEK_MODEL", default="deepseek-v4-flash")

    defaults = _mode_defaults(mode)
    feature_flags = _apply_feature_overrides(defaults)
    return AISettings(
        enabled=mode,
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        nlp_enabled=feature_flags["nlp"],
        rca_enabled=feature_flags["rca"],
        summarize_enabled=feature_flags["summarize"],
    )


def is_feature_enabled(feature: FeatureName) -> bool:
    settings = get_ai_settings()
    if not settings.api_key:
        return False
    return {
        "nlp": settings.nlp_enabled,
        "rca": settings.rca_enabled,
        "summarize": settings.summarize_enabled,
    }[feature]


def chat_completions(payload: dict[str, Any], timeout: int = 60):
    settings = get_ai_settings()
    audit = _MODEL_AUDIT.get()
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    try:
        response = _post_json(
            _chat_url(settings.base_url),
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        if audit:
            _record_model_audit(
                audit,
                settings,
                started_at=started_at,
                latency_ms=round((time.perf_counter() - started) * 1000),
                status="FAILED",
                error_code=type(exc).__name__,
            )
        raise

    if audit:
        response_json: dict[str, Any] = {}
        try:
            parsed = response.json()
            response_json = parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        usage = response_json.get("usage") or {}
        response_hash = hashlib.sha256(
            json.dumps(
                response_json,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        succeeded = 200 <= int(response.status_code) < 300
        _record_model_audit(
            audit,
            settings,
            started_at=started_at,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status="SUCCEEDED" if succeeded else "PROVIDER_ERROR",
            input_tokens=_optional_int(usage.get("prompt_tokens") or usage.get("input_tokens")),
            output_tokens=_optional_int(
                usage.get("completion_tokens") or usage.get("output_tokens"),
            ),
            response_hash=response_hash,
            model_snapshot=str(response_json.get("model") or settings.model)[:128],
            error_code=None if succeeded else f"HTTP_{response.status_code}",
        )
    return response


@contextmanager
def model_audit_scope(
    *,
    case_id: str,
    tenant_id: str,
    context_packet_id: str,
    prompt_version: str,
    output_schema: str,
    recorder,
):
    """Associate model calls in this context with immutable audit metadata."""
    token = _MODEL_AUDIT.set({
        "case_id": case_id,
        "tenant_id": tenant_id,
        "context_packet_id": context_packet_id,
        "prompt_version": prompt_version,
        "output_schema": output_schema,
        "recorder": recorder,
    })
    try:
        yield
    finally:
        _MODEL_AUDIT.reset(token)


def _record_model_audit(
    audit: dict[str, Any],
    settings: AISettings,
    *,
    started_at: datetime,
    latency_ms: int,
    status: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    response_hash: str | None = None,
    model_snapshot: str | None = None,
    error_code: str | None = None,
) -> None:
    audit["recorder"]({
        "context_packet_id": audit["context_packet_id"],
        "case_id": audit["case_id"],
        "tenant_id": audit["tenant_id"],
        "provider": settings.provider,
        "model": settings.model,
        "model_snapshot": model_snapshot,
        "prompt_version": audit["prompt_version"],
        "output_schema": audit["output_schema"],
        "status": status,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "response_hash": response_hash,
        "error_code": error_code,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc),
    })


def _optional_int(value: Any) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _chat_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _mode_defaults(mode: str) -> dict[str, bool]:
    if mode == "none":
        return {"nlp": False, "rca": False, "summarize": False}
    if mode == "nlp-only":
        return {"nlp": True, "rca": False, "summarize": False}
    if mode == "rca-only":
        return {"nlp": False, "rca": True, "summarize": False}
    return {"nlp": True, "rca": True, "summarize": True}


def _first_non_empty(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _apply_feature_overrides(mode_defaults: dict[str, bool]) -> dict[str, bool]:
    """Apply per-feature env flags without bypassing the global AI mode.

    MINI_DROP_AI_ENABLED is the upper bound. For example, `none` always disables
    every feature even if `.env` still contains MINI_DROP_NLP_ENABLED=true.
    """
    env_names = {
        "nlp": "MINI_DROP_NLP_ENABLED",
        "rca": "MINI_DROP_RCA_ENABLED",
        "summarize": "MINI_DROP_SUMMARIZE_ENABLED",
    }
    result: dict[str, bool] = {}
    for feature, default_enabled in mode_defaults.items():
        result[feature] = bool(default_enabled) and env_bool(env_names[feature], default_enabled)
    return result


def _post_json(url: str, headers: dict, json: dict, timeout: int):
    import requests
    return requests.post(url, headers=headers, json=json, timeout=timeout)
