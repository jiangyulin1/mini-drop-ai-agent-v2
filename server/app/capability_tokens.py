"""Short-lived, scope-bound capability tokens for internal gateways."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional
from uuid import uuid4

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel


_PROCESS_LOCAL_DEVELOPMENT_SECRET = secrets.token_bytes(32)


class CapabilityClaims(StrictModel):
    version: str = "v1"
    jti: str = Field(min_length=16, max_length=128)
    principal_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    grant_id: str = Field(min_length=1, max_length=128)
    case_id: Optional[str] = Field(default=None, max_length=128)
    capability_type: str = Field(min_length=1, max_length=32)
    capability_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    resource_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_result_bytes: int = Field(ge=0, le=100_000_000)
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)


class CapabilityTokenError(ValueError):
    pass


def issue_capability_token(
    *,
    principal_id: str,
    tenant_id: str,
    grant_id: str,
    case_id: str | None,
    capability_type: str,
    capability_id: str,
    operation: str,
    resource: dict[str, Any],
    parameters: dict[str, Any],
    max_result_bytes: int,
    ttl_seconds: int = 60,
) -> str:
    now = int(time.time())
    ttl = min(max(int(ttl_seconds), 1), 300)
    claims = CapabilityClaims(
        jti=f"cap_{uuid4().hex}",
        principal_id=principal_id,
        tenant_id=tenant_id,
        grant_id=grant_id,
        case_id=case_id,
        capability_type=capability_type,
        capability_id=capability_id,
        operation=operation,
        resource_hash=canonical_hash(resource),
        parameters_hash=canonical_hash(parameters),
        max_result_bytes=max_result_bytes,
        issued_at=now,
        expires_at=now + ttl,
    )
    payload = _canonical_json(claims.model_dump(mode="json")).encode("utf-8")
    encoded = _b64url_encode(payload)
    signature = hmac.new(_signing_secret(), f"v1.{encoded}".encode("ascii"), hashlib.sha256).digest()
    return f"v1.{encoded}.{_b64url_encode(signature)}"


def verify_capability_token(
    token: str,
    *,
    principal_id: str,
    tenant_id: str,
    capability_type: str,
    capability_id: str,
    operation: str,
    resource: dict[str, Any],
    parameters: dict[str, Any],
    now: int | None = None,
) -> CapabilityClaims:
    try:
        version, encoded, provided_signature = token.split(".", 2)
    except ValueError as exc:
        raise CapabilityTokenError("CAPABILITY_TOKEN_MALFORMED") from exc
    if version != "v1":
        raise CapabilityTokenError("CAPABILITY_TOKEN_VERSION_UNSUPPORTED")
    expected_signature = hmac.new(
        _signing_secret(), f"v1.{encoded}".encode("ascii"), hashlib.sha256,
    ).digest()
    try:
        decoded_signature = _b64url_decode(provided_signature)
    except ValueError as exc:
        raise CapabilityTokenError("CAPABILITY_TOKEN_MALFORMED") from exc
    if not hmac.compare_digest(decoded_signature, expected_signature):
        raise CapabilityTokenError("CAPABILITY_TOKEN_SIGNATURE_INVALID")
    try:
        claims = CapabilityClaims.model_validate_json(_b64url_decode(encoded))
    except Exception as exc:
        raise CapabilityTokenError("CAPABILITY_TOKEN_CLAIMS_INVALID") from exc

    current = int(time.time()) if now is None else int(now)
    if claims.expires_at < current:
        raise CapabilityTokenError("CAPABILITY_TOKEN_EXPIRED")
    if claims.issued_at > current + 30:
        raise CapabilityTokenError("CAPABILITY_TOKEN_ISSUED_IN_FUTURE")
    expected = {
        "principal_id": principal_id,
        "tenant_id": tenant_id,
        "capability_type": capability_type,
        "capability_id": capability_id,
        "operation": operation,
        "resource_hash": canonical_hash(resource),
        "parameters_hash": canonical_hash(parameters),
    }
    for field, value in expected.items():
        if getattr(claims, field) != value:
            raise CapabilityTokenError(f"CAPABILITY_TOKEN_SCOPE_MISMATCH:{field}")
    return claims


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _signing_secret() -> bytes:
    configured = os.getenv("MINI_DROP_CAPABILITY_TOKEN_SECRET", "")
    if configured:
        raw = configured.encode("utf-8")
        if len(raw) < 32:
            raise CapabilityTokenError("MINI_DROP_CAPABILITY_TOKEN_SECRET must contain at least 32 bytes")
        return raw
    auth_enabled = os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if auth_enabled:
        raise CapabilityTokenError("CAPABILITY_TOKEN_SECRET_NOT_CONFIGURED")
    return _PROCESS_LOCAL_DEVELOPMENT_SECRET


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ValueError("invalid base64url") from exc
