"""Validated state transitions for the supervised investigation loop.

The model may propose hypotheses, gaps and causal relationships.  This service
keeps Case scope and Evidence authority in Mini-Drop and persists only bounded,
auditable revisions.
"""

from __future__ import annotations

import hashlib
from typing import Any


ACTIVE_HYPOTHESIS_STATES = {"PROPOSED", "ACTIVE", "CONFIRMED", "UNKNOWN"}
VALID_GAP_STATES = {"OPEN", "BLOCKING", "RESOLVED"}
VALID_CAUSAL_ROLES = {
    "PRIMARY_CAUSE", "PRIMARY_ROOT_CAUSE", "CONTRIBUTING_FACTOR", "AMPLIFIER",
    "PROPAGATED_EFFECT", "SYMPTOM", "COINCIDENTAL_ANOMALY", "UNKNOWN",
}
VALID_CAUSAL_RELATIONS = {"CAUSES", "CONTRIBUTES_TO", "AMPLIFIES", "PROPAGATES_TO", "CORRELATES_WITH"}


class InvestigationStateService:
    """One deterministic gateway for all model-proposed investigation state."""

    def __init__(self, repository: Any):
        self._repo = repository

    def snapshot(self, case_id: str, tenant_id: str) -> dict[str, Any]:
        hypotheses = self._repo.get_case_hypothesis_graph(case_id, tenant_id) or {
            "hypotheses": [], "edges": [],
        }
        gaps = self._repo.list_evidence_gaps(case_id, tenant_id) if hasattr(
            self._repo, "list_evidence_gaps",
        ) else []
        causal_graph = self._repo.get_causal_graph(case_id, tenant_id) if hasattr(
            self._repo, "get_causal_graph",
        ) else None
        conclusion = self._repo.get_conclusion(case_id, tenant_id) if hasattr(
            self._repo, "get_conclusion",
        ) else None
        recommendations = self._repo.list_repair_recommendations(case_id, tenant_id) if hasattr(
            self._repo, "list_repair_recommendations",
        ) else []
        return {
            "hypothesis_graph": hypotheses,
            "evidence_gaps": gaps,
            "causal_graph": causal_graph,
            "conclusion": conclusion,
            "recommendations": recommendations,
        }

    def propose_hypotheses(
        self, case_id: str, tenant_id: str, *, graph: dict[str, Any], actor_id: str,
        expected_scope_revision: int | None = None,
        expected_control_revision: int | None = None,
    ) -> dict[str, Any]:
        self._require_case_revision(
            case_id, tenant_id, expected_scope_revision, expected_control_revision,
        )
        hypotheses = list(graph.get("hypotheses") or [])
        if not hypotheses:
            raise ValueError("HYPOTHESES_REQUIRED")
        if len(hypotheses) > 30 or len(graph.get("edges") or []) > 60:
            raise ValueError("HYPOTHESIS_GRAPH_TOO_LARGE")
        known_evidence = self._active_evidence_ids(case_id, tenant_id)
        seen: set[str] = set()
        for item in hypotheses:
            hypothesis_id = str(item.get("hypothesis_id") or item.get("id") or "").strip()
            statement = str(
                item.get("statement") or item.get("description") or item.get("title") or ""
            ).strip()
            if not hypothesis_id or not statement:
                raise ValueError("HYPOTHESIS_ID_AND_STATEMENT_REQUIRED")
            item["hypothesis_id"] = hypothesis_id
            item["statement"] = statement
            item["status"] = {
                "PARTIALLY_RULED_OUT": "WEAKENED",
                "NOT_SUPPORTED": "WEAKENED",
            }.get(str(item.get("status") or "").upper(), item.get("status") or "PROPOSED")
            if "contradicting_evidence_refs" not in item and "opposing_evidence_refs" in item:
                item["contradicting_evidence_refs"] = item.get("opposing_evidence_refs") or []
            if "alternatives" not in item and "alternative_to" in item:
                item["alternatives"] = item.get("alternative_to") or []
            if hypothesis_id in seen:
                raise ValueError(f"DUPLICATE_HYPOTHESIS:{hypothesis_id}")
            seen.add(hypothesis_id)
            self._validate_evidence_refs(
                known_evidence,
                list(item.get("supporting_evidence_refs") or [])
                + list(item.get("contradicting_evidence_refs") or []),
            )
        hypothesis_ids = seen | {"OTHER_UNKNOWN"}
        for edge in list(graph.get("edges") or []):
            source = str(edge.get("source") or edge.get("source_hypothesis_id") or "").strip()
            target = str(edge.get("target") or edge.get("target_hypothesis_id") or "").strip()
            if not source or not target or source == target or source not in hypothesis_ids or target not in hypothesis_ids:
                raise ValueError(f"INVALID_HYPOTHESIS_EDGE:{source}->{target}")
        result = self._repo.sync_case_hypothesis_graph(
            case_id, tenant_id, graph={"hypotheses": hypotheses, "edges": graph.get("edges") or []},
            source="agent_proposal", actor_id=actor_id,
        )
        return result

    def record_gaps(
        self, case_id: str, tenant_id: str, *, gaps: list[dict[str, Any]], actor_id: str,
        expected_scope_revision: int | None = None,
        expected_control_revision: int | None = None,
    ) -> list[dict[str, Any]]:
        self._require_case_revision(
            case_id, tenant_id, expected_scope_revision, expected_control_revision,
        )
        if not gaps or len(gaps) > 30:
            raise ValueError("EVIDENCE_GAPS_REQUIRED")
        existing = {
            str(item.get("gap_id") or ""): item
            for item in self._repo.list_evidence_gaps(case_id, tenant_id)
        }
        active_evidence = self._active_evidence_ids(case_id, tenant_id)
        persisted: list[dict[str, Any]] = []
        for item in gaps:
            gap_id = str(item.get("gap_id") or item.get("id") or "").strip()
            required_fact = str(item.get("required_fact") or item.get("missing_fact") or "").strip()
            if gap_id:
                item["gap_id"] = gap_id
            if required_fact:
                item["required_fact"] = required_fact
            if "next_best_action" not in item and "resolution_plan" in item:
                item["next_best_action"] = item.get("resolution_plan")
            if "blocked_claim" not in item and "related_hypothesis" in item:
                item["blocked_claim"] = item.get("related_hypothesis")
            status = str(item.get("status") or "OPEN").upper()
            if status not in VALID_GAP_STATES:
                raise ValueError(f"INVALID_GAP_STATUS:{status}")
            observed = list(item.get("observed_evidence") or [])
            conflicts = list(item.get("conflicting_evidence_refs") or [])
            self._validate_evidence_refs(active_evidence, observed + conflicts)
            if gap_id and gap_id in existing:
                if status == "RESOLVED" and existing[gap_id].get("status") != "RESOLVED":
                    persisted.append(self._repo.resolve_evidence_gap(
                        gap_id,
                        observed_evidence=observed,
                        conflicting_evidence_refs=conflicts,
                    ))
                else:
                    persisted.append(existing[gap_id])
                continue
            if not required_fact:
                raise ValueError("GAP_REQUIRED_FACT_REQUIRED")
            if not gap_id:
                fingerprint = "|".join((case_id, required_fact, str(item.get("target") or "")))
                gap_id = "gap-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
            if gap_id in existing:
                if status == "RESOLVED" and existing[gap_id].get("status") != "RESOLVED":
                    persisted.append(self._repo.resolve_evidence_gap(
                        gap_id,
                        observed_evidence=observed,
                        conflicting_evidence_refs=conflicts,
                    ))
                else:
                    persisted.append(existing[gap_id])
                continue
            persisted.append(self._repo.add_evidence_gap(
                case_id=case_id, tenant_id=tenant_id,
                investigation_run_id=item.get("investigation_run_id"),
                gap_id=gap_id, blocked_claim=item.get("blocked_claim"),
                required_fact=required_fact, attempted_execution=item.get("attempted_execution"),
                target=item.get("target"), requested_time_window=item.get("requested_time_window") or {},
                status=status, reason_code=str(item.get("reason_code") or "MISSING_EVIDENCE"),
                raw_error_ref=item.get("raw_error_ref"), observed_evidence=observed,
                what_it_supports=item.get("what_it_supports"),
                what_it_does_not_support=item.get("what_it_does_not_support"),
                conflicting_evidence_refs=conflicts, retryable=bool(item.get("retryable", False)),
                next_best_action=item.get("next_best_action"),
            ))
        self._repo.record_case_event(
            case_id, tenant_id, event_type="evidence_gaps_recorded",
            payload={"gap_ids": [item.get("gap_id") for item in persisted]}, actor_id=actor_id,
        )
        return persisted

    def propose_causal_graph(
        self, case_id: str, tenant_id: str, *, nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]], actor_id: str,
        expected_scope_revision: int | None = None,
        expected_control_revision: int | None = None,
        expected_evidence_watermark: int | None = None,
        investigation_run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_case_revision(
            case_id, tenant_id, expected_scope_revision, expected_control_revision,
        )
        if not nodes or len(nodes) > 60 or len(edges) > 120:
            raise ValueError("INVALID_CAUSAL_GRAPH_SIZE")
        # UNKNOWN nodes without a causal relation are not a causal graph.  In
        # particular, an observed TCP dependency belongs exclusively to the
        # versioned DependencyGraph until failure Evidence establishes a
        # mechanism. Persisting it here would blur correlation into causation.
        if not edges and all(
            str(node.get("role") or "UNKNOWN").upper() == "UNKNOWN"
            for node in nodes
        ):
            raise ValueError("DEPENDENCY_ONLY_NOT_CAUSAL_GRAPH")
        projections = self._repo.list_evidence_projections(case_id, tenant_id)
        watermark = len(projections)
        if expected_evidence_watermark is not None and int(expected_evidence_watermark) < watermark:
            raise ValueError("EVIDENCE_WATERMARK_STALE")
        active_evidence = self._active_evidence_ids(case_id, tenant_id)
        cited_evidence = {
            str(ref)
            for item in [*nodes, *edges]
            for ref in (
                list(item.get("supporting_evidence_refs") or [])
                + list(item.get("opposing_evidence_refs") or [])
            )
            if str(ref)
        }
        dependency_only_evidence = {
            str(projection.get("evidence_id") or "")
            for projection in projections
            if (
                str(projection.get("projection_kind") or "").upper()
                in {"DEPENDENCY_GRAPH", "TOPOLOGY_GRAPH"}
                or str((projection.get("content") or {}).get("graph_semantics") or "")
                == "dependency_only_not_causal"
            )
        }
        if cited_evidence and cited_evidence <= dependency_only_evidence:
            raise ValueError("DEPENDENCY_ONLY_NOT_CAUSAL_GRAPH")
        node_ids: set[str] = set()
        for node in nodes:
            node_id = str(node.get("node_id") or "").strip()
            if not node_id or node_id in node_ids:
                raise ValueError(f"INVALID_CAUSAL_NODE:{node_id or 'missing'}")
            node_ids.add(node_id)
            role = str(node.get("role") or "SYMPTOM").upper()
            if role not in VALID_CAUSAL_ROLES:
                raise ValueError(f"INVALID_CAUSAL_ROLE:{role}")
            node["role"] = role
            supporting = list(node.get("supporting_evidence_refs") or [])
            self._validate_evidence_refs(
                active_evidence, supporting + list(node.get("opposing_evidence_refs") or []),
            )
            node["verifier_role"] = role if supporting else "UNVERIFIED"
        for edge in edges:
            source = str(edge.get("source_node_id") or "")
            target = str(edge.get("target_node_id") or "")
            if source not in node_ids or target not in node_ids or source == target:
                raise ValueError(f"INVALID_CAUSAL_EDGE:{source}->{target}")
            relation = str(edge.get("relation") or "CAUSES").upper()
            if relation not in VALID_CAUSAL_RELATIONS:
                raise ValueError(f"INVALID_CAUSAL_RELATION:{relation}")
            edge["relation"] = relation
            refs = list(edge.get("supporting_evidence_refs") or [])
            self._validate_evidence_refs(active_evidence, refs)
            edge["verification_state"] = "SUPPORTED" if refs else "UNVERIFIED"
        result = self._repo.submit_causal_graph_revision(
            case_id=case_id, tenant_id=tenant_id, investigation_run_id=investigation_run_id,
            evidence_watermark=watermark, nodes=nodes, edges=edges,
            verifier_version="causal-graph-verifier.v1",
        )
        self._repo.record_case_event(
            case_id, tenant_id, event_type="causal_graph_proposed",
            payload={"graph_id": result.get("graph_id"), "evidence_watermark": watermark},
            actor_id=actor_id,
        )
        return self._repo.get_causal_graph(case_id, tenant_id, result.get("graph_id")) or result

    def unresolved_alternative_count(self, case_id: str, tenant_id: str) -> int:
        graph = self._repo.get_case_hypothesis_graph(case_id, tenant_id) or {}
        return sum(
            1 for item in graph.get("hypotheses") or []
            if item.get("hypothesis_id") != "OTHER_UNKNOWN"
            and str(item.get("status") or "") in ACTIVE_HYPOTHESIS_STATES
        )

    def _require_case_revision(
        self, case_id: str, tenant_id: str, expected_scope_revision: int | None,
        expected_control_revision: int | None,
    ) -> dict[str, Any]:
        case = self._repo.get_incident_case(case_id, tenant_id)
        if case is None:
            raise ValueError("CASE_NOT_FOUND")
        if str(case.get("state") or "") in {"STOPPED", "RESOLVED"}:
            raise ValueError("CASE_TERMINAL")
        if expected_scope_revision is not None and int(expected_scope_revision) != int(
            case.get("scope_revision") or 1,
        ):
            raise ValueError("STALE_SCOPE")
        if expected_control_revision is not None and int(expected_control_revision) != int(
            case.get("control_revision") or 1,
        ):
            raise ValueError("STALE_CONTROL")
        return case

    def _active_evidence_ids(self, case_id: str, tenant_id: str) -> set[str]:
        return {
            str(item.get("evidence_id") or "")
            for item in self._repo.list_case_evidence(case_id, tenant_id, status="ACTIVE")
        }

    @staticmethod
    def _validate_evidence_refs(known: set[str], refs: list[Any]) -> None:
        unknown = [str(item) for item in refs if str(item) not in known]
        if unknown:
            raise ValueError(f"INVALID_EVIDENCE_REFS:{','.join(unknown[:5])}")
