from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.app.agent_runtime.catalog import (
    PROPOSE_ONLY_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    TOOL_CATALOG,
)
from server.app.agent_runtime.options import RuntimeOptions, resolve_runtime_options
from server.app.agent_runtime.policy import (
    RuntimePolicy,
    constrain_side_effect_policy,
    resolve_runtime_policy,
)
from server.app.diagnosis.actuation import ActuationError, enforce_runtime_execution_policy
from server.app.diagnosis.strategies.registry import (
    STRATEGY_REGISTRY,
    get_strategy,
    normalize_strategy_id,
)


def test_canonical_tool_catalog_is_unique_and_policy_partitioned():
    names = [item.name for item in TOOL_CATALOG]
    assert len(names) == len(set(names)) == 22
    assert READ_ONLY_TOOL_NAMES.isdisjoint(PROPOSE_ONLY_TOOL_NAMES)
    assert "propose_collection" in PROPOSE_ONLY_TOOL_NAMES
    assert "discover_topology" in PROPOSE_ONLY_TOOL_NAMES
    assert "get_case_snapshot" in READ_ONLY_TOOL_NAMES
    assert "get_dependency_graph" in READ_ONLY_TOOL_NAMES
    assert "evaluate_hypotheses" not in names
    assert "rca_candidate_analysis" not in names


def test_runtime_policy_can_only_shrink_the_code_owned_boundary():
    policy = RuntimePolicy(
        side_effect_policy="PROPOSE_ONLY",
        enabled_tools={"get_case_snapshot", "propose_collection"},
        disabled_tools={"propose_collection"},
    )
    assert policy.effective_tools() == {"get_case_snapshot"}
    assert constrain_side_effect_policy(policy, "READ_ONLY").side_effect_policy == "READ_ONLY"
    with pytest.raises(ValidationError, match="ARBITRARY_COMMAND_NEVER_ALLOWED"):
        RuntimePolicy(allow_arbitrary_command=True)
    with pytest.raises(ValidationError, match="CANNOT_EXPAND_RISK_BOUNDARY"):
        RuntimePolicy(allowed_risk_levels={"R0", "R3"})


def test_runtime_policy_resolution_ignores_audit_derived_fields():
    base = RuntimePolicy(side_effect_policy="PROPOSE_ONLY")
    audit = base.audit_summary()
    assert "effective_tools" in audit and "permission_boundary" in audit
    resolved = resolve_runtime_policy(audit)
    assert resolved.side_effect_policy == "PROPOSE_ONLY"
    assert resolved.audit_summary()["effective_tools"] == sorted(base.effective_tools())


def test_experimental_flags_are_rejected_in_production_resolution():
    with pytest.raises(ValueError, match="AUTO_APPROVE_EXPERIMENT_ONLY"):
        resolve_runtime_policy({"auto_approve": True})
    with pytest.raises(ValueError, match="REASONING_TRACE_EXPERIMENT_ONLY"):
        resolve_runtime_options({"capture_reasoning_trace": True})
    options = resolve_runtime_options(
        {"strategy_id": "evidence_first", "capture_reasoning_trace": True},
        experiment_mode=True,
    )
    assert options.strategy_id == "evidence_first"
    assert options.audit_summary()["capture_reasoning_trace"] is False


def test_all_diagnostic_strategies_share_the_contract_but_keep_distinct_guidance():
    assert set(STRATEGY_REGISTRY) == {
        "rule_tree", "hypothesis_first", "evidence_first",
        "causal_graph", "exploratory", "hybrid",
    }
    assert normalize_strategy_id("CONSTRAINED_HYBRID") == "hybrid"
    guidance = set()
    for strategy_id in STRATEGY_REGISTRY:
        strategy = get_strategy(strategy_id)
        directive = strategy.build_directive(
            goal="locate the root cause",
            target_scope={"service_id": "checkout"},
            strategy_params={"breadth": 2},
        )
        assert directive.strategy_id == strategy_id
        assert directive.strategy_params == {"breadth": 2}
        guidance.add(strategy.render_prompt_guidance())
    assert len(guidance) == len(STRATEGY_REGISTRY)


def test_runtime_options_validate_registered_strategy():
    assert RuntimeOptions().strategy_id == "hybrid"
    with pytest.raises(ValueError, match="UNREGISTERED_DIAGNOSTIC_STRATEGY"):
        resolve_runtime_options({"strategy_id": "made_up_strategy"})
    with pytest.raises(ValueError, match="DIAGNOSTIC_STRATEGY_EXPERIMENT_ONLY:rule_tree"):
        resolve_runtime_options({"strategy_id": "rule_tree"})
    assert resolve_runtime_options(
        {"strategy_id": "rule_tree"}, experiment_mode=True,
    ).strategy_id == "rule_tree"


def test_native_actuation_honors_optional_execution_policy():
    enforce_runtime_execution_policy(None, action_id="safe.action", risk_level="R1")
    enforce_runtime_execution_policy(
        {"enabled_operations": ["safe.action"], "allowed_risk_levels": ["R1"]},
        action_id="safe.action",
        risk_level="R1",
    )
    with pytest.raises(ActuationError, match="ACTION_BLOCKED_BY_EXECUTION_MODE"):
        enforce_runtime_execution_policy(
            {"execution_mode": "dry_run"}, action_id="safe.action", risk_level="R1",
        )
    with pytest.raises(ActuationError, match="ACTION_RISK_NOT_ALLOWED"):
        enforce_runtime_execution_policy({}, action_id="elevated.action", risk_level="R2")


def test_runtime_options_temperature_max_tokens_seed_are_explicit_metadata_only():
    audit = RuntimeOptions(temperature=0.2, max_tokens=2048, seed=7).audit_summary()
    assert audit["runtime_support"]["temperature"] == "experiment_metadata_only"
    assert audit["runtime_support"]["max_tokens"] == "experiment_metadata_only"
    assert audit["runtime_support"]["seed"] == "experiment_metadata_only"
