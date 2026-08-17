from __future__ import annotations

from server.app.diagnosis.strategies.evidence_first import EvidenceFirstStrategy


class HybridStrategy(EvidenceFirstStrategy):
    strategy_id = "hybrid"
    strategy_version = "hybrid.v1"
    description = "Breadth-first baseline followed by adaptive information gain."
    guidance = (
        "Begin with low-cost breadth evidence; on later cycles choose the registered probe "
        "with the highest information gain for the remaining evidence gap."
    )
