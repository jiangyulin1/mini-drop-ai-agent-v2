"""Registered recovery actions and deterministic preflight policy.

This phase deliberately exposes evaluation only.  No action in the registry
is executable until an Actuation Gateway, rollback executor and verification
loop exist.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from server.app.diagnosis.authorization import (
    AuthorizationDecision,
    ImpactLevel,
    OperationClass,
)
from server.app.diagnosis.schemas import StrictModel


class ActionDefinition(StrictModel):
    action_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    title: str = Field(min_length=1, max_length=256)
    operation_class: OperationClass = OperationClass.CHANGE
    base_impact_level: ImpactLevel
    allowed_environments: list[str] = Field(default_factory=list, max_length=16)
    max_targets: int = Field(default=1, ge=1, le=100)
    min_healthy_replicas: int = Field(default=0, ge=0, le=10_000)
    reversible: bool
    dry_run_supported: bool
    preflight_checks: list[str] = Field(default_factory=list, max_length=32)
    rollback_action_id: Optional[str] = Field(default=None, max_length=128)
    implementation_status: str = Field(default="policy_only", pattern=r"^(policy_only|executable)$")
    version: str = Field(default="1.0", min_length=1, max_length=32)


class ActionEvaluationRequest(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=64)
    target_count: int = Field(default=1, ge=1, le=10_000)
    healthy_replicas_after_action: int = Field(default=0, ge=0, le=100_000)
    change_freeze: bool = False
    rollback_ready: bool = False
    dry_run_passed: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionPolicyDecision(StrictModel):
    action_id: str
    decision: AuthorizationDecision
    operation_class: OperationClass
    impact_level: ImpactLevel
    executable: bool
    reason_codes: list[str] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)


class ActionRegistry:
    def __init__(self, definitions: list[ActionDefinition]):
        self._definitions = {item.action_id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("Action Registry 中 action_id 重复")
        missing_rollbacks = {
            item.rollback_action_id
            for item in definitions
            if item.rollback_action_id and item.rollback_action_id not in self._definitions
        }
        if missing_rollbacks:
            raise ValueError(f"Action Registry 缺少回滚动作: {sorted(missing_rollbacks)}")

    def get(self, action_id: str) -> Optional[ActionDefinition]:
        return self._definitions.get(action_id)

    def list(self) -> list[ActionDefinition]:
        return sorted(self._definitions.values(), key=lambda item: item.action_id)


DEFAULT_ACTION_REGISTRY = ActionRegistry([
    ActionDefinition(
        action_id="mini-drop.cleanup-expired-cache",
        title="清理 Mini-Drop 自身过期诊断缓存",
        base_impact_level=ImpactLevel.I1,
        allowed_environments=["development", "staging", "production"],
        max_targets=1,
        reversible=True,
        dry_run_supported=True,
        preflight_checks=["ownership", "retention_expired", "path_scope", "active_case_reference"],
        rollback_action_id="mini-drop.restore-cache-quarantine",
        implementation_status="executable",
    ),
    ActionDefinition(
        action_id="service.drain-unhealthy-instance",
        title="临时摘除单个不健康无状态实例",
        base_impact_level=ImpactLevel.I2,
        allowed_environments=["staging", "production"],
        max_targets=1,
        min_healthy_replicas=2,
        reversible=True,
        dry_run_supported=True,
        preflight_checks=[
            "stateless_proof", "health_failure", "topology_freshness", "remaining_capacity",
            "concurrent_change", "change_freeze", "rollback_route",
        ],
        rollback_action_id="service.restore-instance-traffic",
    ),
    ActionDefinition(
        action_id="service.restart-single-stateless-instance",
        title="重启单个异常无状态实例",
        base_impact_level=ImpactLevel.I2,
        allowed_environments=["staging", "production"],
        max_targets=1,
        min_healthy_replicas=2,
        reversible=False,
        dry_run_supported=True,
        preflight_checks=[
            "stateless_proof", "topology_freshness", "remaining_capacity", "restart_budget",
            "concurrent_change", "change_freeze",
        ],
    ),
    ActionDefinition(
        action_id="service.rollback-registered-feature-flag",
        title="回滚已登记 Feature Flag 到最近健康值",
        base_impact_level=ImpactLevel.I2,
        allowed_environments=["staging", "production"],
        max_targets=1,
        reversible=True,
        dry_run_supported=True,
        preflight_checks=[
            "registered_flag", "known_healthy_value", "change_correlation", "change_freeze",
            "rollback_value",
        ],
        rollback_action_id="service.restore-feature-flag-value",
    ),
    ActionDefinition(
        action_id="mini-drop.restore-cache-quarantine",
        title="从隔离区恢复 Mini-Drop 诊断缓存",
        base_impact_level=ImpactLevel.I1,
        allowed_environments=["development", "staging", "production"],
        max_targets=1,
        reversible=False,
        dry_run_supported=True,
        preflight_checks=["quarantine_entry", "path_scope", "destination_available"],
        implementation_status="executable",
    ),
    ActionDefinition(
        action_id="service.restore-instance-traffic",
        title="恢复单个实例流量",
        base_impact_level=ImpactLevel.I2,
        allowed_environments=["staging", "production"],
        max_targets=1,
        min_healthy_replicas=1,
        reversible=True,
        dry_run_supported=True,
        preflight_checks=["instance_health", "route_snapshot", "capacity"],
        rollback_action_id="service.drain-unhealthy-instance",
    ),
    ActionDefinition(
        action_id="service.restore-feature-flag-value",
        title="恢复 Feature Flag 变更前值",
        base_impact_level=ImpactLevel.I2,
        allowed_environments=["staging", "production"],
        max_targets=1,
        reversible=True,
        dry_run_supported=True,
        preflight_checks=["registered_flag", "previous_value", "change_freeze"],
        rollback_action_id="service.rollback-registered-feature-flag",
    ),
])


def evaluate_action(
    action_id: str,
    request: ActionEvaluationRequest,
    *,
    registry: ActionRegistry = DEFAULT_ACTION_REGISTRY,
) -> ActionPolicyDecision:
    definition = registry.get(action_id)
    if definition is None:
        return ActionPolicyDecision(
            action_id=action_id,
            decision=AuthorizationDecision.DENIED,
            operation_class=OperationClass.CHANGE,
            impact_level=ImpactLevel.I4,
            executable=False,
            reason_codes=["ACTION_NOT_REGISTERED"],
        )

    reasons: list[str] = []
    controls = list(definition.preflight_checks)
    impact = definition.base_impact_level
    if request.environment not in definition.allowed_environments:
        reasons.append("ENVIRONMENT_NOT_ALLOWED")
    if request.target_count > definition.max_targets:
        reasons.append("TARGET_LIMIT_EXCEEDED")
        impact = max(impact, ImpactLevel.I3, key=lambda item: int(item.value[1:]))
    if request.healthy_replicas_after_action < definition.min_healthy_replicas:
        reasons.append("INSUFFICIENT_HEALTHY_REPLICAS")
        impact = ImpactLevel.I4
    if request.change_freeze:
        reasons.append("CHANGE_FREEZE_ACTIVE")
    if definition.dry_run_supported and not request.dry_run_passed:
        reasons.append("DRY_RUN_REQUIRED")
    if definition.reversible and not request.rollback_ready:
        reasons.append("ROLLBACK_NOT_READY")
    if definition.implementation_status != "executable":
        reasons.append("ACTION_POLICY_ONLY_NOT_EXECUTABLE")

    hard_denials = {"ENVIRONMENT_NOT_ALLOWED", "TARGET_LIMIT_EXCEEDED", "INSUFFICIENT_HEALTHY_REPLICAS"}
    if hard_denials.intersection(reasons):
        decision = AuthorizationDecision.DENIED
    elif "CHANGE_FREEZE_ACTIVE" in reasons:
        decision = AuthorizationDecision.CHANGE_APPROVAL
    else:
        # Even a perfect preflight cannot auto-execute while the implementation
        # remains policy_only and no action Grant/Actuation Gateway exists.
        decision = AuthorizationDecision.USER_APPROVAL
    return ActionPolicyDecision(
        action_id=action_id,
        decision=decision,
        operation_class=definition.operation_class,
        impact_level=impact,
        executable=definition.implementation_status == "executable" and not reasons,
        reason_codes=reasons or ["PREFLIGHT_PASSED"],
        required_controls=controls,
    )
