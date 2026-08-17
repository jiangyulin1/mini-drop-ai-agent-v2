"""Closed registry for selectable diagnostic strategies."""

from __future__ import annotations

from typing import Any

from server.app.diagnosis.strategies.causal_graph import CausalGraphStrategy
from server.app.diagnosis.strategies.evidence_first import EvidenceFirstStrategy
from server.app.diagnosis.strategies.exploratory import ExploratoryStrategy
from server.app.diagnosis.strategies.hybrid import HybridStrategy
from server.app.diagnosis.strategies.hypothesis_first import HypothesisFirstStrategy
from server.app.diagnosis.strategies.rule_tree import RuleTreeStrategy


STRATEGY_REGISTRY = {
    item.strategy_id: item
    for item in (
        RuleTreeStrategy(), HypothesisFirstStrategy(), EvidenceFirstStrategy(),
        CausalGraphStrategy(), ExploratoryStrategy(), HybridStrategy(),
    )
}

LEGACY_STRATEGY_ALIASES = {
    "CONSTRAINED_HYBRID": "hybrid",
    "DECISION_TREE": "rule_tree",
    "EXPLORATORY": "exploratory",
}


def normalize_strategy_id(strategy_id: str | None) -> str:
    raw = str(getattr(strategy_id, "value", strategy_id) or "hybrid")
    return LEGACY_STRATEGY_ALIASES.get(raw, raw.lower())


def get_strategy(strategy_id: str | None):
    normalized = normalize_strategy_id(strategy_id)
    try:
        return STRATEGY_REGISTRY[normalized]
    except KeyError as exc:
        raise ValueError(f"UNREGISTERED_DIAGNOSTIC_STRATEGY:{normalized}") from exc


def strategy_catalog() -> list[dict[str, Any]]:
    return [
        {
            "strategy_id": item.strategy_id,
            "strategy_version": item.strategy_version,
            "description": item.description,
            "prompt_guidance": item.render_prompt_guidance(),
        }
        for item in STRATEGY_REGISTRY.values()
    ]
