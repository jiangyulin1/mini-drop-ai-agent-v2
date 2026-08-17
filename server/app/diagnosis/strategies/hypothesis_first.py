from __future__ import annotations

from typing import Any

from server.app.diagnosis.probe_registry import choose_probe_ids
from server.app.diagnosis.strategies.base import BaseDiagnosticStrategy


class HypothesisFirstStrategy(BaseDiagnosticStrategy):
    strategy_id = "hypothesis_first"
    strategy_version = "hypothesis-first.v1"
    description = "Form bounded hypotheses, then choose discriminating probes."
    guidance = "State no more than the configured candidate hypotheses, then choose probes that distinguish them."

    def plan_initial_probes(self, **context: Any) -> list[str]:
        available = set(context.get("available_probe_ids") or [])
        maximum = int((context.get("strategy_params") or {}).get("max_hypotheses", 3))
        maximum = max(1, min(maximum, 8))
        ordered = choose_probe_ids(str(context.get("symptom") or ""))
        return [item for item in ordered if item in available][:maximum]
