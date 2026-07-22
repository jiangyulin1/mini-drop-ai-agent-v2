"""Mini-Drop 诊断流水线的稳定节点契约。"""

from __future__ import annotations

from typing import Final


PIPELINE_VERSION: Final = "diagnosis-pipeline-v2"

PIPELINE_NODES: Final[tuple[str, ...]] = (
    "understand_intent",
    "resolve_scope",
    "build_hypotheses",
    "plan_evidence",
    "risk_gate",
    "run_probes",
    "normalize_evidence",
    "analyze_evidence",
    "assess_cluster",
    "retrieve_knowledge",
    "generate_actions",
    "verify_report",
)


def node_run_id(diagnosis_id: str, node_name: str) -> str:
    if node_name not in PIPELINE_NODES:
        raise ValueError(f"未知诊断节点: {node_name}")
    return f"{diagnosis_id}:{node_name}"

