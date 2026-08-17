from __future__ import annotations

from typing import Any

from server.app.diagnosis.strategies.base import BaseDiagnosticStrategy


class ExploratoryStrategy(BaseDiagnosticStrategy):
    strategy_id = "exploratory"
    strategy_version = "exploratory.v1"
    description = "Broader registered-probe coverage within the same policy boundary."
    guidance = "Explore broadly, but only with registered tools and within the supplied risk and budget limits."

    def plan_initial_probes(self, **context: Any) -> list[str]:
        return list(context.get("available_probe_ids") or [])
