#!/usr/bin/env python3
"""Normalize an adapter response without reading private Oracle files."""

from __future__ import annotations

from typing import Any


def normalize(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    confidence = str(value.get("confidence") or "LOW").upper()
    if confidence not in {"LOW", "MEDIUM", "HIGH"}:
        confidence = "LOW"
    refs = value.get("supporting_evidence") if isinstance(value.get("supporting_evidence"), list) else []
    counter = value.get("counter_evidence") if isinstance(value.get("counter_evidence"), list) else []
    missing = value.get("missing_evidence") if isinstance(value.get("missing_evidence"), list) else []
    return {
        "schema": "mini-drop.normalized-answer.v1",
        "conclusion": str(value.get("conclusion") or ""),
        "root_location": str(value.get("root_location") or "unknown"),
        "mechanism": str(value.get("mechanism") or ""),
        "confidence": confidence,
        "supporting_evidence": refs,
        "counter_evidence": counter,
        "missing_evidence": missing,
        "next_action": str(value.get("next_action") or "request aligned evidence"),
        "abstain": bool(value.get("abstain", False)),
    }
