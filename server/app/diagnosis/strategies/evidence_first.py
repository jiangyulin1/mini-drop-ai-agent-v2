from __future__ import annotations

from typing import Any

from server.app.diagnosis.strategies.base import BaseDiagnosticStrategy


class EvidenceFirstStrategy(BaseDiagnosticStrategy):
    strategy_id = "evidence_first"
    strategy_version = "evidence-first.v1"
    description = "Collect low-cost breadth evidence before root-cause synthesis."
    guidance = "Start with low-cost breadth evidence and defer attribution until the baseline is observed."

    def plan_initial_probes(self, **context: Any) -> list[str]:
        available = set(context.get("available_probe_ids") or [])
        return [item for item in ("host_process_metrics", "process_log_scan") if item in available]
