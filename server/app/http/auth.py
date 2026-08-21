"""Server-derived HTTP identity and authorization helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request


WEB_SESSION_COOKIE = "mini_drop_web_session"
WEB_SESSION_TTL_SECONDS = 7 * 24 * 3600


def requires_api_auth(request: Request) -> bool:
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return False
    return request.url.path.startswith("/api/") and request.url.path not in {
        "/api/healthz",
        "/api/livez",
        "/api/readyz",
        "/api/metrics",
        "/api/auth/set-cookie",
        "/api/auth/bootstrap",
        "/api/auth/clear-cookie",
    }


def extract_api_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    cookie = request.cookies.get("mini_drop_api_key")
    return cookie.strip() if cookie else None


def create_web_session_token() -> str:
    """Issue an opaque token signed by a server-only secret."""
    signing_key = os.getenv("MINI_DROP_WEB_SESSION_SECRET", "").strip()
    if not signing_key:
        signing_key = os.getenv("MINI_DROP_API_KEY", "").strip()
    if not signing_key:
        raise RuntimeError("web session signing key is empty")
    expires_at = int(time.time()) + WEB_SESSION_TTL_SECONDS
    payload = f"v1.{expires_at}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(
        signing_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def valid_web_session(request: Request) -> bool:
    token = request.cookies.get(WEB_SESSION_COOKIE, "").strip()
    if not token:
        return False
    signing_key = os.getenv("MINI_DROP_WEB_SESSION_SECRET", "").strip()
    if not signing_key:
        signing_key = os.getenv("MINI_DROP_API_KEY", "").strip()
    if not signing_key:
        return False
    try:
        version, expiry, nonce, supplied_signature = token.split(".", 3)
        if version != "v1" or not nonce or int(expiry) < int(time.time()):
            return False
    except (TypeError, ValueError):
        return False
    payload = f"{version}.{expiry}.{nonce}"
    expected_signature = hmac.new(
        signing_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied_signature, expected_signature)


def principal_for_request(token: str | None) -> str:
    configured = os.getenv("MINI_DROP_API_PRINCIPAL_ID", "").strip()
    if configured:
        return configured[:128]
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        return f"api-key:{digest}"
    return "local-development"


def roles_for_request() -> set[str]:
    configured = os.getenv("MINI_DROP_API_ROLES", "").strip()
    if configured:
        return {item.strip() for item in configured.split(",") if item.strip()}
    if os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return {"operator", "authorization_admin"}
    return {"operator"}


def request_principal(request: Request) -> str:
    return getattr(request.state, "principal_id", "local-development")


def request_tenant() -> str:
    tenant_id = os.getenv("MINI_DROP_API_TENANT_ID", "local-development").strip()
    return (tenant_id or "local-development")[:128]


def require_role(request: Request, role: str) -> None:
    if role not in getattr(request.state, "principal_roles", set()):
        raise HTTPException(status_code=403, detail=f"当前主体缺少角色: {role}")


# Temporary compatibility names used by route slices still being migrated.
_extract_api_token = extract_api_token
_request_principal = request_principal
_request_tenant = request_tenant
_require_role = require_role
