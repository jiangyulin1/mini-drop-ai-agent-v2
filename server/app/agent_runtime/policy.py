"""Per-turn RuntimePolicy with a code-owned maximum permission boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from server.app.agent_runtime.catalog import (
    PROPOSE_ONLY_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    TOOL_CATALOG_BY_NAME,
    WRITE_TOOL_NAMES,
)


class RuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side_effect_policy: Literal["READ_ONLY", "PROPOSE_ONLY", "AUTO_READ_LOW"] = "AUTO_READ_LOW"
    enabled_tools: frozenset[str] | None = None
    disabled_tools: frozenset[str] = Field(default_factory=frozenset)
    enabled_operations: frozenset[str] | None = None
    allowed_risk_levels: frozenset[str] = Field(default_factory=lambda: frozenset({"R0", "R1"}))
    execution_mode: Literal["normal", "dry_run", "sandbox", "deny_write"] = "normal"
    auto_approve: bool = False
    require_approval_for: frozenset[str] = Field(default_factory=lambda: frozenset({"R2", "R3"}))
    allow_arbitrary_command: bool = False

    @field_validator("enabled_tools", "disabled_tools", "enabled_operations", mode="before")
    @classmethod
    def normalize_sets(cls, value):
        if value is None:
            return None
        return frozenset(str(item) for item in value)

    @field_validator("allowed_risk_levels", "require_approval_for", mode="before")
    @classmethod
    def normalize_risks(cls, value):
        return frozenset(str(item).upper() for item in (value or []))

    @model_validator(mode="after")
    def enforce_code_boundary(self):
        known = set(TOOL_CATALOG_BY_NAME)
        requested = set(self.enabled_tools or ()) | set(self.disabled_tools)
        unknown = requested - known
        if unknown:
            raise ValueError(f"UNREGISTERED_RUNTIME_TOOLS:{','.join(sorted(unknown))}")
        if self.allow_arbitrary_command:
            raise ValueError("ARBITRARY_COMMAND_NEVER_ALLOWED")
        if not set(self.allowed_risk_levels).issubset({"R0", "R1"}):
            raise ValueError("RUNTIME_POLICY_CANNOT_EXPAND_RISK_BOUNDARY")
        if not set(self.require_approval_for).issubset({"R0", "R1", "R2", "R3"}):
            raise ValueError("UNKNOWN_RISK_LEVEL")
        return self

    def effective_tools(self) -> frozenset[str]:
        if self.side_effect_policy == "READ_ONLY":
            maximum = set(READ_ONLY_TOOL_NAMES)
        elif self.side_effect_policy == "PROPOSE_ONLY":
            maximum = set(READ_ONLY_TOOL_NAMES | PROPOSE_ONLY_TOOL_NAMES)
        else:
            maximum = set(READ_ONLY_TOOL_NAMES | PROPOSE_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES)
        if self.enabled_tools is not None:
            maximum &= set(self.enabled_tools)
        maximum -= set(self.disabled_tools)
        return frozenset(maximum)

    def allows_tool(self, tool_name: str) -> bool:
        return tool_name in self.effective_tools()

    def allows_operation(self, operation_id: str, risk: str) -> bool:
        if self.enabled_operations is not None and operation_id not in self.enabled_operations:
            return False
        return str(risk).upper().replace("READ_LOW", "R1") in self.allowed_risk_levels

    def audit_summary(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json"),
            "effective_tools": sorted(self.effective_tools()),
            "permission_boundary": "request_may_only_reduce_code_owned_permissions",
        }


def resolve_runtime_policy(
    value: RuntimePolicy | dict[str, Any] | None,
    *,
    experiment_mode: bool = False,
) -> RuntimePolicy:
    if isinstance(value, dict):
        allowed = set(RuntimePolicy.model_fields)
        value = {key: item for key, item in value.items() if key in allowed}
    policy = value if isinstance(value, RuntimePolicy) else RuntimePolicy.model_validate(value or {})
    if policy.auto_approve and not experiment_mode:
        raise ValueError("AUTO_APPROVE_EXPERIMENT_ONLY")
    if policy.auto_approve and "R3" not in policy.require_approval_for:
        raise ValueError("R3_APPROVAL_CANNOT_BE_DISABLED")
    return policy


_POLICY_RANK = {"READ_ONLY": 0, "PROPOSE_ONLY": 1, "AUTO_READ_LOW": 2}


def constrain_side_effect_policy(policy: RuntimePolicy, ceiling: str) -> RuntimePolicy:
    """Apply the stricter of route intent and requested RuntimePolicy."""
    requested = policy.side_effect_policy
    effective = min((requested, ceiling), key=lambda item: _POLICY_RANK[item])
    return policy.model_copy(update={"side_effect_policy": effective})
