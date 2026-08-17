from __future__ import annotations

from typing import Any

from server.app.diagnosis.strategies.base import BaseDiagnosticStrategy


class CausalGraphStrategy(BaseDiagnosticStrategy):
    strategy_id = "causal_graph"
    strategy_version = "causal-graph.v1"
    description = "Build candidate causal edges and verify them one by one."
    guidance = "Represent candidate causes as bounded causal edges; verify each edge with registered Evidence."

    def plan_initial_probes(self, **context: Any) -> list[str]:
        available = set(context.get("available_probe_ids") or [])
        order = (
            "host_process_metrics", "endpoint_connectivity_probe",
            "runtime_thread_snapshot", "process_log_scan",
        )
        return [item for item in order if item in available][:3]
