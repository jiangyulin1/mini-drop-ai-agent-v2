"""多原因因果图：根因实体 → 受影响实体，含传播边与每原因 EvidenceContract 覆盖率。

结构对齐实施方案 3.6：
- primary_cause / contributing_causes / amplifiers / propagation_edges / ruled_out_causes；
- 每个原因单独计算其 EvidenceContract 的必需事实覆盖率，不能用一份证据重复支持。
"""

from __future__ import annotations

from typing import Any, Literal

from server.app.diagnosis.evidence_contracts import (
    contracts_for_hypothesis,
    get_contract,
    missing_facts,
)
from server.app.diagnosis.schemas import StrictModel

CLASSIFICATION_TO_MECHANISM = {
    "self_code_or_process_pressure": "cpu_saturation",
    "host_resource_contention": "same_host_noisy_neighbor",
    "same_host_noisy_neighbor": "same_host_noisy_neighbor",
    "downstream_dependency": "downstream_dependency",
    "network_degradation": "network_degradation",
    "runtime_lock_contention": "runtime_lock_contention",
    "runtime_stall": "runtime_stall",
    "process_oom": "process_oom",
    "filesystem_exhaustion": "filesystem_exhaustion",
    "compound_incident": "compound_incident",
}


class CausalNode(StrictModel):
    entity: str = ...  # type: ignore[name-defined]
    node_type: Literal["root", "affected", "amplifier"] = "affected"
    mechanism: str = "unknown"
    contract_coverage: float = 0.0
    missing_facts: list[str] = list  # type: ignore[assignment]


class CausalEdge(StrictModel):
    source: str
    target: str
    relation: Literal["propagates_to", "amplifies", "rules_out"] = "propagates_to"
    confidence: Literal["high", "medium", "low"] = "medium"


def contract_coverage(
    mechanism: str,
    facts: dict[str, Any],
) -> tuple[float, list[str]]:
    """返回该机制 EvidenceContract 的必需事实覆盖率与缺失事实。

    优先按 mechanism 查契约；回退到按 hypothesis 类型匹配。
    """
    contracts = []
    try:
        contracts = [get_contract(mechanism)]
    except ValueError:
        contracts = contracts_for_hypothesis(mechanism)
    if not contracts:
        return 0.0, []
    best_missing: list[str] = []
    best_ratio = 0.0
    for contract in contracts:
        missing = missing_facts(contract, facts)
        required = len(contract.required_facts)
        if required == 0:
            continue
        ratio = (required - len(missing)) / required
        if ratio > best_ratio:
            best_ratio = ratio
            best_missing = missing
    return round(best_ratio, 3), best_missing


def build_causal_graph(
    assessment: dict[str, Any],
    facts: dict[str, Any],
    *,
    identity_entities: list[str] | None = None,
) -> dict[str, Any]:
    """由集群归因结论构建因果图。

    ``facts`` 为当前已收集的扁平事实（用于 per-cause EvidenceContract 覆盖率）。
    ``identity_entities`` 为身份图中的稳定实体集合，用于把根因实体锚定到图上。
    """
    classification = str(assessment.get("classification") or "")
    root_entity = assessment.get("root_entity") or (
        (assessment.get("root_location") or {}).get("target_ref")
    )
    mechanism = CLASSIFICATION_TO_MECHANISM.get(classification, classification)
    coverage, missing = contract_coverage(mechanism, facts)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    root = str(root_entity or "unknown")
    nodes.append({
        "entity": root,
        "node_type": "root",
        "mechanism": mechanism,
        "contract_coverage": coverage,
        "missing_facts": missing,
    })

    # 贡献原因（复合事故的每个原因独立算覆盖率）
    contributing = assessment.get("contributing_causes") or []
    for cause in contributing:
        cause_mech = CLASSIFICATION_TO_MECHANISM.get(
            str(cause.get("classification") or ""),
            str(cause.get("subtype") or "unknown"),
        )
        cause_coverage, cause_missing = contract_coverage(cause_mech, facts)
        nodes.append({
            "entity": str(cause.get("target_ref") or root),
            "node_type": "amplifier" if classification == "compound_incident" else "root",
            "mechanism": cause_mech,
            "contract_coverage": cause_coverage,
            "missing_facts": cause_missing,
        })
        if str(cause.get("target_ref") or root) != root:
            edges.append({
                "source": str(cause.get("target_ref") or root),
                "target": root,
                "relation": "propagates_to",
                "confidence": str(cause.get("confidence_level") or "medium").lower(),
            })

    # 受影响实体：身份图中 root 的邻居 / 同宿主 / 下游
    affected = assessment.get("compared_targets") or []
    for target in affected:
        entity = str(target.get("service_id") or target.get("instance_id") or "unknown")
        if entity == root:
            continue
        nodes.append({"entity": entity, "node_type": "affected", "mechanism": "unknown",
                      "contract_coverage": 0.0, "missing_facts": []})
        edges.append({
            "source": root, "target": entity, "relation": "propagates_to", "confidence": "medium",
        })

    # 已排除原因
    for ruled_out in assessment.get("ruled_out") or []:
        edges.append({
            "source": root,
            "target": str(ruled_out.get("hypothesis") or "unknown"),
            "relation": "rules_out",
            "confidence": "medium",
        })

    return {
        "schema_version": "causal-graph.v1",
        "primary_cause": nodes[0] if nodes else {},
        "contributing_causes": [n for n in nodes[1:] if n["node_type"] != "affected"],
        "propagation_edges": [e for e in edges if e["relation"] == "propagates_to"],
        "ruled_out_causes": [e for e in edges if e["relation"] == "rules_out"],
        "nodes": nodes,
        "edges": edges,
        "identity_entities": identity_entities or [],
    }
