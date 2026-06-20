"""AI provider configuration and OpenAI-compatible chat client.

The runtime is intentionally vendor-neutral. Any provider that exposes an
OpenAI-compatible `/v1/chat/completions` endpoint can be used by setting URL,
API key and model through environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from server.app.common_utils import env_bool

FeatureName = Literal["nlp", "rca", "summarize"]


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
    provider = os.getenv("MINI_DROP_AI_PROVIDER", os.getenv("DEEPSEEK_PROVIDER", "deepseek"))
    base_url = os.getenv("MINI_DROP_AI_BASE_URL", os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"))
    api_key = os.getenv("MINI_DROP_AI_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
    model = os.getenv("MINI_DROP_AI_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

    defaults = _mode_defaults(mode)
    return AISettings(
        enabled=mode,
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        nlp_enabled=_feature_enabled("MINI_DROP_NLP_ENABLED", defaults["nlp"]),
        rca_enabled=_feature_enabled("MINI_DROP_RCA_ENABLED", defaults["rca"]),
        summarize_enabled=_feature_enabled("MINI_DROP_SUMMARIZE_ENABLED", defaults["summarize"]),
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
    return _post_json(
        _chat_url(settings.base_url),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )


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


def _feature_enabled(env_name: str, mode_default: bool) -> bool:
    if not mode_default:
        return False
    return env_bool(env_name, True)


def _post_json(url: str, headers: dict, json: dict, timeout: int):
    import requests
    return requests.post(url, headers=headers, json=json, timeout=timeout)
