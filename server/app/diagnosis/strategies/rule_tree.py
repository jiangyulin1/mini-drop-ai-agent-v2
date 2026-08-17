from __future__ import annotations

from typing import Any

from server.app.diagnosis.probe_registry import choose_probe_ids
from server.app.diagnosis.strategies.base import BaseDiagnosticStrategy


class RuleTreeStrategy(BaseDiagnosticStrategy):
    strategy_id = "rule_tree"
    strategy_version = "rule-tree.v1"
    description = "Strict symptom decision tree for reproducible probe selection."
    guidance = "Follow the registered symptom decision tree; do not invent branches or probes."

    def plan_initial_probes(self, **context: Any) -> list[str]:
        available = set(context.get("available_probe_ids") or [])
        return [item for item in choose_probe_ids(str(context.get("symptom") or "")) if item in available]
