"""Shared, dependency-light contracts used by Mini-Drop Server and Agent."""

from mini_drop_contracts.collector_spec import (
    CollectorSpec,
    catalog_hash,
    catalog_payload,
    get_collector_spec,
    list_collector_specs,
)

__all__ = [
    "CollectorSpec",
    "catalog_hash",
    "catalog_payload",
    "get_collector_spec",
    "list_collector_specs",
]
