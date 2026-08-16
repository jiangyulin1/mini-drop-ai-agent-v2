"""Immutable, one-shot approval bindings for executable proposals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from server.app.diagnosis.v6_policy import stable_hash


APPROVAL_FIELDS = (
    "operation",
    "normalized_arguments",
    "target_resource_incarnation",
    "risk",
    "scope_revision",
    "control_revision",
    "execution_epoch",
    "expires_at",
    "nonce",
    "approver_identity",
)


def approval_digest(binding: dict[str, Any]) -> str:
    """Hash only the contract fields, so metadata cannot weaken the binding."""
    return stable_hash({field: binding.get(field) for field in APPROVAL_FIELDS})


def seal_approval_binding(**values: Any) -> dict[str, Any]:
    binding = {field: values.get(field) for field in APPROVAL_FIELDS}
    binding["proposal_digest"] = approval_digest(binding)
    binding["consumed_at"] = None
    return binding


def verify_approval_binding(
    binding: dict[str, Any] | None,
    *,
    supplied_digest: str,
    approver_identity: str,
    scope_revision: int,
    control_revision: int,
    execution_epoch: str,
    now: datetime | None = None,
) -> str | None:
    """Return a stable rejection code, or ``None`` when the grant is valid."""
    if not binding or not supplied_digest:
        return "APPROVAL_DIGEST_REQUIRED"
    if binding.get("consumed_at"):
        return "APPROVAL_ALREADY_CONSUMED"
    if supplied_digest != binding.get("proposal_digest"):
        return "APPROVAL_DIGEST_MISMATCH"
    if approval_digest(binding) != supplied_digest:
        return "APPROVAL_BINDING_TAMPERED"
    if binding.get("approver_identity") != approver_identity:
        return "APPROVAL_APPROVER_MISMATCH"
    if int(binding.get("scope_revision") or 0) != int(scope_revision):
        return "APPROVAL_SCOPE_STALE"
    if int(binding.get("control_revision") or 0) != int(control_revision):
        return "APPROVAL_CONTROL_STALE"
    if str(binding.get("execution_epoch") or "") != str(execution_epoch):
        return "APPROVAL_EXECUTION_EPOCH_STALE"
    try:
        expires_at = datetime.fromisoformat(str(binding.get("expires_at") or ""))
    except ValueError:
        return "APPROVAL_EXPIRY_INVALID"
    if expires_at.tzinfo is None:
        return "APPROVAL_EXPIRY_INVALID"
    if expires_at <= (now or datetime.now(timezone.utc)):
        return "APPROVAL_EXPIRED"
    return None
