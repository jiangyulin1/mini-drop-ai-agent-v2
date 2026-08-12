"""Adaptive Investigation Planner：按活跃候选的证据契约缺失事实生成下一步采集。

替代"症状直接决定探针"的静态路径：每个根因机制声明 EvidenceContract，
planner 把契约的缺失事实映射为受控探针动作，复用信息增益排序器
``rank_investigation_actions`` 选出最小充分动作。只读 R1 优先；R2 仅在
无 R1 可区分时由风险预算控制。
"""

from __future__ import annotations

from typing import Any

from server.app.diagnosis.evidence_contracts import (
    EvidenceContract,
    contracts_for_hypothesis,
    contracts_for_symptom,
    missing_facts,
    probe_supplies_facts,
)
from server.app.diagnosis.investigation_planner import (
    InvestigationActionCandidate,
    rank_investigation_actions,
)
from server.app.diagnosis.schemas import ProbeDefinition

# 每个契约确认所需的最低独立证据来源族数；低于该值说明还需要跨源补证。
RELIABILITY_BY_RISK = {"R0": 0.95, "R1": 0.9, "R2": 0.85, "R3": 0.7}
RISK_COST = {"R0": 0.0, "R1": 0.1, "R2": 0.6, "R3": 1.0}


def _active_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item for item in (hypotheses or [])
        if item.get("status") not in {"RULED_OUT", "WEAKENED"}
        and item.get("hypothesis_id") != "OTHER_UNKNOWN"
    ]


def _present_facts(observations: list[dict[str, Any]]) -> set[str]:
    present: set[str] = set()
    for observation in observations or []:
        facts = observation.get("facts") or {}
        present.update(facts.keys())
        present.update((observation.get("pressure") or {}).keys())
    return present


def _relevant_contracts(
    symptom: str,
    active: list[dict[str, Any]],
) -> list[EvidenceContract]:
    if active:
        contracts = []
        seen: set[str] = set()
        for hypothesis in active:
            for contract in contracts_for_hypothesis(str(hypothesis.get("type") or "")):
                if contract.mechanism not in seen:
                    seen.add(contract.mechanism)
                    contracts.append(contract)
        if contracts:
            return contracts
    return contracts_for_symptom(symptom)


def _primary_target(
    targets: list[dict[str, Any]],
    active: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    affected = set()
    for hypothesis in active:
        affected.update(str(item) for item in (hypothesis.get("affected_targets") or []))
    if affected:
        for target in targets:
            if target.get("instance_id") in affected:
                return target
    # 压力最大的观测目标优先补证。
    pressured = None
    pressured_score = -1
    for observation in observations or []:
        score = sum(1 for value in (observation.get("pressure") or {}).values() if value)
        target = observation.get("target") or {}
        if score > pressured_score and target:
            pressured_score = score
            pressured = target
    if pressured:
        for target in targets:
            if target.get("instance_id") == pressured.get("instance_id"):
                return target
    return targets[0] if targets else None


def build_probe_candidates(
    *,
    symptom: str,
    hypotheses: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    scope: dict[str, Any],
    available_probes: list[ProbeDefinition],
    targets: list[dict[str, Any]],
    round_number: int,
    connection_endpoints: list[dict[str, Any]] | None = None,
    allow_r2: bool = False,
    present_facts: set[str] | list[str] | None = None,
) -> list[InvestigationActionCandidate]:
    """为活跃候选的缺失契约事实生成可排序的探针动作。

    ``available_probes`` 必须是已通过 Agent 能力/风险预算过滤的探针定义，
    本函数不再重复做越权过滤。``present_facts`` 显式给出已收集的事实键
    （多轮调查时由会话持久化的事实集传入），缺省从 observations 推导。
    """
    active = _active_hypotheses(hypotheses)
    present = set(present_facts) if present_facts is not None else _present_facts(observations)
    contracts = _relevant_contracts(symptom, active)
    available_by_id = {item.probe_id: item for item in available_probes}
    if not available_by_id:
        return []

    # 每个契约的缺失事实，以及所有契约的缺失事实（跨假设聚合）。
    contract_missing: list[tuple[EvidenceContract, list[str]]] = []
    for contract in contracts:
        missing = missing_facts(contract, present)
        if missing:
            contract_missing.append((contract, missing))
    if not contract_missing:
        return []

    # 活跃假设类型集，用于假设区分度。
    active_types = {str(item.get("type") or "") for item in active}
    if not active_types:
        active_types = {item.mechanism for item in contracts}

    candidates: list[InvestigationActionCandidate] = []
    for contract, missing in contract_missing:
        for probe_id in contract.candidate_probes:
            definition = available_by_id.get(probe_id)
            if definition is None:
                continue
            supplied = probe_supplies_facts(probe_id) & set(missing)
            if not supplied:
                continue
            if definition.risk_level == "R2" and not allow_r2:
                continue
            gain = len(supplied) / max(len(missing), 1)
            # 假设区分度：该探针参与解决多少个活跃假设的契约。
            discriminating = sum(
                1
                for hypothesis in active
                if any(
                    probe_id in c.candidate_probes
                    for c in contracts_for_hypothesis(str(hypothesis.get("type") or ""))
                )
            )
            discrimination = (
                discriminating / len(active_types) if active_types else 1.0
            )
            duration = min(
                definition.default_duration_seconds, definition.max_duration_seconds,
            )
            target = _primary_target(targets, active, observations) or {}
            parameters: dict[str, Any] = {
                "target": target,
                "duration_sec": duration,
                "sample_rate": definition.default_sample_rate,
                "round_number": round_number,
                "missing_facts": sorted(supplied),
                "contract_mechanisms": sorted({item.mechanism for item, _ in contract_missing}),
            }
            if probe_id == "endpoint_connectivity_probe" and connection_endpoints:
                parameters["endpoints"] = connection_endpoints
            candidates.append(InvestigationActionCandidate(
                action_id=f"probe:{probe_id}",
                source_id=probe_id,
                operation="probe.collect",
                expected_information_gain=round(gain, 6),
                source_reliability=RELIABILITY_BY_RISK.get(definition.risk_level, 0.9),
                probability_of_success=1.0,
                hypothesis_discrimination=round(discrimination, 6),
                latency_cost=round(duration / 60.0, 6),
                resource_cost=round(duration / 120.0, 6),
                monetary_cost=0.0,
                risk_cost=RISK_COST.get(definition.risk_level, 0.1),
                approval_wait_cost=1.0 if definition.requires_approval else 0.0,
                parameters=parameters,
            ))
    return candidates


def select_probe_actions(
    candidates: list[InvestigationActionCandidate],
    *,
    max_actions: int = 1,
    exclude_probe_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """复用信息增益排序器返回 Top-N 探针动作（按 source_id 去重）。

    同一探针可能因多个契约各生成一条候选，只保留效用最高的一条。
    """
    ranked = rank_investigation_actions(candidates)
    if not ranked:
        return []
    excluded = exclude_probe_ids or set()
    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for item in ranked:
        source = str(item["source_id"])
        if source in excluded or source in seen:
            continue
        seen.add(source)
        selected.append(item)
        if len(selected) >= max(1, max_actions):
            break
    return selected


def uncovered_mechanisms(
    symptom: str,
    hypotheses: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> list[str]:
    """所有相关契约中尚未满足的机制，用于解释为什么继续补证。"""
    active = _active_hypotheses(hypotheses)
    present = _present_facts(observations)
    uncovered: list[str] = []
    for contract in _relevant_contracts(symptom, active):
        if missing_facts(contract, present):
            uncovered.append(contract.mechanism)
    return uncovered
