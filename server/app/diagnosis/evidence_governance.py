"""Deterministic human-in-the-loop Evidence governance rules."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any


REVIEW_DECISIONS = {
    "TRUSTED",
    "LOW_TRUST",
    "EXCLUDED",
    "RESTORE_AS_TRUSTED",
    "RESTORE_AS_LOW_TRUST",
    "HIDDEN",
    "VISIBLE",
    "ARCHIVED",
    "UNARCHIVED",
}

INFERENCE_DECISIONS = {
    "TRUSTED",
    "LOW_TRUST",
    "EXCLUDED",
    "RESTORE_AS_TRUSTED",
    "RESTORE_AS_LOW_TRUST",
}

HIGH_IMPACT_DECISIONS = {
    "EXCLUDED",
    "RESTORE_AS_TRUSTED",
    "RESTORE_AS_LOW_TRUST",
}

ASSESSMENT_OPTIONS = {
    "target_identity": {"CONFIRMED", "POSSIBLE_PID_REUSE", "INSTANCE_MISMATCH"},
    "time_alignment": {"FULL_WINDOW", "PARTIAL_WINDOW", "MISMATCH"},
    "data_integrity": {"COMPLETE", "TRUNCATED", "FAILED"},
    "source_reliability": {"NATIVE_COLLECTOR", "EXTERNAL_SYSTEM", "MANUAL_UPLOAD"},
    "scope_fit": {"CORRECT", "PARTIAL", "WRONG_SCOPE"},
    "corroboration": {"INDEPENDENT_SUPPORT", "NONE", "CONFLICT"},
    "freshness": {"CURRENT_WINDOW", "HISTORICAL", "EXPIRED"},
}

_SCORES = {
    "target_identity": {"CONFIRMED": 20, "POSSIBLE_PID_REUSE": 8, "INSTANCE_MISMATCH": 0},
    "time_alignment": {"FULL_WINDOW": 20, "PARTIAL_WINDOW": 10, "MISMATCH": 0},
    "data_integrity": {"COMPLETE": 15, "TRUNCATED": 7, "FAILED": 0},
    "source_reliability": {"NATIVE_COLLECTOR": 15, "EXTERNAL_SYSTEM": 10, "MANUAL_UPLOAD": 3},
    "scope_fit": {"CORRECT": 15, "PARTIAL": 7, "WRONG_SCOPE": 0},
    "corroboration": {"INDEPENDENT_SUPPORT": 10, "NONE": 5, "CONFLICT": 0},
    "freshness": {"CURRENT_WINDOW": 5, "HISTORICAL": 2, "EXPIRED": 0},
}

_PROCESS_SECRET = secrets.token_bytes(32)


def normalize_assessment(value: dict[str, Any] | None) -> dict[str, str]:
    assessment = value or {}
    normalized: dict[str, str] = {}
    unknown = set(assessment) - set(ASSESSMENT_OPTIONS)
    if unknown:
        raise ValueError(f"INVALID_EVIDENCE_ASSESSMENT_FIELDS:{','.join(sorted(unknown))}")
    for field, raw in assessment.items():
        option = str(raw or "").strip().upper()
        if option not in ASSESSMENT_OPTIONS[field]:
            raise ValueError(f"INVALID_EVIDENCE_ASSESSMENT:{field}:{option}")
        normalized[field] = option
    return normalized


def assess_evidence(value: dict[str, Any] | None) -> dict[str, Any]:
    assessment = normalize_assessment(value)
    score = sum(_SCORES[field][option] for field, option in assessment.items())
    maximum = sum(max(_SCORES[field].values()) for field in assessment)
    derived = round(score * 100 / maximum) if maximum else 50
    hard_exclusion = any((
        assessment.get("target_identity") == "INSTANCE_MISMATCH",
        assessment.get("time_alignment") == "MISMATCH",
        assessment.get("data_integrity") == "FAILED",
        assessment.get("scope_fit") == "WRONG_SCOPE",
    ))
    reasons: list[str] = []
    if assessment.get("time_alignment") == "PARTIAL_WINDOW":
        reasons.append("采样时间只覆盖部分故障窗口")
    if assessment.get("target_identity") == "POSSIBLE_PID_REUSE":
        reasons.append("目标进程身份可能发生 PID 复用")
    if assessment.get("corroboration") == "NONE":
        reasons.append("没有独立数据源交叉佐证")
    if assessment.get("corroboration") == "CONFLICT":
        reasons.append("存在独立证据冲突")
    if assessment.get("data_integrity") == "TRUNCATED":
        reasons.append("数据不完整或已截断")
    if assessment.get("freshness") in {"HISTORICAL", "EXPIRED"}:
        reasons.append("证据不属于当前故障窗口")
    if hard_exclusion:
        recommendation = "EXCLUDED"
    elif derived < 70 or assessment.get("corroboration") == "CONFLICT":
        recommendation = "LOW_TRUST"
    else:
        recommendation = "TRUSTED"
    return {
        "assessment": assessment,
        "derived_trust_score": derived,
        "recommended_decision": recommendation,
        "reasons": reasons,
    }


def compatibility_status(lifecycle_status: str, trust_state: str) -> str:
    lifecycle = str(lifecycle_status or "ACTIVE").upper()
    trust = str(trust_state or "UNREVIEWED").upper()
    if lifecycle != "ACTIVE":
        return lifecycle
    return "LOW_TRUST" if trust == "LOW_TRUST" else "ACTIVE"


def review_result(
    *, decision: str, current_lifecycle: str, current_trust: str,
    hidden: bool = False, archived: bool = False,
) -> dict[str, Any]:
    decision = str(decision or "").upper()
    if decision not in REVIEW_DECISIONS:
        raise ValueError(f"INVALID_REVIEW_DECISION:{decision}")
    lifecycle = str(current_lifecycle or "ACTIVE").upper()
    trust = str(current_trust or "UNREVIEWED").upper()
    next_hidden = bool(hidden)
    next_archived = bool(archived)
    if decision == "TRUSTED":
        if lifecycle == "EXCLUDED":
            raise ValueError("EXCLUDED_EVIDENCE_REQUIRES_EXPLICIT_RESTORE")
        trust = "TRUSTED"
    elif decision == "LOW_TRUST":
        if lifecycle == "EXCLUDED":
            raise ValueError("EXCLUDED_EVIDENCE_REQUIRES_EXPLICIT_RESTORE")
        trust = "LOW_TRUST"
    elif decision == "EXCLUDED":
        lifecycle = "EXCLUDED"
    elif decision == "RESTORE_AS_TRUSTED":
        lifecycle, trust = "ACTIVE", "TRUSTED"
    elif decision == "RESTORE_AS_LOW_TRUST":
        lifecycle, trust = "ACTIVE", "LOW_TRUST"
    elif decision == "HIDDEN":
        next_hidden = True
    elif decision == "VISIBLE":
        next_hidden = False
    elif decision == "ARCHIVED":
        next_archived = True
    elif decision == "UNARCHIVED":
        next_archived = False
    return {
        "lifecycle_status": lifecycle,
        "trust_state": trust,
        "ui_hidden": next_hidden,
        "ui_archived": next_archived,
        "status": compatibility_status(lifecycle, trust),
        "inference_changed": decision in INFERENCE_DECISIONS,
    }


def canonical_impact_payload(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def _impact_secret() -> bytes:
    for name in (
        "MINI_DROP_EVIDENCE_REVIEW_SECRET",
        "MINI_DROP_WEB_SESSION_SECRET",
        "MINI_DROP_API_KEY",
    ):
        configured = os.getenv(name, "").strip()
        if configured:
            return configured.encode("utf-8")
    return _PROCESS_SECRET


def create_impact_token(payload: dict[str, Any], *, ttl_seconds: int = 300) -> str:
    expiry = int(time.time()) + max(30, min(int(ttl_seconds), 900))
    body = canonical_impact_payload(payload)
    signature = hmac.new(
        _impact_secret(), str(expiry).encode("ascii") + b"." + body, hashlib.sha256,
    ).hexdigest()
    return f"v1.{expiry}.{signature}"


def verify_impact_token(token: str, payload: dict[str, Any]) -> bool:
    try:
        version, expiry_text, supplied = str(token or "").split(".", 2)
        expiry = int(expiry_text)
    except (TypeError, ValueError):
        return False
    if version != "v1" or expiry < int(time.time()):
        return False
    expected = hmac.new(
        _impact_secret(), expiry_text.encode("ascii") + b"." + canonical_impact_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)
