"""PostgreSQL-backed at-least-once DomainOutbox relay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


Publisher = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class RelayResult:
    claimed: int = 0
    delivered: int = 0
    failed: int = 0
    reclaimed: int = 0
    dead: int = 0


class OutboxRelay:
    """Claim, publish, and ACK events without acknowledging before delivery."""

    def __init__(
        self,
        repository: Any,
        publisher: Publisher,
        *,
        relay_id: str,
        lease_seconds: int = 120,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._relay_id = relay_id
        self._lease_seconds = lease_seconds

    def run_once(self, *, limit: int = 10) -> RelayResult:
        reclaimed_items = self._repository.reclaim_expired_outbox(
            self._relay_id,
            limit=limit,
        )
        claimed = self._repository.claim_domain_outbox(
            self._relay_id,
            limit=limit,
            lease_seconds=self._lease_seconds,
        )
        delivered = 0
        failed = 0
        dead = sum(1 for item in reclaimed_items if item.get("status") == "DEAD")
        for event in claimed:
            try:
                self._publisher(event)
            except Exception as exc:  # noqa: BLE001 - persisted NACK captures adapter failures.
                updated = self._repository.fail_domain_outbox(
                    event["outbox_id"],
                    claim_token=event["claim_token"],
                    error=f"{type(exc).__name__}: {exc}",
                )
                failed += 1
                dead += int((updated or {}).get("status") == "DEAD")
                continue
            updated = self._repository.mark_outbox_delivered(
                event["outbox_id"],
                claim_token=event["claim_token"],
                dispatch_outcome="DOWNSTREAM_ACKNOWLEDGED",
            )
            delivered += int((updated or {}).get("status") == "DELIVERED")
        return RelayResult(
            claimed=len(claimed),
            delivered=delivered,
            failed=failed,
            reclaimed=len(reclaimed_items),
            dead=dead,
        )


class IdempotentOutboxConsumer:
    """Persist a stable effect receipt before returning the downstream ACK."""

    def __init__(self, repository: Any, *, consumer_name: str) -> None:
        self._repository = repository
        self._consumer_name = consumer_name

    def consume(
        self,
        event: dict[str, Any],
        *,
        effect_key: str,
        effect_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._repository.record_outbox_consumer_effect(
            event_id=event["outbox_id"],
            consumer_name=self._consumer_name,
            effect_key=effect_key,
            effect_payload=effect_payload or event.get("payload") or {},
        )
