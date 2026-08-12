"""VerificationContract：每个 Case 创建时生成的恢复验证契约。

对照实施方案 6.3：
- primary_objectives：恢复目标（业务指标达到即恢复）；
- guardrails：保护指标（不可退化）；
- synthetic_checks：业务合成检查；
- sample_window_seconds / required_consecutive_passes / max_observation_seconds。

没有业务目标时允许用基础健康检查，但只能标记为 MITIGATED 而非完整解决。
"""

from __future__ import annotations

from typing import Any, Literal

from server.app.diagnosis.schemas import StrictModel

DEFAULT_WINDOW_SECONDS = 30
DEFAULT_CONSECUTIVE_PASSES = 2
DEFAULT_MAX_OBSERVATION_SECONDS = 300

_OPERATORS = {">=", "<=", ">", "<", "==", "!="}


class Objective(StrictModel):
    metric: str = ...  # type: ignore[name-defined]
    operator: Literal[">=", "<=", ">", "<", "==", "!="] = ">="
    value: float = 0.0
    kind: Literal["objective", "guardrail"] = "objective"


class VerificationContract(StrictModel):
    schema_version: str = "verification-contract.v1"
    case_id: str
    primary_objectives: list[Objective] = list  # type: ignore[assignment]
    guardrails: list[Objective] = list  # type: ignore[assignment]
    synthetic_checks: list[str] = list  # type: ignore[assignment]
    sample_window_seconds: int = DEFAULT_WINDOW_SECONDS
    required_consecutive_passes: int = DEFAULT_CONSECUTIVE_PASSES
    max_observation_seconds: int = DEFAULT_MAX_OBSERVATION_SECONDS
    has_business_objectives: bool = True

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_verification_contract(
    case_id: str,
    target_scope: dict[str, Any],
) -> VerificationContract:
    """由 Case 目标作用域生成恢复验证契约。

    优先读 target_scope.verification（含 http_checks / metrics / business_targets），
    缺省用基础健康检查（HTTP 200）作为保底，has_business_objectives=False。
    """
    verification = target_scope.get("verification") or {}
    http_checks = verification.get("http_checks") or []
    objectives: list[Objective] = []
    guardrails: list[Objective] = []

    business = verification.get("business_targets") or []
    for item in business:
        metric = str(item.get("metric") or "")
        operator = str(item.get("operator") or ">=")
        if not metric or operator not in _OPERATORS:
            continue
        try:
            value = float(item.get("value") or 0)
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind") or "objective")
        objective = Objective(
            metric=metric, operator=operator, value=value,
            kind="guardrail" if kind == "guardrail" else "objective",
        )
        if kind == "guardrail":
            guardrails.append(objective)
        else:
            objectives.append(objective)

    # HTTP 检查作为 synthetic_checks + 可达性目标。
    synthetic: list[str] = []
    for check in http_checks:
        url = str(check.get("url") or "")
        if url:
            synthetic.append(url)

    has_business = bool(objectives)
    if not has_business:
        # 无业务目标：以健康检查（HTTP 2xx）为保底客观目标。
        for url in synthetic:
            objectives.append(Objective(
                metric=f"http:{url}", operator="<", value=500.0, kind="objective",
            ))
        has_business = False

    if not guardrails:
        # 默认保护指标：错误率不得飙升、实例不得少于副本数。
        replicas = int(((target_scope.get("orchestration") or {}).get("replicas") or 1))
        guardrails.append(Objective(
            metric="healthy_replicas", operator=">=", value=max(1, replicas),
            kind="guardrail",
        ))

    return VerificationContract(
        case_id=case_id,
        primary_objectives=objectives,
        guardrails=guardrails,
        synthetic_checks=synthetic,
        sample_window_seconds=int(verification.get("sample_window_seconds") or DEFAULT_WINDOW_SECONDS),
        required_consecutive_passes=int(
            verification.get("required_consecutive_passes") or DEFAULT_CONSECUTIVE_PASSES,
        ),
        max_observation_seconds=int(
            verification.get("max_observation_seconds") or DEFAULT_MAX_OBSERVATION_SECONDS,
        ),
        has_business_objectives=has_business,
    )


def evaluate_verification(
    contract: VerificationContract,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """按契约评估一次正式测量的业务目标与保护指标。

    ``metrics`` 为本次测量的指标快照（如 {"http:...": 200, "healthy_replicas": 2,
    "error_rate": 0.01}）。
    """
    objective_results: list[dict[str, Any]] = []
    objectives_met = 0
    for objective in contract.primary_objectives:
        value = metrics.get(objective.metric)
        met = _compare(value, objective.operator, objective.value)
        objective_results.append({
            "metric": objective.metric,
            "operator": objective.operator,
            "expected": objective.value,
            "actual": value,
            "met": bool(met),
        })
        if met:
            objectives_met += 1

    guardrail_violations: list[dict[str, Any]] = []
    guardrails_ok = True
    for guardrail in contract.guardrails:
        value = metrics.get(guardrail.metric)
        if value is None:
            # 未测量的保护指标视为未知，不因缺失而误判违约。
            continue
        ok = _compare(value, guardrail.operator, guardrail.value)
        if not ok:
            guardrail_violations.append({
                "metric": guardrail.metric,
                "operator": guardrail.operator,
                "expected": guardrail.value,
                "actual": value,
            })
            guardrails_ok = False

    recovered = (
        objectives_met == len(contract.primary_objectives)
        and guardrails_ok
        and len(contract.primary_objectives) > 0
    )
    return {
        "contract_id": contract.case_id,
        "recovered": recovered,
        "objectives_met": objectives_met,
        "objectives_total": len(contract.primary_objectives),
        "objective_results": objective_results,
        "guardrail_violations": guardrail_violations,
        "guardrails_ok": guardrails_ok,
        "synthetic_checks": contract.synthetic_checks,
        "status": "RECOVERED" if recovered else (
            "MITIGATED" if not contract.has_business_objectives and guardrails_ok else "NOT_RECOVERED"
        ),
    }


def _compare(value: Any, operator: str, expected: float) -> bool:
    if value is None:
        return False
    try:
        actual = float(value)
    except (TypeError, ValueError):
        return False
    if operator == ">=":
        return actual >= expected
    if operator == "<=":
        return actual <= expected
    if operator == ">":
        return actual > expected
    if operator == "<":
        return actual < expected
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected
    return False
