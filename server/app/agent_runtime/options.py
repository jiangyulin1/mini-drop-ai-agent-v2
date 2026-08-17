"""Model/runtime tuning separated from diagnostic strategy and permissions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RuntimeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(default="hybrid", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "high"
    model: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_variant: Literal["default", "concise", "evidence_strict"] = "default"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=128, le=65536)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    capture_reasoning_trace: bool = False

    def audit_summary(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json"),
            "capture_reasoning_trace": False,
            "reasoning_persistence": "decision_summary_and_tool_sequence_only",
            "runtime_support": {
                "strategy": "applied",
                "model": "applied",
                "reasoning_effort": "applied",
                "prompt_variant": "applied",
                "temperature": "experiment_metadata_only",
                "max_tokens": "experiment_metadata_only",
                "seed": "experiment_metadata_only",
            },
        }


def resolve_runtime_options(
    value: RuntimeOptions | dict[str, Any] | None,
    *,
    experiment_mode: bool = False,
) -> RuntimeOptions:
    options = value if isinstance(value, RuntimeOptions) else RuntimeOptions.model_validate(value or {})
    from server.app.diagnosis.strategies.registry import get_strategy

    get_strategy(options.strategy_id)
    if options.capture_reasoning_trace and not experiment_mode:
        raise ValueError("REASONING_TRACE_EXPERIMENT_ONLY")
    return options
