"""Lease fencing context propagated through Supervisor-owned writes."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class CaseLeaseFence:
    case_id: str
    tenant_id: str
    owner: str
    token: int


class LeaseFenceViolation(RuntimeError):
    """Raised before commit when a Supervisor no longer owns its lease."""


_ACTIVE_CASE_FENCE: ContextVar[CaseLeaseFence | None] = ContextVar(
    "mini_drop_active_case_lease_fence",
    default=None,
)


def active_case_lease_fence() -> CaseLeaseFence | None:
    return _ACTIVE_CASE_FENCE.get()


@contextmanager
def case_lease_fence(fence: CaseLeaseFence) -> Iterator[None]:
    token: Token[CaseLeaseFence | None] = _ACTIVE_CASE_FENCE.set(fence)
    try:
        yield
    finally:
        _ACTIVE_CASE_FENCE.reset(token)
