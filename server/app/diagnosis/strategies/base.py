"""Protocol and common mechanics for deterministic and Pi strategies."""

from __future__ import annotations

from typing import Any, Protocol

from server.app.diagnosis.investigation_directive import InvestigationDirective, build_directive


class DiagnosticStrategy(Protocol):
    strategy_id: str
    strategy_version: str
    description: str

    def build_directive(self, **context: Any) -> InvestigationDirective: ...
    def plan_initial_probes(self, **context: Any) -> list[str]: ...
    def select_next_probes(self, **context: Any) -> list[str]: ...
    def should_stop(self, **context: Any) -> dict[str, Any]: ...
    def render_prompt_guidance(self) -> str: ...


class BaseDiagnosticStrategy:
    strategy_id = "base"
    strategy_version = "strategy.v1"
    description = "Base evidence-driven strategy"
    guidance = "Use registered evidence and choose the smallest discriminating next step."

    def build_directive(self, **context: Any) -> InvestigationDirective:
        directive = build_directive(
            goal=str(context.get("goal") or ""),
            target_scope=context.get("target_scope") or {},
            evidence_summary=context.get("evidence_summary") or [],
            skill_context=context.get("skill_context") or [],
            missing_facts=context.get("missing_facts") or [],
        )
        return directive.model_copy(update={
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_guidance": self.render_prompt_guidance(),
            "strategy_params": dict(context.get("strategy_params") or {}),
        })

    def plan_initial_probes(self, **context: Any) -> list[str]:
        available = list(context.get("available_probe_ids") or [])
        return available[:2]

    def select_next_probes(self, **context: Any) -> list[str]:
        candidates = list(context.get("candidate_probe_ids") or [])
        completed = set(context.get("completed_probe_ids") or [])
        return [item for item in candidates if item not in completed][:2]

    def should_stop(self, **context: Any) -> dict[str, Any]:
        missing = list(context.get("missing_facts") or [])
        progress = int(context.get("no_progress_cycles") or 0)
        stop = not missing or progress >= 2
        return {
            "stop": stop,
            "reason": "evidence_complete" if not missing else (
                "no_effective_progress" if progress >= 2 else "continue"
            ),
        }

    def render_prompt_guidance(self) -> str:
        return self.guidance
