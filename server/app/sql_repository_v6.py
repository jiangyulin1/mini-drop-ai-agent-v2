"""v6 Agent core persistence mixin for SqlRepository.

Kept as a separate bounded-context mixin so the historical Drop/Task repository
does not have to grow into a 5000+ line god class.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from server.app.models import (
    AcquisitionAssignmentModel,
    AgentCycleModel,
    AgentDecisionRecordModel,
    AgentProposalModel,
    AgentRuntimeTurnModel,
    AssistantMessageModel,
    CampaignRevisionModel,
    CaseEventModel,
    CaseContextSnapshotModel,
    CaseEvidenceModel,
    CaseHypothesisNodeModel,
    CaseRecoveryPlanModel,
    CollectionProposalModel,
    CollectionRequestModel,
    CausalEdgeModel,
    CausalGraphRevisionModel,
    CausalNodeModel,
    ClaimEvidenceBindingModel,
    ConclusionRevisionModel,
    DeploymentAssessmentModel,
    DomainOutboxModel,
    EvidenceGapModel,
    EvidenceAnalysisRunModel,
    EvidenceDependencyEdgeModel,
    ConfidenceChainSnapshotModel,
    ConfidenceAdjustmentModel,
    EvidenceProjectionModel,
    EvidenceReuseDecisionModel,
    EvidenceReviewRevisionModel,
    EvidenceReviewModel,
    ExecutionUnitModel,
    InvestigationRunModel,
    InvestigationTreeNodeModel,
    InvestigationTreeDependencyModel,
    InvestigationTreeEventModel,
    IncidentCaseModel,
    ModelRequestModel,
    ModelResponseModel,
    OperationSpecModel,
    OutboxConsumerEffectModel,
    RepairRecommendationModel,
    RuntimeWakeupModel,
    RuntimeWakeupSourceModel,
)
from server.app.diagnosis.evidence_governance import (
    INFERENCE_DECISIONS,
    assess_evidence,
    create_impact_token,
    review_result,
    verify_impact_token,
)
from server.app.diagnosis.confidence_engine import calculate_chain_confidence, CALCULATION_VERSION
from server.app.state_machine import now_utc


def _parse_aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _canonical_json(value: Any) -> str:
    """Canonical JSON representation for immutable projection comparison."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_safe(value: Any) -> Any:
    """Convert transport values (notably capability sets) to JSON values."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        values = [_json_safe(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class SqlRepositoryV6Mixin:
    """v6 persistence methods.  Every method only relies on repository base helpers."""

    # v6 canonical Agent core persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:16]}"

    def _next_run_watermark(self, session: OrmSession, case_id: str, tenant_id: str) -> int:
        current = session.query(EvidenceProjectionModel.projection_id).filter(
            EvidenceProjectionModel.case_id == case_id,
            EvidenceProjectionModel.tenant_id == tenant_id,
        ).count()
        return current

    def create_investigation_run(
        self,
        *,
        case_id: str,
        tenant_id: str,
        created_from_turn_id: str | None = None,
        scope_revision: int | None = None,
        control_revision: int | None = None,
        case_command_revision: int | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            row = InvestigationRunModel(
                run_id=run_id or self._new_id("run"),
                case_id=case_id,
                tenant_id=tenant_id,
                status="CREATED",
                scope_revision=int(scope_revision or case.scope_revision or 1),
                control_revision=int(control_revision or case.control_revision or 1),
                case_command_revision=int(case_command_revision or case.case_command_revision or 1),
                active_plan_revision=0,
                evidence_watermark=0,
                created_from_turn_id=created_from_turn_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def get_investigation_run(
        self, case_id: str, tenant_id: str, run_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(InvestigationRunModel).filter(
                InvestigationRunModel.case_id == case_id,
                InvestigationRunModel.tenant_id == tenant_id,
                InvestigationRunModel.run_id == run_id,
            ).first()
            return row.to_dict() if row else None

    def list_investigation_runs(self, case_id: str, tenant_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(InvestigationRunModel).filter(
                InvestigationRunModel.case_id == case_id,
                InvestigationRunModel.tenant_id == tenant_id,
            ).order_by(InvestigationRunModel.created_at.desc()).all()
            return [row.to_dict() for row in rows]

    # Durable evidence-driven investigation tree
    # ------------------------------------------------------------------

    _TREE_TRANSITIONS = {
        "OPEN": {"WAITING_EVIDENCE", "SUPPORTED", "RULED_OUT", "PAUSED", "INVALIDATED", "ABANDONED", "CLOSED"},
        "WAITING_EVIDENCE": {"OPEN", "SUPPORTED", "RULED_OUT", "PAUSED", "INVALIDATED", "ABANDONED"},
        "SUPPORTED": {"INVALIDATED", "ABANDONED", "CLOSED"},
        "RULED_OUT": {"INVALIDATED", "ABANDONED", "CLOSED"},
        "PAUSED": {"OPEN", "ABANDONED", "INVALIDATED"},
        "CLOSED": set(), "INVALIDATED": set(), "ABANDONED": set(),
    }

    def create_investigation_tree_node(
        self, *, case_id: str, tenant_id: str, run_id: str,
        node_type: str, statement: str = "", parent_node_id: str | None = None,
        branch_id: str | None = None, hypothesis_id: str | None = None,
        obligation: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        replay_of_node_id: str | None = None,
        created_by: str = "agent", node_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            run = session.query(InvestigationRunModel).filter(
                InvestigationRunModel.run_id == str(run_id),
                InvestigationRunModel.case_id == case_id,
                InvestigationRunModel.tenant_id == tenant_id,
            ).first()
            if run is None:
                raise ValueError("TREE_RUN_NOT_FOUND")
            parent = None
            if parent_node_id:
                parent = session.query(InvestigationTreeNodeModel).filter(
                    InvestigationTreeNodeModel.node_id == parent_node_id,
                    InvestigationTreeNodeModel.case_id == case_id,
                    InvestigationTreeNodeModel.tenant_id == tenant_id,
                ).first()
                if parent is None:
                    raise ValueError("TREE_PARENT_NOT_FOUND")
                if parent.run_id != str(run_id):
                    raise ValueError("TREE_PARENT_RUN_MISMATCH")
                if parent.status in {"INVALIDATED", "ABANDONED", "CLOSED"}:
                    raise ValueError("TREE_PARENT_NOT_ACTIVE")
            node = InvestigationTreeNodeModel(
                node_id=node_id or self._new_id("tnode"), case_id=case_id,
                tenant_id=tenant_id, run_id=run_id,
                parent_node_id=parent_node_id,
                branch_id=branch_id or (parent.branch_id if parent else self._new_id("branch")),
                node_type=str(node_type or "HYPOTHESIS").upper(), status="OPEN",
                statement=str(statement or ""), hypothesis_id=hypothesis_id,
                obligation_json=_json_safe(obligation or {}),
                evidence_refs_json=list(dict.fromkeys(str(item) for item in (evidence_refs or []) if str(item))),
                metadata_json=_json_safe(metadata or {}), depth=(int(parent.depth) + 1 if parent else 0),
                replay_of_node_id=replay_of_node_id, created_by=created_by,
                created_at=now, updated_at=now,
            )
            session.add(node)
            session.flush()
            session.add(InvestigationTreeEventModel(
                event_id=self._new_id("tevent"), case_id=case_id, tenant_id=tenant_id,
                run_id=run_id, node_id=node.node_id, event_type="NODE_CREATED",
                from_status=None, to_status="OPEN", payload_json={
                    "parent_node_id": parent_node_id, "branch_id": node.branch_id,
                }, actor_id=created_by, created_at=now,
            ))
            session.flush()
            return node.to_dict()

    def get_investigation_tree_node(self, case_id: str, tenant_id: str, node_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(InvestigationTreeNodeModel).filter(
                InvestigationTreeNodeModel.case_id == case_id,
                InvestigationTreeNodeModel.tenant_id == tenant_id,
                InvestigationTreeNodeModel.node_id == node_id,
            ).first()
            return row.to_dict() if row else None

    def list_investigation_tree(
        self, case_id: str, tenant_id: str, *, run_id: str | None = None,
        include_terminal: bool = True,
    ) -> dict[str, Any]:
        with self._read_session() as session:
            query = session.query(InvestigationTreeNodeModel).filter(
                InvestigationTreeNodeModel.case_id == case_id,
                InvestigationTreeNodeModel.tenant_id == tenant_id,
            )
            if run_id:
                query = query.filter(InvestigationTreeNodeModel.run_id == run_id)
            rows = query.order_by(InvestigationTreeNodeModel.depth.asc(), InvestigationTreeNodeModel.created_at.asc()).all()
            nodes = [row.to_dict() for row in rows if include_terminal or row.status not in {"CLOSED", "INVALIDATED", "ABANDONED"}]
            node_ids = {item["node_id"] for item in nodes}
            deps = session.query(InvestigationTreeDependencyModel).filter(
                InvestigationTreeDependencyModel.case_id == case_id,
                InvestigationTreeDependencyModel.tenant_id == tenant_id,
            ).all()
            dependencies = [row.to_dict() for row in deps if row.node_id in node_ids]
            return {"nodes": nodes, "dependencies": dependencies}

    def add_investigation_tree_dependency(
        self, *, case_id: str, tenant_id: str, node_id: str,
        target_kind: str, target_id: str, relation: str = "REQUIRES",
        actor_id: str = "agent",
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            node = session.query(InvestigationTreeNodeModel).filter(
                InvestigationTreeNodeModel.case_id == case_id,
                InvestigationTreeNodeModel.tenant_id == tenant_id,
                InvestigationTreeNodeModel.node_id == node_id,
            ).with_for_update().first()
            if node is None:
                raise ValueError("TREE_NODE_NOT_FOUND")
            target_kind = str(target_kind or "").upper()
            if target_kind not in {"EVIDENCE", "HYPOTHESIS", "COLLECTION_REQUEST", "PROJECTION"}:
                raise ValueError("TREE_DEPENDENCY_TARGET_INVALID")
            existing = session.query(InvestigationTreeDependencyModel).filter(
                InvestigationTreeDependencyModel.case_id == case_id,
                InvestigationTreeDependencyModel.tenant_id == tenant_id,
                InvestigationTreeDependencyModel.node_id == node_id,
                InvestigationTreeDependencyModel.target_kind == target_kind,
                InvestigationTreeDependencyModel.target_id == str(target_id),
                InvestigationTreeDependencyModel.relation == str(relation or "REQUIRES").upper(),
            ).first()
            if existing:
                return existing.to_dict()
            row = InvestigationTreeDependencyModel(
                dependency_id=self._new_id("tdep"), case_id=case_id, tenant_id=tenant_id,
                node_id=node_id, target_kind=target_kind, target_id=str(target_id),
                relation=str(relation or "REQUIRES").upper(), status="ACTIVE",
                created_at=now, updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def transition_investigation_tree_node(
        self, *, case_id: str, tenant_id: str, node_id: str, to_status: str,
        reason: str | None = None, actor_id: str = "agent",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        to_status = str(to_status or "").upper()
        with self._write_session() as session:
            node = session.query(InvestigationTreeNodeModel).filter(
                InvestigationTreeNodeModel.case_id == case_id,
                InvestigationTreeNodeModel.tenant_id == tenant_id,
                InvestigationTreeNodeModel.node_id == node_id,
            ).with_for_update().first()
            if node is None:
                raise ValueError("TREE_NODE_NOT_FOUND")
            if to_status == node.status:
                return node.to_dict()
            if to_status not in self._TREE_TRANSITIONS.get(node.status, set()):
                raise ValueError("TREE_NODE_INVALID_TRANSITION")
            old = node.status
            node.status = to_status
            node.revision = int(node.revision or 1) + 1
            node.invalidated_reason = reason if to_status in {"INVALIDATED", "ABANDONED"} else node.invalidated_reason
            node.updated_at = now
            if to_status in {"CLOSED", "INVALIDATED", "ABANDONED"}:
                node.closed_at = now
            session.add(InvestigationTreeEventModel(
                event_id=self._new_id("tevent"), case_id=case_id, tenant_id=tenant_id,
                run_id=node.run_id, node_id=node.node_id, event_type="NODE_STATUS_CHANGED",
                from_status=old, to_status=to_status, reason=reason,
                payload_json=_json_safe(payload or {}), actor_id=actor_id, created_at=now,
            ))
            session.flush()
            return node.to_dict()

    def _invalidate_investigation_tree_for_evidence_in_session(
        self, session: OrmSession, *, case_id: str, tenant_id: str, evidence_id: str,
        reason: str = "EVIDENCE_INVALIDATED", actor_id: str = "evidence-governance",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or now_utc()
        invalidated: list[str] = []
        abandoned: list[str] = []
        deps = session.query(InvestigationTreeDependencyModel).filter(
            InvestigationTreeDependencyModel.case_id == case_id,
            InvestigationTreeDependencyModel.tenant_id == tenant_id,
            InvestigationTreeDependencyModel.target_kind == "EVIDENCE",
            InvestigationTreeDependencyModel.target_id == str(evidence_id),
            InvestigationTreeDependencyModel.status == "ACTIVE",
        ).with_for_update().all()
        # The evidence-dependent nodes are invalidated. Their descendants are
        # abandoned recursively, while the original rows remain replayable.
        queue: list[tuple[str, bool, str | None]] = []
        for dep in deps:
            dep.status = "INVALIDATED"
            dep.invalidated_reason = reason
            dep.updated_at = now
            queue.append((dep.node_id, False, None))
        seen: set[str] = set()
        while queue:
            current_id, inherited, parent_id = queue.pop(0)
            if current_id in seen:
                continue
            seen.add(current_id)
            node = session.query(InvestigationTreeNodeModel).filter(
                InvestigationTreeNodeModel.node_id == current_id,
                InvestigationTreeNodeModel.case_id == case_id,
                InvestigationTreeNodeModel.tenant_id == tenant_id,
            ).with_for_update().first()
            if node is None or node.status in {"INVALIDATED", "ABANDONED", "CLOSED"}:
                continue
            old = node.status
            node.invalidated_evidence_refs_json = list(dict.fromkeys([
                *(node.invalidated_evidence_refs_json or []), str(evidence_id),
            ]))
            node.status = "ABANDONED" if inherited else "INVALIDATED"
            node.invalidated_reason = f"PARENT_INVALIDATED:{reason}" if inherited else reason
            node.revision = int(node.revision or 1) + 1
            node.updated_at = now
            node.closed_at = now
            (abandoned if inherited else invalidated).append(node.node_id)
            session.add(InvestigationTreeEventModel(
                event_id=self._new_id("tevent"), case_id=case_id, tenant_id=tenant_id,
                run_id=node.run_id, node_id=node.node_id,
                event_type="PARENT_INVALIDATED" if inherited else "EVIDENCE_INVALIDATED",
                from_status=old, to_status=node.status, reason=reason,
                payload_json={"evidence_id": str(evidence_id), "parent_node_id": parent_id},
                actor_id=actor_id, created_at=now,
            ))
            children = session.query(InvestigationTreeNodeModel).filter(
                InvestigationTreeNodeModel.case_id == case_id,
                InvestigationTreeNodeModel.tenant_id == tenant_id,
                InvestigationTreeNodeModel.parent_node_id == node.node_id,
                InvestigationTreeNodeModel.status.notin_(["INVALIDATED", "ABANDONED", "CLOSED"]),
            ).with_for_update().all()
            for child in children:
                queue.append((child.node_id, True, node.node_id))
        return {"evidence_id": str(evidence_id), "invalidated_nodes": invalidated, "abandoned_nodes": abandoned, "reason": reason}

    def invalidate_investigation_tree_for_evidence(
        self, *, case_id: str, tenant_id: str, evidence_id: str,
        reason: str = "EVIDENCE_INVALIDATED", actor_id: str = "evidence-governance",
    ) -> dict[str, Any]:
        with self._write_session() as session:
            result = self._invalidate_investigation_tree_for_evidence_in_session(
                session, case_id=case_id, tenant_id=tenant_id, evidence_id=evidence_id,
                reason=reason, actor_id=actor_id,
            )
            session.flush()
            return result

    def list_investigation_tree_events(
        self, case_id: str, tenant_id: str, *, run_id: str | None = None,
        node_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(InvestigationTreeEventModel).filter(
                InvestigationTreeEventModel.case_id == case_id,
                InvestigationTreeEventModel.tenant_id == tenant_id,
            )
            if run_id:
                query = query.filter(InvestigationTreeEventModel.run_id == run_id)
            if node_id:
                query = query.filter(InvestigationTreeEventModel.node_id == node_id)
            return [row.to_dict() for row in query.order_by(InvestigationTreeEventModel.created_at.asc()).all()]

    def transition_investigation_run(
        self, run_id: str, to_status: str,
        *,
        evidence_watermark: int | None = None,
        active_plan_revision: int | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(InvestigationRunModel, run_id)
            if row is None:
                return None
            row.status = to_status
            if evidence_watermark is not None:
                row.evidence_watermark = max(row.evidence_watermark, int(evidence_watermark))
            if active_plan_revision is not None:
                row.active_plan_revision = int(active_plan_revision)
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def create_case_context_snapshot(
        self, *, case_id: str, tenant_id: str, content: dict[str, Any],
        snapshot_id: str | None = None, investigation_run_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        snapshot_id = snapshot_id or self._new_id("snap")
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        snapshot_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._write_session() as session:
            row = CaseContextSnapshotModel(
                snapshot_id=snapshot_id,
                case_id=case_id,
                tenant_id=tenant_id,
                investigation_run_id=investigation_run_id,
                case_command_revision=int(content.get("case_command_revision") or 1),
                control_revision=int(content.get("control_revision") or 1),
                scope_revision=int(content.get("scope_revision") or 1),
                plan_revision=int(content.get("plan_revision") or 0),
                campaign_revision=int(content.get("campaign_revision") or 0),
                evidence_watermark=int(content.get("evidence_watermark") or 0),
                snapshot_hash=snapshot_hash,
                content_json=content,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def get_case_context_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.get(CaseContextSnapshotModel, snapshot_id)
            return row.to_dict() if row else None

    def create_agent_cycle(
        self, *, case_id: str, tenant_id: str, run_id: str,
        trigger_type: str, trigger_ref: str | None = None,
        trigger_turn_id: str | None = None, origin_turn_id: str | None = None,
        recovery_of_cycle_id: str | None = None,
        context_snapshot_id: str | None = None,
        evidence_watermark: int = 0, runtime_binding_id: str | None = None,
        generation: int = 1, cycle_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = AgentCycleModel(
                cycle_id=cycle_id or self._new_id("cycle"),
                case_id=case_id,
                tenant_id=tenant_id,
                run_id=run_id,
                trigger_type=trigger_type,
                trigger_ref=trigger_ref,
                trigger_turn_id=trigger_turn_id,
                origin_turn_id=origin_turn_id,
                recovery_of_cycle_id=recovery_of_cycle_id,
                context_snapshot_id=context_snapshot_id,
                evidence_watermark=int(evidence_watermark or 0),
                runtime_binding_id=runtime_binding_id,
                generation=int(generation or 1),
                status="QUEUED",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            # Every durable AgentCycle gets a branch root. This is an audit
            # projection; canonical Evidence remains the authority.
            root_node_id = f"tnode_cycle_{row.cycle_id}"
            root = InvestigationTreeNodeModel(
                node_id=root_node_id,
                case_id=case_id,
                tenant_id=tenant_id,
                run_id=run_id,
                parent_node_id=None,
                branch_id=self._new_id("branch"),
                node_type="CYCLE",
                status="OPEN",
                statement=f"Agent cycle {row.cycle_id}: {trigger_type}",
                obligation_json={"trigger_type": trigger_type, "trigger_ref": trigger_ref},
                metadata_json={
                    "cycle_id": row.cycle_id,
                    "recovery_of_cycle_id": recovery_of_cycle_id,
                },
                replay_of_node_id=(
                    f"tnode_cycle_{recovery_of_cycle_id}" if recovery_of_cycle_id else None
                ),
                created_by="agent-runtime",
                created_at=now,
                updated_at=now,
            )
            session.add(root)
            session.add(InvestigationTreeEventModel(
                event_id=self._new_id("tevent"), case_id=case_id, tenant_id=tenant_id,
                run_id=run_id, node_id=root_node_id, event_type="CYCLE_ROOT_CREATED",
                from_status=None, to_status="OPEN",
                payload_json={"cycle_id": row.cycle_id, "trigger_type": trigger_type},
                actor_id="agent-runtime", created_at=now,
            ))
            session.flush()
            result = row.to_dict()
            result["tree_root_node_id"] = root_node_id
            return result

    def get_agent_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.get(AgentCycleModel, cycle_id)
            return row.to_dict() if row else None

    def list_agent_cycles(
        self, case_id: str, tenant_id: str, *, run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(AgentCycleModel).filter(
                AgentCycleModel.case_id == case_id,
                AgentCycleModel.tenant_id == tenant_id,
            )
            if run_id:
                query = query.filter(AgentCycleModel.run_id == run_id)
            rows = query.order_by(AgentCycleModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def transition_agent_cycle(self, cycle_id: str, to_status: str) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(AgentCycleModel, cycle_id)
            if row is None:
                return None
            row.status = to_status
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def create_model_request(
        self, *, case_id: str, tenant_id: str, run_id: str, cycle_id: str,
        provider_request_id: str | None = None, idempotency_key: str | None = None,
        input_snapshot_hash: str | None = None,
        evidence_projection_hashes: list[str] | None = None,
        model_request_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = ModelRequestModel(
                model_request_id=model_request_id or self._new_id("mreq"),
                case_id=case_id,
                tenant_id=tenant_id,
                run_id=run_id,
                cycle_id=cycle_id,
                provider_request_id=provider_request_id,
                idempotency_key=idempotency_key,
                input_snapshot_hash=input_snapshot_hash,
                evidence_projection_hashes=list(evidence_projection_hashes or []),
                status="QUEUED",
                usage={},
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def get_model_request(self, model_request_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.get(ModelRequestModel, model_request_id)
            return row.to_dict() if row else None

    def transition_model_request(
        self, model_request_id: str, to_status: str,
        *, provider_request_id: str | None = None, usage: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(ModelRequestModel, model_request_id)
            if row is None:
                return None
            row.status = to_status
            if provider_request_id is not None:
                row.provider_request_id = provider_request_id
            if usage is not None:
                row.usage = usage
            if to_status in {"RUNNING", "WAITING_TOOL"} and row.started_at is None:
                row.started_at = now
            if to_status in {"COMPLETED", "FAILED", "CANCELLED", "FENCED"}:
                row.completed_at = now
            session.flush()
            return row.to_dict()

    def accept_model_response(
        self, *, model_request_id: str, provider_request_id: str | None,
        idempotency_key: str, canonical_visible_content: str,
        proposed_tool_calls: list[dict[str, Any]] | None = None,
        response_hash: str | None = None,
        durable_spool_offset: int | None = None,
        model_response_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        content = str(canonical_visible_content or "")
        response_hash = response_hash or hashlib.sha256(
            json.dumps({
                "content": content,
                "tool_calls": list(proposed_tool_calls or []),
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        with self._write_session() as session:
            existing = session.query(ModelResponseModel).filter(
                ModelResponseModel.model_request_id == model_request_id,
                ModelResponseModel.idempotency_key == idempotency_key,
            ).first()
            if existing is not None:
                return existing.to_dict()
            row = ModelResponseModel(
                model_response_id=model_response_id or self._new_id("mresp"),
                model_request_id=model_request_id,
                provider_request_id=provider_request_id,
                idempotency_key=idempotency_key,
                canonical_visible_content=content,
                proposed_tool_calls=list(proposed_tool_calls or []),
                response_hash=response_hash,
                durable_spool_offset=durable_spool_offset,
                accepted_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def add_assistant_message(
        self, *, case_id: str, tenant_id: str, content: str,
        trigger_turn_id: str | None = None, origin_turn_id: str | None = None,
        cycle_id: str | None = None, model_request_id: str | None = None,
        evidence_refs: list[str] | None = None,
        limitation_refs: list[str] | None = None,
        conclusion_revision_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            if message_id:
                existing = session.get(AssistantMessageModel, message_id)
                if existing is not None:
                    return existing.to_dict()
            row = AssistantMessageModel(
                message_id=message_id or self._new_id("msg"),
                case_id=case_id,
                tenant_id=tenant_id,
                trigger_turn_id=trigger_turn_id,
                origin_turn_id=origin_turn_id,
                cycle_id=cycle_id,
                model_request_id=model_request_id,
                content=content,
                evidence_refs=list(evidence_refs or []),
                limitation_refs=list(limitation_refs or []),
                conclusion_revision_id=conclusion_revision_id,
                created_at=now,
            )
            session.add(row)
            if trigger_turn_id:
                turn = session.get(AgentRuntimeTurnModel, trigger_turn_id)
                if turn is not None:
                    turn.status = "COMPLETED"
                    turn.completed_at = now
            if cycle_id:
                cycle = session.get(AgentCycleModel, cycle_id)
                if cycle is not None:
                    cycle.status = "COMPLETED"
                    cycle.updated_at = now
            session.flush()
            return row.to_dict()

    def complete_agent_runtime_turn(
        self, turn_id: str, tenant_id: str, *, detail: str | None = None,
    ) -> dict[str, Any] | None:
        """Complete an accepted Turn when its work continues asynchronously."""
        now = now_utc()
        with self._write_session() as session:
            turn = session.get(AgentRuntimeTurnModel, turn_id)
            if turn is None or turn.tenant_id != tenant_id:
                return None
            if turn.status != "COMPLETED":
                turn.status = "COMPLETED"
                turn.completed_at = now
                turn.updated_at = now
            if detail:
                turn.detail = detail
            session.flush()
            return turn.to_dict()

    def finalize_investigation_result(
        self, *, case_id: str, tenant_id: str, summary: str,
        evidence_refs: list[str], limitations: list[str], conclusion_state: str,
        conclusion_id: str | None, message_id: str, visible_content: str,
        trigger_turn_id: str | None, limitation_refs: list[str] | None = None,
        intervention_audit: dict[str, Any] | None = None,
        actor_id: str = "mini-drop-pi-runtime",
    ) -> dict[str, Any]:
        """Atomically publish the Case conclusion, assistant message and Turn completion."""
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            existing = session.get(AssistantMessageModel, message_id)
            if existing is not None:
                return {"message": existing.to_dict(), "case": case.to_dict(), "duplicate": True}

            case.current_finding_json = {
                "status": "concluded",
                "statement": summary,
                "evidence_refs": list(evidence_refs),
                "limitations": list(limitations),
            }
            case.current_activity_json = {
                "phase": "conclusion_drafted",
                "message": "Agent 已提交证据约束的结论，等待处置或继续追问",
            }
            if str(conclusion_state).upper() == "INSUFFICIENT_EVIDENCE":
                case.state = "INSUFFICIENT_EVIDENCE"
                case.state_reason = "agent_finished_with_insufficient_evidence"
                case.need_user_json = {
                    "required": True,
                    "question": "当前证据不足。请补充范围、时间窗或新的可观测数据后新开调查。",
                }
            elif case.state not in {"PAUSED", "STOPPED", "RESOLVED"}:
                case.state = "WAITING_USER"
                case.state_reason = "agent_conclusion_ready"
                case.need_user_json = {
                    "required": True,
                    "question": "结论已形成。请审查证据、选择恢复建议，或继续追问。",
                }
            case.row_version += 1
            case.updated_at = now

            message = AssistantMessageModel(
                message_id=message_id,
                case_id=case_id,
                tenant_id=tenant_id,
                trigger_turn_id=trigger_turn_id,
                origin_turn_id=trigger_turn_id,
                content=visible_content,
                evidence_refs=list(evidence_refs),
                limitation_refs=list(limitation_refs or []),
                conclusion_revision_id=conclusion_id,
                created_at=now,
            )
            session.add(message)
            if trigger_turn_id:
                turn = session.get(AgentRuntimeTurnModel, trigger_turn_id)
                if turn is not None and turn.tenant_id == tenant_id:
                    turn.status = "COMPLETED"
                    turn.completed_at = now
                    turn.updated_at = now

            event_payloads = [
                ("assistant.message", "mini-drop-agent-runtime", {
                    "message_id": message_id,
                    "trigger_turn_id": trigger_turn_id,
                    "content": visible_content,
                    "evidence_refs": list(evidence_refs),
                    "conclusion_revision_id": conclusion_id,
                }),
                ("agent_finish_investigation", actor_id, {
                    "summary": summary,
                    "evidence_refs": list(evidence_refs),
                    "verifier": "causal-report-verifier.v1",
                    "state": conclusion_state,
                    "conclusion_id": conclusion_id,
                    "assistant_message_id": message_id,
                    **(intervention_audit or {}),
                }),
            ]
            if trigger_turn_id:
                event_payloads.insert(1, ("turn.completed", "mini-drop-agent-runtime", {
                    "turn_id": trigger_turn_id,
                    "message_id": message_id,
                }))
            events: list[CaseEventModel] = []
            for event_type, event_actor, event_payload in event_payloads:
                event = CaseEventModel(
                    case_id=case_id,
                    tenant_id=tenant_id,
                    event_type=event_type,
                    actor_id=event_actor,
                    payload_json=event_payload,
                    created_at=now,
                )
                session.add(event)
                events.append(event)
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case_id,
                "tenant_id": tenant_id,
                "state": case.state,
                "row_version": case.row_version,
            })
            for event in events:
                self._notify_after_commit(session, "case_event", event.to_dict())
            return {"message": message.to_dict(), "case": case.to_dict(), "duplicate": False}

    def list_assistant_messages(
        self, case_id: str, tenant_id: str, *, limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(AssistantMessageModel).filter(
                AssistantMessageModel.case_id == case_id,
                AssistantMessageModel.tenant_id == tenant_id,
            ).order_by(AssistantMessageModel.created_at.asc()).limit(limit).all()
            return [row.to_dict() for row in rows]

    def create_agent_proposal(
        self, *, case_id: str, tenant_id: str, object_type: str,
        payload: dict[str, Any], validation_result: dict[str, Any] | None = None,
        source_cycle_id: str | None = None, proposal_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = AgentProposalModel(
                proposal_id=proposal_id or self._new_id("prop"),
                case_id=case_id,
                tenant_id=tenant_id,
                object_type=object_type,
                payload=payload or {},
                validation_result=validation_result or {},
                source_cycle_id=source_cycle_id,
                status="PROPOSED",
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def decide_agent_proposal(
        self, proposal_id: str, status: str, *, validation_result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(AgentProposalModel, proposal_id)
            if row is None:
                return None
            row.status = status
            row.decided_at = now
            if validation_result is not None:
                row.validation_result = validation_result
            session.flush()
            return row.to_dict()

    def create_collection_proposal(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = CollectionProposalModel(
                proposal_id=payload.get("proposal_id") or self._new_id("cprop"),
                case_id=payload["case_id"], tenant_id=payload["tenant_id"],
                agent_run_id=payload.get("agent_run_id"), cycle_id=payload.get("cycle_id"),
                plan_step_id=payload.get("plan_step_id"), plan_revision=payload.get("plan_revision"),
                collector_id=payload["collector_id"],
                collector_spec_version=payload["collector_spec_version"],
                target_selector=payload.get("target_selector") or {},
                parameters=payload.get("parameters") or {},
                time_window=payload.get("time_window") or {},
                information_goal=payload["information_goal"],
                reason_summary=payload.get("reason_summary") or "",
                expected_cost=payload.get("expected_cost") or {},
                expected_risk=payload["expected_risk"],
                input_evidence_refs=payload.get("input_evidence_refs") or [],
                status="PROPOSED", validation_result={}, created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def decide_collection_proposal(
        self, proposal_id: str, status: str, validation_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self._write_session() as session:
            row = session.get(CollectionProposalModel, proposal_id)
            if row is None:
                return None
            row.status = status
            row.validation_result = validation_result or {}
            row.decided_at = now_utc()
            session.flush()
            return row.to_dict()

    def get_collection_proposal(
        self, proposal_id: str, case_id: str, tenant_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(CollectionProposalModel).filter(
                CollectionProposalModel.proposal_id == proposal_id,
                CollectionProposalModel.case_id == case_id,
                CollectionProposalModel.tenant_id == tenant_id,
            ).first()
            return row.to_dict() if row else None

    def list_collection_proposals(
        self, case_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(CollectionProposalModel).filter(
                CollectionProposalModel.case_id == case_id,
                CollectionProposalModel.tenant_id == tenant_id,
            ).order_by(CollectionProposalModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def create_collection_request(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            session.query(IncidentCaseModel).filter(
                IncidentCaseModel.id == payload["case_id"],
                IncidentCaseModel.tenant_id == payload["tenant_id"],
            ).with_for_update().first()
            existing = session.query(CollectionRequestModel).filter(
                CollectionRequestModel.idempotency_key == payload["idempotency_key"],
            ).first()
            if existing is not None:
                return existing.to_dict()
            requests = session.query(CollectionRequestModel).filter(
                CollectionRequestModel.case_id == payload["case_id"],
                CollectionRequestModel.tenant_id == payload["tenant_id"],
            ).all()
            request_limit = int(payload.get("request_limit") or 0)
            if request_limit and len(requests) >= request_limit:
                raise ValueError("COLLECTION_REQUEST_COUNT_BUDGET_EXHAUSTED")
            duration_limit = int(payload.get("duration_limit_sec") or 0)
            if duration_limit:
                consumed_duration = 0
                for request in requests:
                    reservation = request.budget_reservation or {}
                    effective = request.effective_parameters or {}
                    consumed_duration += max(0, int(
                        reservation.get("reserved_duration_sec")
                        or effective.get("duration_sec")
                        or reservation.get("max_duration_sec")
                        or 0
                    ))
                requested_duration = max(0, int(
                    (payload.get("budget_reservation") or {}).get("reserved_duration_sec") or 0
                ))
                if consumed_duration + requested_duration > duration_limit:
                    raise ValueError("COLLECTION_REQUEST_DURATION_BUDGET_EXHAUSTED")
            row = CollectionRequestModel(
                collection_request_id=payload.get("collection_request_id") or self._new_id("creq"),
                proposal_id=payload["proposal_id"], case_id=payload["case_id"],
                tenant_id=payload["tenant_id"], collector_id=payload["collector_id"],
                collector_spec_version=payload["collector_spec_version"],
                resolved_target_identity=payload.get("resolved_target_identity") or {},
                effective_parameters=payload.get("effective_parameters") or {},
                runtime_generation=int(payload.get("runtime_generation") or 1),
                control_revision=int(payload.get("control_revision") or 1),
                scope_revision=int(payload.get("scope_revision") or 1),
                plan_step_id=payload.get("plan_step_id"), plan_revision=payload.get("plan_revision"),
                idempotency_key=payload["idempotency_key"],
                budget_reservation=payload.get("budget_reservation") or {},
                status=payload.get("status") or "ACCEPTED",
                task_id=payload.get("task_id"), attempt_ids=payload.get("attempt_ids") or [],
                created_at=now, updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    # Explicit Evidence reuse ledger
    # ------------------------------------------------------------------

    @staticmethod
    def _reuse_decision_key_filters(model: Any, payload: dict[str, Any]) -> list[Any]:
        """Build equality filters for the idempotency projection.

        SQL ``NULL`` never compares equal with ``=``, so nullable branch
        identifiers need an explicit ``IS NULL`` predicate.  Keeping this in
        one helper makes replay behavior identical across SQLite/PostgreSQL.
        """
        fields = (
            "case_id", "tenant_id", "investigation_run_id", "contract_digest",
            "probe_fingerprint", "evidence_id", "projection_hash",
        )
        filters: list[Any] = []
        for field in fields:
            value = payload.get(field)
            column = getattr(model, field)
            filters.append(column.is_(None) if value is None else column == value)
        return filters

    def create_evidence_reuse_decision(self, **payload: Any) -> dict[str, Any]:
        """Persist one explicit branch-local reuse/recollect decision.

        Replays of the same branch/contract/probe/result return the existing
        row.  A later review or scope fence may update the *current* decision
        to ``RECOLLECT_REQUIRED`` while retaining the row and its original
        evidence/revision snapshots.
        """
        now = now_utc()
        normalized = {
            "case_id": payload["case_id"],
            "tenant_id": payload["tenant_id"],
            "investigation_run_id": payload.get("investigation_run_id"),
            "contract_digest": str(payload.get("contract_digest") or ""),
            "probe_fingerprint": str(payload.get("probe_fingerprint") or ""),
            "evidence_id": payload.get("evidence_id"),
            "projection_hash": payload.get("projection_hash"),
        }
        if not normalized["probe_fingerprint"]:
            raise ValueError("EVIDENCE_REUSE_PROBE_FINGERPRINT_REQUIRED")
        normalized_decision = str(payload.get("decision") or "REJECTED").upper()
        if normalized_decision not in {"REUSED", "RECOLLECT_REQUIRED", "REJECTED"}:
            raise ValueError("EVIDENCE_REUSE_DECISION_INVALID")
        with self._write_session() as session:
            existing = session.query(EvidenceReuseDecisionModel).filter(
                *self._reuse_decision_key_filters(EvidenceReuseDecisionModel, normalized),
            ).first()
            if existing is not None:
                return existing.to_dict()
            row = EvidenceReuseDecisionModel(
                decision_id=payload.get("decision_id") or self._new_id("reuse"),
                case_id=normalized["case_id"],
                tenant_id=normalized["tenant_id"],
                investigation_run_id=normalized["investigation_run_id"],
                cycle_id=payload.get("cycle_id"),
                obligation_id=payload.get("obligation_id"),
                contract_digest=normalized["contract_digest"],
                collector_id=str(payload.get("collector_id") or "unknown"),
                collector_spec_version=str(payload.get("collector_spec_version") or "unknown"),
                probe_fingerprint=normalized["probe_fingerprint"],
                result_fingerprint=payload.get("result_fingerprint"),
                collection_request_id=payload.get("collection_request_id"),
                task_id=payload.get("task_id"),
                evidence_id=normalized["evidence_id"],
                projection_id=payload.get("projection_id"),
                projection_hash=normalized["projection_hash"],
                target_identity_json=_json_safe(payload.get("target_identity") or {}),
                requested_time_window_json=_json_safe(payload.get("requested_time_window") or {}),
                effective_time_window_json=_json_safe(payload.get("effective_time_window") or {}),
                control_revision=max(1, int(payload.get("control_revision") or 1)),
                scope_revision=max(1, int(payload.get("scope_revision") or 1)),
                runtime_generation=max(1, int(payload.get("runtime_generation") or 1)),
                evidence_review_revision=(
                    int(payload["evidence_review_revision"])
                    if payload.get("evidence_review_revision") is not None else None
                ),
                lifecycle_status=payload.get("lifecycle_status"),
                trust_state=payload.get("trust_state"),
                decision=normalized_decision,
                reason_codes_json=list(dict.fromkeys(
                    str(item) for item in (payload.get("reason_codes") or []) if str(item)
                )),
                actor_id=str(payload.get("actor_id") or "agent"),
                source=str(payload.get("source") or "collection_supervisor"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # Another worker may have recorded the exact same decision
                # between our read and flush.  Return its durable row rather
                # than making a replay fail with a raw database error.
                session.rollback()
                with self._read_session() as read_session:
                    raced = read_session.query(EvidenceReuseDecisionModel).filter(
                        *self._reuse_decision_key_filters(EvidenceReuseDecisionModel, normalized),
                    ).first()
                    if raced is not None:
                        return raced.to_dict()
                raise
            return row.to_dict()

    def record_evidence_reuse_decision(self, **payload: Any) -> dict[str, Any]:
        """Alias for callers that treat the ledger as an append operation."""
        return self.create_evidence_reuse_decision(**payload)

    def get_evidence_reuse_decision(
        self, decision_id: str, case_id: str, tenant_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(EvidenceReuseDecisionModel).filter(
                EvidenceReuseDecisionModel.decision_id == decision_id,
                EvidenceReuseDecisionModel.case_id == case_id,
                EvidenceReuseDecisionModel.tenant_id == tenant_id,
            ).first()
            return row.to_dict() if row else None

    def list_evidence_reuse_decisions(
        self, case_id: str, tenant_id: str, *,
        investigation_run_id: str | None = None,
        cycle_id: str | None = None,
        evidence_id: str | None = None,
        probe_fingerprint: str | None = None,
        decision: str | None = None,
        include_invalidated: bool = True,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(EvidenceReuseDecisionModel).filter(
                EvidenceReuseDecisionModel.case_id == case_id,
                EvidenceReuseDecisionModel.tenant_id == tenant_id,
            )
            if investigation_run_id is not None:
                query = query.filter(
                    EvidenceReuseDecisionModel.investigation_run_id == investigation_run_id,
                )
            if cycle_id is not None:
                query = query.filter(EvidenceReuseDecisionModel.cycle_id == cycle_id)
            if evidence_id is not None:
                query = query.filter(EvidenceReuseDecisionModel.evidence_id == evidence_id)
            if probe_fingerprint is not None:
                query = query.filter(
                    EvidenceReuseDecisionModel.probe_fingerprint == probe_fingerprint,
                )
            if decision is not None:
                query = query.filter(EvidenceReuseDecisionModel.decision == str(decision).upper())
            if not include_invalidated:
                query = query.filter(EvidenceReuseDecisionModel.invalidated_at.is_(None))
            rows = query.order_by(EvidenceReuseDecisionModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def invalidate_evidence_reuse_decisions(
        self, *, case_id: str, tenant_id: str, evidence_id: str,
        reason: str = "EVIDENCE_REVIEW_CHANGED",
    ) -> int:
        """Fence active reuse choices without deleting their audit rows."""
        with self._write_session() as session:
            return self._invalidate_reuse_decisions_for_evidence_in_session(
                session,
                case_id=case_id,
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                reason=reason,
            )

    @staticmethod
    def _invalidate_reuse_decisions_for_evidence_in_session(
        session: OrmSession, *, case_id: str, tenant_id: str,
        evidence_id: str, reason: str,
    ) -> int:
        now = now_utc()
        rows = session.query(EvidenceReuseDecisionModel).filter(
            EvidenceReuseDecisionModel.case_id == case_id,
            EvidenceReuseDecisionModel.tenant_id == tenant_id,
            EvidenceReuseDecisionModel.evidence_id == evidence_id,
            EvidenceReuseDecisionModel.decision == "REUSED",
            EvidenceReuseDecisionModel.invalidated_at.is_(None),
        ).with_for_update().all()
        for row in rows:
            row.decision = "RECOLLECT_REQUIRED"
            row.reason_codes_json = list(dict.fromkeys([
                *(row.reason_codes_json or []), reason,
            ]))
            row.invalidated_at = now
            row.invalidated_reason = reason
            row.updated_at = now
        return len(rows)

    @staticmethod
    def _invalidate_reuse_decisions_for_scope_in_session(
        session: OrmSession, *, case_id: str, tenant_id: str,
        scope_revision: int, reason: str = "SCOPE_REVISION_CHANGED",
    ) -> int:
        now = now_utc()
        rows = session.query(EvidenceReuseDecisionModel).filter(
            EvidenceReuseDecisionModel.case_id == case_id,
            EvidenceReuseDecisionModel.tenant_id == tenant_id,
            EvidenceReuseDecisionModel.decision == "REUSED",
            EvidenceReuseDecisionModel.scope_revision < int(scope_revision),
            EvidenceReuseDecisionModel.invalidated_at.is_(None),
        ).with_for_update().all()
        for row in rows:
            row.decision = "RECOLLECT_REQUIRED"
            row.reason_codes_json = list(dict.fromkeys([
                *(row.reason_codes_json or []), reason,
            ]))
            row.invalidated_at = now
            row.invalidated_reason = reason
            row.updated_at = now
        return len(rows)

    def update_collection_request(
        self, collection_request_id: str, *, status: str, task_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._write_session() as session:
            row = session.get(CollectionRequestModel, collection_request_id)
            if row is None:
                return None
            row.status = status
            if task_id is not None:
                row.task_id = task_id
            row.updated_at = now_utc()
            session.flush()
            return row.to_dict()

    def list_collection_requests(
        self, case_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(CollectionRequestModel).filter(
                CollectionRequestModel.case_id == case_id,
                CollectionRequestModel.tenant_id == tenant_id,
            ).order_by(CollectionRequestModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def add_agent_decision_record(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = AgentDecisionRecordModel(
                decision_id=payload.get("decision_id") or self._new_id("dec"),
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                cycle_id=payload["cycle_id"],
                model_request_id=payload["model_request_id"],
                observed_projection_hashes=payload.get("observed_projection_hashes") or [],
                hypotheses=payload.get("hypotheses") or [],
                opposing_evidence=payload.get("opposing_evidence") or [],
                selected_missing_fact=payload.get("selected_missing_fact"),
                selection_reason=payload.get("selection_reason"),
                proposed_operation_or_action=payload.get("proposed_operation_or_action") or {},
                alternatives_considered=payload.get("alternatives_considered") or [],
                stop_reason=payload.get("stop_reason"),
                provider_response_hash=payload.get("provider_response_hash"),
                tool_call_ids=payload.get("tool_call_ids") or [],
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def upsert_evidence_projection(
        self, *, evidence_id: str, case_id: str, tenant_id: str,
        projection_kind: str, content: dict[str, Any],
        projection_schema: str = "evidence-projection.v1",
        projection_version: int = 1, truncated: bool = False,
        source_bytes: int = 0, parser_version: str = "deterministic.v1",
    ) -> dict[str, Any]:
        now = now_utc()
        projected = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        projection_hash = hashlib.sha256(projected.encode("utf-8")).hexdigest()
        projected_bytes = len(projected.encode("utf-8"))
        with self._write_session() as session:
            existing = session.query(EvidenceProjectionModel).filter(
                EvidenceProjectionModel.evidence_id == evidence_id,
                EvidenceProjectionModel.projection_kind == projection_kind,
                EvidenceProjectionModel.projection_version == int(projection_version),
            ).first()
            if existing is not None:
                if existing.case_id != case_id or existing.tenant_id != tenant_id:
                    raise ValueError("EVIDENCE_PROJECTION_OWNERSHIP_CONFLICT")
                # A projection version is an immutable, citation-addressable
                # snapshot.  Updating content in place would silently change
                # the meaning of every citation carrying this hash.  New
                # parser output must use a new projection_version instead.
                immutable_conflicts: list[str] = []
                if existing.projection_hash != projection_hash:
                    immutable_conflicts.append("projection_hash")
                if _canonical_json(existing.content_json or {}) != _canonical_json(content):
                    immutable_conflicts.append("content")
                for field, incoming in (
                    ("projection_schema", projection_schema),
                    ("projection_version", int(projection_version)),
                    ("truncated", bool(truncated)),
                    ("source_bytes", int(source_bytes or 0)),
                    ("projected_bytes", projected_bytes),
                    ("parser_version", parser_version),
                ):
                    if getattr(existing, field) != incoming:
                        immutable_conflicts.append(field)
                if immutable_conflicts:
                    raise ValueError(
                        "EVIDENCE_PROJECTION_MUTATION:"
                        + ",".join(immutable_conflicts)
                    )
                return existing.to_dict()
            row = EvidenceProjectionModel(
                projection_id=self._new_id("proj"),
                evidence_id=evidence_id,
                case_id=case_id,
                tenant_id=tenant_id,
                projection_kind=projection_kind,
                projection_schema=projection_schema,
                projection_version=int(projection_version),
                content_json=content,
                projection_hash=projection_hash,
                truncated=bool(truncated),
                source_bytes=int(source_bytes or 0),
                projected_bytes=projected_bytes,
                parser_version=parser_version,
                created_at=now,
            )
            session.add(row)
            self._invalidate_analysis_rows(
                session, evidence_id, tenant_id, input_state="STALE_INPUT",
            )
            session.flush()
            return row.to_dict()

    def list_evidence_projections(
        self, case_id: str, tenant_id: str, evidence_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(EvidenceProjectionModel).filter(
                EvidenceProjectionModel.case_id == case_id,
                EvidenceProjectionModel.tenant_id == tenant_id,
            )
            if evidence_id:
                query = query.filter(EvidenceProjectionModel.evidence_id == evidence_id)
            rows = query.order_by(EvidenceProjectionModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def create_evidence_analysis_run(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            session.query(IncidentCaseModel).filter(
                IncidentCaseModel.id == payload["case_id"],
                IncidentCaseModel.tenant_id == payload["tenant_id"],
            ).with_for_update().first()
            existing = session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.case_id == payload["case_id"],
                EvidenceAnalysisRunModel.tenant_id == payload["tenant_id"],
                EvidenceAnalysisRunModel.input_fingerprint == payload["input_fingerprint"],
            ).first()
            if existing is not None:
                return {**existing.to_dict(), "reused": True}
            row = EvidenceAnalysisRunModel(
                analysis_run_id=payload.get("analysis_run_id") or self._new_id("ear"),
                case_id=payload["case_id"], tenant_id=payload["tenant_id"],
                mode=payload.get("mode") or "SINGLE",
                evidence_inputs=payload.get("evidence_inputs") or [],
                input_fingerprint=payload["input_fingerprint"],
                model_config_id=payload.get("model_config_id"),
                prompt_version=payload.get("prompt_version") or "evidence-analysis.v1",
                side_effect_policy="READ_ONLY",
                input_state=payload.get("input_state") or "CURRENT",
                status=payload.get("status") or "QUEUED",
                runtime_turn_id=payload.get("runtime_turn_id"), created_at=now,
            )
            session.add(row)
            session.flush()
            return {**row.to_dict(), "reused": False}

    def get_evidence_analysis_run(
        self, analysis_run_id: str, case_id: str, tenant_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.analysis_run_id == analysis_run_id,
                EvidenceAnalysisRunModel.case_id == case_id,
                EvidenceAnalysisRunModel.tenant_id == tenant_id,
            ).first()
            return row.to_dict() if row else None

    def attach_evidence_analysis_turn(
        self, analysis_run_id: str, case_id: str, tenant_id: str, runtime_turn_id: str,
    ) -> dict[str, Any] | None:
        """Bind the queued model turn after the runtime has accepted it."""
        with self._write_session() as session:
            row = session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.analysis_run_id == analysis_run_id,
                EvidenceAnalysisRunModel.case_id == case_id,
                EvidenceAnalysisRunModel.tenant_id == tenant_id,
            ).first()
            if row is None:
                return None
            row.runtime_turn_id = runtime_turn_id
            session.flush()
            return row.to_dict()

    def list_evidence_analysis_runs(
        self, case_id: str, tenant_id: str, *, evidence_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.case_id == case_id,
                EvidenceAnalysisRunModel.tenant_id == tenant_id,
            ).order_by(EvidenceAnalysisRunModel.created_at.asc()).all()
            items = [row.to_dict() for row in rows]
            if evidence_id:
                items = [item for item in items if any(
                    ref.get("evidence_id") == evidence_id for ref in item.get("evidence_inputs") or []
                )]
            return items

    def complete_evidence_analysis_run(self, analysis_run_id: str, **payload: Any) -> dict[str, Any] | None:
        with self._write_session() as session:
            row = session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.analysis_run_id == analysis_run_id,
                EvidenceAnalysisRunModel.case_id == payload["case_id"],
                EvidenceAnalysisRunModel.tenant_id == payload["tenant_id"],
            ).with_for_update().first()
            if row is None:
                return None
            if row.status not in {"QUEUED", "RUNNING"}:
                raise ValueError("ANALYSIS_RUN_NOT_COMPLETABLE")
            if row.input_state not in {"CURRENT", "EXCLUDED_INPUT"}:
                raise ValueError("ANALYSIS_INPUT_STALE")
            expected_fingerprint = payload.get("expected_input_fingerprint")
            if expected_fingerprint and row.input_fingerprint != expected_fingerprint:
                raise ValueError("ANALYSIS_INPUT_FENCED")
            for name in (
                "facts", "anomalies", "interpretations", "conflicts", "limitations",
                "next_collection_proposals", "token_usage",
            ):
                if name in payload:
                    setattr(row, name, payload.get(name) or ([] if name != "token_usage" else {}))
            row.status = payload.get("status") or "COMPLETED"
            row.latency_ms = payload.get("latency_ms")
            row.completed_at = now_utc()
            session.flush()
            return row.to_dict()

    def invalidate_evidence_analysis_run(
        self, analysis_run_id: str, case_id: str, tenant_id: str, *, input_state: str,
    ) -> dict[str, Any] | None:
        with self._write_session() as session:
            row = session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.analysis_run_id == analysis_run_id,
                EvidenceAnalysisRunModel.case_id == case_id,
                EvidenceAnalysisRunModel.tenant_id == tenant_id,
            ).first()
            if row is None:
                return None
            row.input_state = input_state
            session.flush()
            return row.to_dict()

    @staticmethod
    def _invalidate_analysis_rows(
        session: OrmSession, evidence_id: str, tenant_id: str, *, input_state: str,
    ) -> int:
        changed = 0
        rows = session.query(EvidenceAnalysisRunModel).filter(
            EvidenceAnalysisRunModel.tenant_id == tenant_id,
            EvidenceAnalysisRunModel.input_state == "CURRENT",
        ).with_for_update().all()
        for row in rows:
            if any(ref.get("evidence_id") == evidence_id for ref in (row.evidence_inputs or [])):
                row.input_state = input_state
                changed += 1
        return changed

    def invalidate_evidence_analysis_runs(
        self, evidence_id: str, tenant_id: str, *, input_state: str = "STALE_INPUT",
    ) -> int:
        changed = 0
        with self._write_session() as session:
            changed = self._invalidate_analysis_rows(
                session, evidence_id, tenant_id, input_state=input_state,
            )
            session.flush()
        return changed

    @staticmethod
    def _evidence_ref_contains(value: Any, evidence_id: str) -> bool:
        if isinstance(value, str):
            return value == evidence_id
        if isinstance(value, dict):
            return any(
                key in {"evidence_id", "id"} and str(item) == evidence_id
                for key, item in value.items()
            ) or any(
                SqlRepositoryV6Mixin._evidence_ref_contains(item, evidence_id)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(SqlRepositoryV6Mixin._evidence_ref_contains(item, evidence_id) for item in value)
        return False

    @staticmethod
    def _dependency_id(*parts: Any) -> str:
        digest = hashlib.sha256("|".join(str(item) for item in parts).encode("utf-8")).hexdigest()[:24]
        return f"dep_{digest}"

    def _sync_evidence_dependency_edges_in_session(
        self, session: OrmSession, case_id: str, tenant_id: str,
    ) -> None:
        """Materialize dependency edges from the existing canonical projections.

        The legacy graph JSON remains the write contract. This ledger gives
        lifecycle review a durable, queryable propagation surface without
        changing model-facing payloads.
        """
        now = now_utc()
        desired: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

        def add(source_id: str, target_kind: str, target_id: str, relation: str = "SUPPORTS"):
            if not source_id or not target_id:
                return
            key = ("EVIDENCE", source_id, target_kind, target_id, relation)
            desired[key] = {"source_id": source_id, "target_kind": target_kind, "target_id": target_id, "relation": relation}

        hypotheses = session.query(CaseHypothesisNodeModel).filter(
            CaseHypothesisNodeModel.case_id == case_id,
            CaseHypothesisNodeModel.tenant_id == tenant_id,
        ).all()
        for row in hypotheses:
            for ref in row.supporting_evidence_refs_json or []:
                add(str(ref), "HYPOTHESIS", row.hypothesis_id, "SUPPORTS")
            for ref in row.contradicting_evidence_refs_json or []:
                add(str(ref), "HYPOTHESIS", row.hypothesis_id, "CONTRADICTS")

        graph = session.query(CausalGraphRevisionModel).filter(
            CausalGraphRevisionModel.case_id == case_id,
            CausalGraphRevisionModel.tenant_id == tenant_id,
        ).order_by(CausalGraphRevisionModel.graph_revision.desc()).first()
        if graph is not None:
            for node in session.query(CausalNodeModel).filter(CausalNodeModel.graph_id == graph.graph_id).all():
                for ref in node.supporting_evidence_refs or []:
                    add(str(ref), "CAUSAL_NODE", f"{graph.graph_id}:{node.node_id}")
            for edge in session.query(CausalEdgeModel).filter(CausalEdgeModel.graph_id == graph.graph_id).all():
                for ref in edge.supporting_evidence_refs or []:
                    add(str(ref), "CAUSAL_EDGE", f"{graph.graph_id}:{edge.edge_id}")

        conclusions = session.query(ConclusionRevisionModel).filter(
            ConclusionRevisionModel.case_id == case_id,
            ConclusionRevisionModel.tenant_id == tenant_id,
        ).all()
        for conclusion in conclusions:
            bindings = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == conclusion.conclusion_id,
            ).all()
            for binding in bindings:
                add(binding.evidence_id, "CLAIM", f"{conclusion.conclusion_id}:{binding.claim_id}")
            claims_by_id = {
                str(item.get("claim_id")): item
                for item in conclusion.claims or []
                if isinstance(item, dict) and item.get("claim_id")
            }
            for claim_id, claim in claims_by_id.items():
                refs = claim.get("hypothesis_refs") or []
                if claim.get("hypothesis_id"):
                    refs = [*refs, claim.get("hypothesis_id")]
                for hypothesis_id in dict.fromkeys(str(ref) for ref in refs if ref):
                    key = ("HYPOTHESIS", hypothesis_id, "CLAIM", f"{conclusion.conclusion_id}:{claim_id}", "SUPPORTS")
                    desired[key] = {
                        "source_kind": "HYPOTHESIS", "source_id": hypothesis_id,
                        "target_kind": "CLAIM", "target_id": f"{conclusion.conclusion_id}:{claim_id}",
                        "relation": "SUPPORTS",
                    }

        existing = session.query(EvidenceDependencyEdgeModel).filter(
            EvidenceDependencyEdgeModel.case_id == case_id,
            EvidenceDependencyEdgeModel.tenant_id == tenant_id,
        ).all()
        existing_by_key = {
            (row.source_kind, row.source_id, row.target_kind, row.target_id, row.relation): row
            for row in existing
        }
        for key, item in desired.items():
            row = existing_by_key.get(key)
            if row is None:
                row = EvidenceDependencyEdgeModel(
                    dependency_id=self._dependency_id(case_id, tenant_id, *key),
                    case_id=case_id,
                    tenant_id=tenant_id,
                    source_kind=item.get("source_kind", key[0]),
                    source_id=item["source_id"],
                    target_kind=item["target_kind"],
                    target_id=item["target_id"],
                    relation=item["relation"],
                    support_weight=1.0,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.updated_at = now

    def _propagate_evidence_lifecycle_in_session(
        self, session: OrmSession, *, case_id: str, tenant_id: str,
        evidence: CaseEvidenceModel, decision: str, review_revision: int,
    ) -> dict[str, Any]:
        self._sync_evidence_dependency_edges_in_session(session, case_id, tenant_id)
        session.flush()
        lifecycle = str(evidence.lifecycle_status or "ACTIVE").upper()
        trust = str(evidence.review_trust_state or "UNREVIEWED").upper()
        if lifecycle != "ACTIVE":
            edge_status = "INVALIDATED"
            reason = f"EVIDENCE_{lifecycle}"
        elif trust == "LOW_TRUST":
            edge_status = "RECHECK_REQUIRED"
            reason = "EVIDENCE_LOW_TRUST"
        else:
            edge_status = "ACTIVE"
            reason = "EVIDENCE_RESTORED"
        source_edges = session.query(EvidenceDependencyEdgeModel).filter(
            EvidenceDependencyEdgeModel.case_id == case_id,
            EvidenceDependencyEdgeModel.tenant_id == tenant_id,
            EvidenceDependencyEdgeModel.source_kind == "EVIDENCE",
            EvidenceDependencyEdgeModel.source_id == evidence.evidence_id,
        ).all()
        for edge in source_edges:
            edge.status = edge_status
            edge.invalidated_by_evidence_id = evidence.evidence_id if edge_status != "ACTIVE" else None
            edge.invalidated_review_revision = review_revision if edge_status != "ACTIVE" else None
            edge.invalidated_reason = reason if edge_status != "ACTIVE" else None
            edge.updated_at = now_utc()

        active_rows = session.query(CaseEvidenceModel).filter(
            CaseEvidenceModel.case_id == case_id,
            CaseEvidenceModel.tenant_id == tenant_id,
        ).all()
        active_ids = {
            row.evidence_id for row in active_rows
            if str(row.lifecycle_status or "ACTIVE").upper() == "ACTIVE"
            and str(row.status or "ACTIVE").upper() in {"ACTIVE", "LOW_TRUST"}
        }
        evidence_by_id = {row.evidence_id: row for row in active_rows}
        # The causal-edge projection above needs the active set; update it now
        # after the set is materialized for databases that evaluate the loop
        # before this point. Recompute the fields from current lifecycle state
        # instead of appending to an old invalidation list, so restore really
        # restores the prior proof edge.
        for edge in source_edges:
            if edge.target_kind != "CAUSAL_EDGE":
                continue
            try:
                graph_id, causal_edge_id = str(edge.target_id).split(":", 1)
            except ValueError:
                continue
            causal = session.query(CausalEdgeModel).filter(
                CausalEdgeModel.graph_id == graph_id,
                CausalEdgeModel.edge_id == causal_edge_id,
            ).first()
            if causal is not None:
                support_refs = [str(ref) for ref in causal.supporting_evidence_refs or []]
                invalidated_refs = sorted(ref for ref in support_refs if ref not in active_ids)
                causal.invalidated_evidence_refs = invalidated_refs
                causal.remaining_active_support = sorted(
                    ref for ref in support_refs if ref in active_ids
                )
                causal.dependency_status = (
                    "INVALIDATED" if support_refs and not causal.remaining_active_support
                    else "RECHECK_REQUIRED" if invalidated_refs
                    else "ACTIVE"
                )
        invalidated_hypotheses: list[str] = []
        affected_hypotheses: list[str] = []
        hypotheses_needing_recheck: set[str] = set()
        for row in session.query(CaseHypothesisNodeModel).filter(
            CaseHypothesisNodeModel.case_id == case_id,
            CaseHypothesisNodeModel.tenant_id == tenant_id,
        ).all():
            refs = [str(ref) for ref in row.supporting_evidence_refs_json or []]
            contradicting = [str(ref) for ref in row.contradicting_evidence_refs_json or []]
            if evidence.evidence_id not in refs and evidence.evidence_id not in contradicting:
                continue
            affected_hypotheses.append(row.hypothesis_id)
            remaining = sorted({ref for ref in refs if ref in active_ids})
            invalidated = sorted({ref for ref in (refs + contradicting) if ref not in active_ids})
            row.invalidated_evidence_refs_json = invalidated
            row.remaining_active_support_json = remaining
            low_trust_support = any(
                str(evidence_by_id.get(ref).review_trust_state or "").upper() == "LOW_TRUST"
                for ref in remaining if evidence_by_id.get(ref) is not None
            )
            support_missing = bool(refs) and not remaining
            # A missing counter-signal is not a missing prerequisite. It may
            # change confidence, but it must never retract or recheck the
            # hypothesis by itself.
            requires_recheck = support_missing or low_trust_support
            if requires_recheck:
                row.status = "RECHECK_REQUIRED"
                hypotheses_needing_recheck.add(row.hypothesis_id)
                if support_missing:
                    invalidated_hypotheses.append(row.hypothesis_id)
            elif row.status == "RECHECK_REQUIRED" and remaining:
                row.status = "ACTIVE"
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now_utc()

        # Propagate hypothesis invalidation into the durable hypothesis->claim
        # ledger and the latest conclusion projection. A claim with independent
        # active support remains reviewable; a claim with none is retracted.
        affected_hypothesis_set = set(affected_hypotheses)
        hypothesis_claim_edges = session.query(EvidenceDependencyEdgeModel).filter(
            EvidenceDependencyEdgeModel.case_id == case_id,
            EvidenceDependencyEdgeModel.tenant_id == tenant_id,
            EvidenceDependencyEdgeModel.source_kind == "HYPOTHESIS",
        ).all()
        for edge in hypothesis_claim_edges:
            if edge.source_id not in affected_hypothesis_set:
                continue
            edge.status = (
                "RECHECK_REQUIRED"
                if edge.source_id in hypotheses_needing_recheck else "ACTIVE"
            )
            edge.invalidated_by_evidence_id = (
                evidence.evidence_id if edge.status != "ACTIVE" else None
            )
            edge.invalidated_review_revision = (
                review_revision if edge.status != "ACTIVE" else None
            )
            edge.invalidated_reason = reason if edge.status != "ACTIVE" else None
            edge.updated_at = now_utc()

        invalidated_claims: list[str] = []
        remaining_support: dict[str, list[str]] = {}
        affected_claims: list[str] = []
        bindings = session.query(ClaimEvidenceBindingModel).join(
            ConclusionRevisionModel,
            ClaimEvidenceBindingModel.conclusion_id == ConclusionRevisionModel.conclusion_id,
        ).filter(
            ConclusionRevisionModel.case_id == case_id,
            ConclusionRevisionModel.tenant_id == tenant_id,
            ClaimEvidenceBindingModel.evidence_id == evidence.evidence_id,
        ).all()
        for binding in bindings:
            claim_key = f"{binding.conclusion_id}:{binding.claim_id}"
            supports = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == binding.conclusion_id,
                ClaimEvidenceBindingModel.claim_id == binding.claim_id,
                ClaimEvidenceBindingModel.support_kind == "SUPPORTS",
            ).all()
            support_refs = sorted({str(item.evidence_id) for item in supports})
            remaining = sorted({ref for ref in support_refs if ref in active_ids})
            trusted_remaining = [
                ref for ref in remaining
                if evidence_by_id.get(ref) is not None
                and str(evidence_by_id[ref].review_trust_state or "").upper() != "LOW_TRUST"
            ]
            if str(binding.support_kind or "SUPPORTS").upper() != "SUPPORTS":
                # Losing counter-evidence is not a support failure. Keep the
                # binding as an audit fact while making its current availability
                # explicit; it must not change conclusion state.
                binding.invalidated_evidence_refs = (
                    [binding.evidence_id] if binding.evidence_id not in active_ids else []
                )
                binding.remaining_active_support = []
                binding.claim_status = "ACTIVE"
                binding.verifier_result = (
                    "CONTRADICTION_UNAVAILABLE"
                    if binding.evidence_id not in active_ids else "VALIDATED"
                )
                continue
            affected_claims.append(claim_key)
            remaining_support[claim_key] = remaining
            binding.invalidated_evidence_refs = sorted(
                ref for ref in support_refs if ref not in active_ids
            )
            binding.remaining_active_support = remaining
            if not remaining:
                binding.claim_status = "RETRACTED"
                # Keep the historical verifier code for compatibility; the
                # structured claim_status carries the stronger propagation
                # state without losing the original lifecycle reason.
                binding.verifier_result = f"EVIDENCE_{lifecycle}"
                invalidated_claims.append(claim_key)
            elif not trusted_remaining:
                binding.claim_status = "RECHECK_REQUIRED"
                binding.verifier_result = "RECHECK_REQUIRED"
            else:
                binding.claim_status = "ACTIVE"
                binding.verifier_result = "VALIDATED"

        for conclusion_id in sorted({claim_key.split(":", 1)[0] for claim_key in affected_claims}):
            conclusion = session.query(ConclusionRevisionModel).filter(
                ConclusionRevisionModel.conclusion_id == conclusion_id,
            ).first()
            if conclusion is None:
                continue
            # Rebuild the current claim ledger from all bindings.  This is
            # deliberately non-append-only: restoring an Evidence item must
            # remove its old invalidation marker rather than leaving a stale
            # tombstone that permanently poisons the conclusion.
            current_support: dict[str, list[str]] = {}
            current_invalidated: list[str] = []
            all_support_bindings = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == conclusion_id,
                ClaimEvidenceBindingModel.support_kind == "SUPPORTS",
            ).all()
            grouped: dict[str, list[ClaimEvidenceBindingModel]] = {}
            for item in all_support_bindings:
                grouped.setdefault(f"{item.conclusion_id}:{item.claim_id}", []).append(item)
            for claim_key, claim_bindings in grouped.items():
                refs = sorted({item.evidence_id for item in claim_bindings})
                active_support = sorted(ref for ref in refs if ref in active_ids)
                current_support[claim_key] = active_support
                if not active_support:
                    current_invalidated.append(claim_key)
            conclusion.invalidated_claims = sorted(set(current_invalidated))
            conclusion.remaining_active_support = current_support

        return {
            "edge_status": edge_status,
            "invalidated_hypotheses": sorted(set(invalidated_hypotheses)),
            "affected_hypotheses": sorted(set(affected_hypotheses)),
            "invalidated_claims": sorted(set(invalidated_claims)),
            "affected_claims": sorted(set(affected_claims)),
            "remaining_active_support": remaining_support,
        }

    def list_evidence_dependency_edges(
        self, case_id: str, tenant_id: str, *, target_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(EvidenceDependencyEdgeModel).filter(
                EvidenceDependencyEdgeModel.case_id == case_id,
                EvidenceDependencyEdgeModel.tenant_id == tenant_id,
            )
            if target_kind:
                query = query.filter(EvidenceDependencyEdgeModel.target_kind == target_kind)
            return [row.to_dict() for row in query.order_by(EvidenceDependencyEdgeModel.created_at.asc()).all()]

    def propose_evidence_dependency(self, **payload: Any) -> dict[str, Any]:
        """Persist an explicit Agent dependency proposal for deterministic review."""
        now = now_utc()
        key = (
            payload["case_id"], payload["tenant_id"], payload["source_kind"],
            payload["source_id"], payload["target_kind"], payload["target_id"],
            payload.get("relation", "SUPPORTS"),
        )
        with self._write_session() as session:
            existing = session.query(EvidenceDependencyEdgeModel).filter(
                EvidenceDependencyEdgeModel.case_id == key[0],
                EvidenceDependencyEdgeModel.tenant_id == key[1],
                EvidenceDependencyEdgeModel.source_kind == key[2],
                EvidenceDependencyEdgeModel.source_id == key[3],
                EvidenceDependencyEdgeModel.target_kind == key[4],
                EvidenceDependencyEdgeModel.target_id == key[5],
                EvidenceDependencyEdgeModel.relation == key[6],
            ).first()
            if existing:
                existing.support_weight = float(payload.get("support_weight", existing.support_weight) or 0.0)
                existing.updated_at = now
                return existing.to_dict()
            row = EvidenceDependencyEdgeModel(
                dependency_id=self._dependency_id(*key), case_id=key[0], tenant_id=key[1],
                source_kind=key[2], source_id=key[3], target_kind=key[4], target_id=key[5],
                relation=key[6], support_weight=float(payload.get("support_weight", 1.0) or 0.0),
                status="PROPOSED", invalidated_reason=payload.get("reason"),
                created_at=now, updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def save_confidence_snapshot(
        self,
        *,
        case_id: str,
        tenant_id: str,
        chain_type: str,
        chain_id: str,
        result: dict[str, Any],
        operator_adjustment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one immutable calculation revision and its complete ledger."""
        now = now_utc()
        with self._write_session() as session:
            latest = session.query(ConfidenceChainSnapshotModel).filter(
                ConfidenceChainSnapshotModel.case_id == case_id,
                ConfidenceChainSnapshotModel.tenant_id == tenant_id,
                ConfidenceChainSnapshotModel.chain_type == chain_type,
                ConfidenceChainSnapshotModel.chain_id == chain_id,
            ).order_by(ConfidenceChainSnapshotModel.revision.desc()).first()
            revision = int(latest.revision or 0) + 1 if latest else 1
            row = ConfidenceChainSnapshotModel(
                snapshot_id=self._new_id("conf"), case_id=case_id, tenant_id=tenant_id,
                chain_type=chain_type, chain_id=chain_id, revision=revision,
                status=result.get("status", "ACTIVE"),
                computed_confidence=float(result.get("computed_confidence", 0.0) or 0.0),
                operator_requested_confidence=result.get("operator_requested_confidence"),
                effective_confidence=float(result.get("effective_confidence", 0.0) or 0.0),
                confidence_cap=float(result.get("confidence_cap", 1.0) or 0.0),
                calculation_version=result.get("calculation_version") or CALCULATION_VERSION,
                confidence_reason=result.get("confidence_reason") or "",
                invalidated_evidence_refs=result.get("invalidated_evidence_refs") or [],
                remaining_active_support=result.get("remaining_active_support") or [],
                ledger_json=result.get("ledger") or [],
                operator_adjustment_json=operator_adjustment or {},
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def list_confidence_snapshots(
        self, case_id: str, tenant_id: str, *, chain_type: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(ConfidenceChainSnapshotModel).filter(
                ConfidenceChainSnapshotModel.case_id == case_id,
                ConfidenceChainSnapshotModel.tenant_id == tenant_id,
            )
            if chain_type:
                query = query.filter(ConfidenceChainSnapshotModel.chain_type == chain_type)
            rows = query.order_by(
                ConfidenceChainSnapshotModel.chain_type.asc(),
                ConfidenceChainSnapshotModel.chain_id.asc(),
                ConfidenceChainSnapshotModel.revision.desc(),
            ).all()
            return [row.to_dict() for row in rows]

    def list_confidence_adjustments(
        self, case_id: str, tenant_id: str, *, chain_type: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(ConfidenceAdjustmentModel).filter(
                ConfidenceAdjustmentModel.case_id == case_id,
                ConfidenceAdjustmentModel.tenant_id == tenant_id,
            )
            if chain_type:
                query = query.filter(ConfidenceAdjustmentModel.chain_type == chain_type)
            return [row.to_dict() for row in query.order_by(ConfidenceAdjustmentModel.created_at.desc()).all()]

    def record_confidence_adjustment(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = ConfidenceAdjustmentModel(
                adjustment_id=payload.get("adjustment_id") or self._new_id("cadj"),
                case_id=payload["case_id"], tenant_id=payload["tenant_id"],
                chain_type=payload["chain_type"], chain_id=payload["chain_id"],
                revision_before=int(payload["revision_before"]),
                revision_after=int(payload["revision_after"]),
                confidence_before=float(payload["confidence_before"]),
                requested_confidence=float(payload["requested_confidence"]),
                effective_confidence=float(payload["effective_confidence"]),
                reason=payload["reason"], evidence_refs=payload.get("evidence_refs") or [],
                calculation_version=payload.get("calculation_version") or CALCULATION_VERSION,
                actor_id=payload["actor_id"], created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def build_confidence_chain_impact(
        self, case_id: str, tenant_id: str, *, persist: bool = False,
    ) -> dict[str, Any]:
        """Recalculate every dependency target and return user-facing impact."""
        evidence_rows = self.list_case_evidence(case_id, tenant_id, status=None)
        evidence_by_id = {str(item.get("evidence_id")): item for item in evidence_rows if item.get("evidence_id")}
        edges = self.list_evidence_dependency_edges(case_id, tenant_id)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        proposed_dependencies = []
        for edge in edges:
            if edge.get("source_kind") != "EVIDENCE":
                continue
            # PROPOSED edges are visible as pending review but do not affect
            # confidence. INVALIDATED/RECHECK_REQUIRED edges remain in the
            # ledger so users can see exactly what an Evidence decision broke.
            if str(edge.get("status") or "ACTIVE").upper() == "PROPOSED":
                proposed_dependencies.append(edge)
                continue
            chain_type = str(edge.get("target_kind") or "").lower()
            chain_id = str(edge.get("target_id") or "")
            grouped.setdefault((chain_type, chain_id), []).append(edge)
            if chain_type == "claim" and ":" in chain_id:
                grouped.setdefault(("conclusion", chain_id.split(":", 1)[0]), []).append(edge)
        latest_snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        for snapshot in self.list_confidence_snapshots(case_id, tenant_id):
            key = (snapshot["chain_type"], snapshot["chain_id"])
            latest_snapshots.setdefault(key, snapshot)
        chains = []
        for (chain_type, chain_id), dependencies in grouped.items():
            result = calculate_chain_confidence(evidence_by_id.values(), dependencies)
            current = latest_snapshots.get((chain_type, chain_id))
            if persist:
                snapshot = self.save_confidence_snapshot(
                    case_id=case_id, tenant_id=tenant_id, chain_type=chain_type,
                    chain_id=chain_id, result=result,
                )
                if hasattr(snapshot.get("created_at"), "isoformat"):
                    snapshot["created_at"] = snapshot["created_at"].isoformat()
            else:
                snapshot = {
                    **result,
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    "chain_type": chain_type,
                    "chain_id": chain_id,
                    "revision": int((current or {}).get("revision") or 0),
                }
            chains.append(snapshot)
        return {
            "case_id": case_id,
            "calculation_version": CALCULATION_VERSION,
            "chains": chains,
            "proposed_dependencies": proposed_dependencies,
            "generated_at": now_utc().isoformat(),
        }

    def _evidence_review_impact_in_session(
        self,
        session: OrmSession,
        *,
        case_id: str,
        tenant_id: str,
        evidence: CaseEvidenceModel,
        decision: str,
        assessment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if decision in INFERENCE_DECISIONS:
            assessment_result = assess_evidence(assessment)
        else:
            assessment_result = {
                "assessment": {},
                "derived_trust_score": int(evidence.derived_trust_score or 50),
                "recommended_decision": str(evidence.review_trust_state or "UNREVIEWED"),
                "reasons": ["该操作只整理界面，不改变证据推理准入"],
            }
        outcome = review_result(
            decision=decision,
            current_lifecycle=evidence.lifecycle_status,
            current_trust=evidence.review_trust_state,
            hidden=bool(evidence.ui_hidden),
            archived=bool(evidence.ui_archived),
        )
        analyses = [
            row for row in session.query(EvidenceAnalysisRunModel).filter(
                EvidenceAnalysisRunModel.case_id == case_id,
                EvidenceAnalysisRunModel.tenant_id == tenant_id,
            ).all()
            if self._evidence_ref_contains(row.evidence_inputs or [], evidence.evidence_id)
        ]
        hypotheses = [
            row for row in session.query(CaseHypothesisNodeModel).filter(
                CaseHypothesisNodeModel.case_id == case_id,
                CaseHypothesisNodeModel.tenant_id == tenant_id,
            ).all()
            if self._evidence_ref_contains(row.supporting_evidence_refs_json or [], evidence.evidence_id)
            or self._evidence_ref_contains(row.contradicting_evidence_refs_json or [], evidence.evidence_id)
        ]
        bindings = session.query(ClaimEvidenceBindingModel).filter(
            ClaimEvidenceBindingModel.evidence_id == evidence.evidence_id,
        ).all()
        supporting_bindings = [
            row for row in bindings
            if str(row.support_kind or "SUPPORTS").upper() == "SUPPORTS"
        ]
        conclusion_ids = sorted({row.conclusion_id for row in supporting_bindings})
        plans = [
            row for row in session.query(CaseRecoveryPlanModel).filter(
                CaseRecoveryPlanModel.case_id == case_id,
                CaseRecoveryPlanModel.tenant_id == tenant_id,
                CaseRecoveryPlanModel.status.notin_({
                    "VERIFIED", "ROLLED_BACK", "REJECTED", "DRY_RUN_EMPTY", "FAILED",
                }),
            ).all()
            if evidence.evidence_id in (row.evidence_refs_json or [])
        ]
        latest = session.query(ConclusionRevisionModel).filter(
            ConclusionRevisionModel.case_id == case_id,
            ConclusionRevisionModel.tenant_id == tenant_id,
        ).order_by(ConclusionRevisionModel.revision.desc()).first()
        predicted = latest.state if latest else None
        if supporting_bindings and outcome["inference_changed"] and outcome["lifecycle_status"] != "ACTIVE":
            supporting = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == latest.conclusion_id,
                ClaimEvidenceBindingModel.support_kind == "SUPPORTS",
            ).all() if latest else []
            remaining = [item for item in supporting if item.evidence_id != evidence.evidence_id]
            predicted = "PARTIALLY_CONFIRMED" if remaining else "INSUFFICIENT_EVIDENCE"
        elif supporting_bindings and outcome["inference_changed"] and outcome["trust_state"] == "LOW_TRUST":
            predicted = "PARTIALLY_CONFIRMED" if latest else None
        recollection = []
        if decision in {"EXCLUDED", "LOW_TRUST"} and evidence.collector_id:
            recollection.append({
                "collector_id": evidence.collector_id,
                "target": evidence.target_ref or f"evidence:{evidence.evidence_id}",
                "duration_sec": 30,
            })
        affected = {
            "analysis_runs": len(analyses),
            "hypotheses": len(hypotheses),
            "conclusions": len(conclusion_ids),
            "recovery_plans": len(plans),
        }
        active_evidence_ids = {
            row.evidence_id for row in session.query(CaseEvidenceModel).filter(
                CaseEvidenceModel.case_id == case_id,
                CaseEvidenceModel.tenant_id == tenant_id,
            ).all()
            if row.evidence_id != evidence.evidence_id
            and str(row.lifecycle_status or "ACTIVE").upper() == "ACTIVE"
            and str(row.status or "ACTIVE").upper() in {"ACTIVE", "LOW_TRUST"}
        }
        predicted_invalidated_hypotheses = [
            row.hypothesis_id for row in hypotheses
            if evidence.evidence_id in (row.supporting_evidence_refs_json or [])
            and not any(str(ref) in active_evidence_ids for ref in (row.supporting_evidence_refs_json or []))
        ] if decision == "EXCLUDED" else []
        predicted_invalidated_claims: list[str] = []
        predicted_remaining_support: dict[str, list[str]] = {}
        for binding in bindings:
            if str(binding.support_kind or "SUPPORTS").upper() != "SUPPORTS":
                continue
            claim_key = f"{binding.conclusion_id}:{binding.claim_id}"
            supports = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == binding.conclusion_id,
                ClaimEvidenceBindingModel.claim_id == binding.claim_id,
                ClaimEvidenceBindingModel.support_kind == "SUPPORTS",
            ).all()
            remaining = sorted({item.evidence_id for item in supports if item.evidence_id in active_evidence_ids})
            predicted_remaining_support[claim_key] = remaining
            if decision == "EXCLUDED" and not remaining:
                predicted_invalidated_claims.append(claim_key)
        affected["invalidated_hypotheses"] = len(predicted_invalidated_hypotheses)
        affected["dependency_edges"] = len(hypotheses) + len(bindings)
        requires_approval = bool(
            plans
            or (
                decision in {"EXCLUDED", "RESTORE_AS_TRUSTED"}
                and latest is not None
                and latest.state in {"CONFIRMED", "PARTIALLY_CONFIRMED"}
            )
        )
        token_payload = {
            "case_id": case_id,
            "tenant_id": tenant_id,
            "evidence_id": evidence.evidence_id,
            "decision": decision,
            "assessment": assessment_result["assessment"],
            "expected_review_revision": int(evidence.review_revision or 0),
            "projection_hash": evidence.projection_hash,
            "current_lifecycle_status": evidence.lifecycle_status,
            "current_trust_state": evidence.review_trust_state,
            "outcome": outcome,
            "affected": affected,
            "predicted_conclusion_state": predicted,
            "propagation": {
                "invalidated_hypotheses": sorted(set(predicted_invalidated_hypotheses)),
                "invalidated_claims": sorted(set(predicted_invalidated_claims)),
                "remaining_active_support": predicted_remaining_support,
            },
            "recovery_plan_ids": sorted(row.id for row in plans),
            "requires_approval": requires_approval,
        }
        return {
            **token_payload,
            "current_review_revision": int(evidence.review_revision or 0),
            "assessment_result": assessment_result,
            "recommended_recollection": recollection,
            "impact_token": create_impact_token(token_payload),
        }

    def preview_evidence_review(
        self,
        *,
        case_id: str,
        tenant_id: str,
        evidence_id: str,
        decision: str,
        assessment: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            evidence = session.query(CaseEvidenceModel).filter(
                CaseEvidenceModel.case_id == case_id,
                CaseEvidenceModel.tenant_id == tenant_id,
                CaseEvidenceModel.evidence_id == evidence_id,
            ).first()
            if evidence is None:
                return None
            return self._evidence_review_impact_in_session(
                session,
                case_id=case_id,
                tenant_id=tenant_id,
                evidence=evidence,
                decision=decision,
                assessment=assessment,
            )

    def _hold_recovery_plans_for_evidence(
        self,
        session: OrmSession,
        *,
        case_id: str,
        tenant_id: str,
        evidence_id: str,
        review_revision: int,
        actor_id: str,
        now: datetime,
    ) -> list[str]:
        held: list[str] = []
        plans = session.query(CaseRecoveryPlanModel).filter(
            CaseRecoveryPlanModel.case_id == case_id,
            CaseRecoveryPlanModel.tenant_id == tenant_id,
            CaseRecoveryPlanModel.status.in_({
                "PROPOSED", "DRY_RUN_COMPLETED", "APPROVED", "HELD_FOR_EVIDENCE_REVIEW",
            }),
        ).with_for_update().all()
        for plan in plans:
            if evidence_id not in (plan.evidence_refs_json or []):
                continue
            existing_hold = dict(plan.evidence_hold_json or {})
            previous = (
                str(existing_hold.get("previous_status") or "PROPOSED")
                if plan.status == "HELD_FOR_EVIDENCE_REVIEW"
                else plan.status
            )
            plan.status = "HELD_FOR_EVIDENCE_REVIEW"
            plan.evidence_hold_json = {
                "previous_status": previous,
                "evidence_ids": sorted(set([
                    *(existing_hold.get("evidence_ids") or []),
                    evidence_id,
                ])),
                "review_revision": review_revision,
                "held_by": actor_id,
                "held_at": now.isoformat(),
            }
            plan.row_version += 1
            plan.updated_at = now
            held.append(plan.id)
        return held

    def _resume_recovery_plans_after_restore(
        self,
        session: OrmSession,
        *,
        case_id: str,
        tenant_id: str,
        evidence_id: str,
        now: datetime,
    ) -> list[str]:
        resumed: list[str] = []
        plans = session.query(CaseRecoveryPlanModel).filter(
            CaseRecoveryPlanModel.case_id == case_id,
            CaseRecoveryPlanModel.tenant_id == tenant_id,
            CaseRecoveryPlanModel.status == "HELD_FOR_EVIDENCE_REVIEW",
        ).with_for_update().all()
        for plan in plans:
            hold = dict(plan.evidence_hold_json or {})
            blocked = [item for item in (hold.get("evidence_ids") or []) if item != evidence_id]
            if blocked:
                hold["evidence_ids"] = blocked
                plan.evidence_hold_json = hold
                continue
            previous = str(hold.get("previous_status") or "PROPOSED")
            plan.status = previous if previous in {"PROPOSED", "DRY_RUN_COMPLETED", "APPROVED"} else "PROPOSED"
            plan.evidence_hold_json = {}
            plan.row_version += 1
            plan.updated_at = now
            resumed.append(plan.id)
        return resumed

    def apply_evidence_review(
        self,
        *,
        case_id: str,
        tenant_id: str,
        evidence_id: str,
        decision: str,
        assessment: dict[str, Any] | None,
        reason_code: str,
        reason: str,
        override_reason: str | None,
        expected_review_revision: int,
        impact_token: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            evidence = session.query(CaseEvidenceModel).filter(
                CaseEvidenceModel.case_id == case_id,
                CaseEvidenceModel.tenant_id == tenant_id,
                CaseEvidenceModel.evidence_id == evidence_id,
            ).with_for_update().first()
            if evidence is None:
                return None
            if int(evidence.review_revision or 0) != int(expected_review_revision):
                raise ValueError("EVIDENCE_REVIEW_VERSION_CONFLICT")
            preview = self._evidence_review_impact_in_session(
                session,
                case_id=case_id,
                tenant_id=tenant_id,
                evidence=evidence,
                decision=decision,
                assessment=assessment,
            )
            token_payload = {
                key: preview[key] for key in (
                    "case_id", "tenant_id", "evidence_id", "decision", "assessment",
                    "expected_review_revision", "projection_hash", "current_lifecycle_status",
                    "current_trust_state", "outcome", "affected", "predicted_conclusion_state",
                    "propagation", "recovery_plan_ids", "requires_approval",
                )
            }
            if not verify_impact_token(impact_token, token_payload):
                raise ValueError("EVIDENCE_REVIEW_IMPACT_STALE")
            assessment_result = preview["assessment_result"]
            recommendation = str(assessment_result["recommended_decision"])
            normalized_decision = {
                "RESTORE_AS_TRUSTED": "TRUSTED",
                "RESTORE_AS_LOW_TRUST": "LOW_TRUST",
            }.get(decision, decision)
            overridden = bool(assessment_result["assessment"] and normalized_decision != recommendation)
            if overridden and not str(override_reason or "").strip():
                raise ValueError("EVIDENCE_REVIEW_OVERRIDE_REASON_REQUIRED")
            outcome = preview["outcome"]
            revision = int(evidence.review_revision or 0) + 1
            legacy = EvidenceReviewModel(
                review_id=self._new_id("review"),
                case_id=case_id,
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                decision=decision,
                reason_code=reason_code,
                reason=reason,
                actor_id=actor_id,
                review_revision=revision,
                created_at=now,
            )
            session.add(legacy)
            review = EvidenceReviewRevisionModel(
                review_revision_id=self._new_id("rev"),
                evidence_id=evidence_id,
                case_id=case_id,
                tenant_id=tenant_id,
                review_revision=revision,
                decision=decision,
                lifecycle_status=outcome["lifecycle_status"],
                trust_state=outcome["trust_state"],
                derived_trust_score=int(assessment_result["derived_trust_score"]),
                projection_hash=evidence.projection_hash,
                reason_code=reason_code,
                reason=reason,
                assessment_json=assessment_result["assessment"],
                recommendation_json={
                    "decision": recommendation,
                    "reasons": assessment_result["reasons"],
                    "override_reason": override_reason,
                },
                impact_json={
                    "affected": preview["affected"],
                    "predicted_conclusion_state": preview["predicted_conclusion_state"],
                    "recommended_recollection": preview["recommended_recollection"],
                    "propagation": preview.get("propagation") or {},
                },
                overridden_recommendation=overridden,
                reviewed_by=actor_id,
                created_at=now,
            )
            session.add(review)
            previous_status = evidence.status
            evidence.lifecycle_status = outcome["lifecycle_status"]
            evidence.review_trust_state = outcome["trust_state"]
            evidence.review_revision = revision
            evidence.derived_trust_score = int(assessment_result["derived_trust_score"])
            evidence.ui_hidden = bool(outcome["ui_hidden"])
            evidence.ui_archived = bool(outcome["ui_archived"])
            evidence.status = outcome["status"]
            evidence.updated_at = now
            if outcome["lifecycle_status"] != "ACTIVE" or outcome["trust_state"] == "LOW_TRUST":
                self._invalidate_reuse_decisions_for_evidence_in_session(
                    session,
                    case_id=case_id,
                    tenant_id=tenant_id,
                    evidence_id=evidence_id,
                    reason=(
                        "EVIDENCE_REVIEW_EXCLUDED"
                        if outcome["lifecycle_status"] != "ACTIVE"
                        else "EVIDENCE_REVIEW_LOW_TRUST"
                    ),
                )
            invalidated = 0
            held_plans: list[str] = []
            resumed_plans: list[str] = []
            propagation = {
                "invalidated_hypotheses": [],
                "affected_hypotheses": [],
                "invalidated_claims": [],
                "affected_claims": [],
                "remaining_active_support": {},
            }
            tree_propagation = {
                "evidence_id": evidence_id,
                "invalidated_nodes": [],
                "abandoned_nodes": [],
            }
            if decision in INFERENCE_DECISIONS:
                invalidated = self._invalidate_analysis_rows(
                    session,
                    evidence_id,
                    tenant_id,
                    input_state=("EXCLUDED_INPUT" if outcome["lifecycle_status"] == "EXCLUDED" else "STALE_INPUT"),
                )
                self._revalidate_conclusions_after_evidence_status(
                    session, evidence, outcome["status"], now,
                )
                propagation = self._propagate_evidence_lifecycle_in_session(
                    session,
                    case_id=case_id,
                    tenant_id=tenant_id,
                    evidence=evidence,
                    decision=decision,
                    review_revision=revision,
                )
                if outcome["lifecycle_status"] != "ACTIVE" or outcome["trust_state"] == "LOW_TRUST":
                    tree_propagation = self._invalidate_investigation_tree_for_evidence_in_session(
                        session,
                        case_id=case_id,
                        tenant_id=tenant_id,
                        evidence_id=evidence_id,
                        reason=(
                            "EVIDENCE_REVIEW_EXCLUDED"
                            if outcome["lifecycle_status"] != "ACTIVE"
                            else "EVIDENCE_REVIEW_LOW_TRUST"
                        ),
                        actor_id=actor_id,
                        now=now,
                    )
                if outcome["lifecycle_status"] == "EXCLUDED" or outcome["trust_state"] == "LOW_TRUST":
                    held_plans = self._hold_recovery_plans_for_evidence(
                        session,
                        case_id=case_id,
                        tenant_id=tenant_id,
                        evidence_id=evidence_id,
                        review_revision=revision,
                        actor_id=actor_id,
                        now=now,
                    )
                elif decision.startswith("RESTORE_AS_") or decision == "TRUSTED":
                    resumed_plans = self._resume_recovery_plans_after_restore(
                        session,
                        case_id=case_id,
                        tenant_id=tenant_id,
                        evidence_id=evidence_id,
                        now=now,
                    )
            event_payload = {
                "evidence_id": evidence_id,
                "decision": decision,
                "review_revision": revision,
                "previous_status": previous_status,
                "lifecycle_status": outcome["lifecycle_status"],
                "trust_state": outcome["trust_state"],
                "invalidated_analysis_runs": invalidated,
                "held_recovery_plan_ids": held_plans,
                "resumed_recovery_plan_ids": resumed_plans,
                "propagation": propagation,
                "tree_propagation": tree_propagation,
            }
            session.add(CaseEventModel(
                case_id=case_id,
                tenant_id=tenant_id,
                event_type="evidence_reviewed",
                actor_id=actor_id,
                payload_json=event_payload,
                created_at=now,
            ))
            if held_plans:
                session.add(CaseEventModel(
                    case_id=case_id,
                    tenant_id=tenant_id,
                    event_type="recovery_plan_held",
                    actor_id=actor_id,
                    payload_json={"evidence_id": evidence_id, "recovery_plan_ids": held_plans},
                    created_at=now,
                ))
            if decision in INFERENCE_DECISIONS:
                run = session.query(InvestigationRunModel).filter(
                    InvestigationRunModel.case_id == case_id,
                    InvestigationRunModel.tenant_id == tenant_id,
                ).order_by(InvestigationRunModel.created_at.desc()).first()
                self._enqueue_domain_outbox_in_session(
                    session,
                    aggregate_type="case",
                    aggregate_id=case_id,
                    event_type="EVIDENCE_ELIGIBILITY_CHANGED",
                    payload={
                        **event_payload,
                        "case_id": case_id,
                        "tenant_id": tenant_id,
                        "investigation_run_id": run.run_id if run else None,
                        "source_refs": [f"evidence:{evidence_id}"],
                        "reason": "Human Evidence review changed inference eligibility",
                    },
                    dedupe_key=f"evidence-review:{evidence_id}:{revision}",
                    aggregate_revision=revision,
                )
            session.flush()
            return {
                **review.to_dict(),
                "status": evidence.status,
                "ui_hidden": bool(evidence.ui_hidden),
                "ui_archived": bool(evidence.ui_archived),
                "affected": preview["affected"],
                "predicted_conclusion_state": preview["predicted_conclusion_state"],
                "invalidated_analysis_runs": invalidated,
                "held_recovery_plan_ids": held_plans,
                "resumed_recovery_plan_ids": resumed_plans,
                "propagation": propagation,
                "tree_propagation": tree_propagation,
            }

    def add_evidence_review_revision(
        self, *, evidence_id: str, case_id: str, tenant_id: str,
        decision: str, reviewed_by: str, reason: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            previous = session.query(EvidenceReviewRevisionModel).filter(
                EvidenceReviewRevisionModel.evidence_id == evidence_id,
                EvidenceReviewRevisionModel.tenant_id == tenant_id,
            ).order_by(EvidenceReviewRevisionModel.review_revision.desc()).first()
            revision = int(previous.review_revision or 0) + 1 if previous else 1
            row = EvidenceReviewRevisionModel(
                review_revision_id=self._new_id("rev"),
                evidence_id=evidence_id,
                case_id=case_id,
                tenant_id=tenant_id,
                review_revision=revision,
                decision=decision,
                reason=reason,
                reviewed_by=reviewed_by,
                created_at=now,
            )
            session.add(row)
            evidence = session.query(CaseEvidenceModel).filter(
                CaseEvidenceModel.evidence_id == evidence_id,
                CaseEvidenceModel.case_id == case_id,
                CaseEvidenceModel.tenant_id == tenant_id,
            ).first()
            if evidence is not None:
                evidence.lifecycle_status = {
                    "EXCLUDED": "EXCLUDED",
                    "SUPERSEDED": "SUPERSEDED",
                    "INVALID": "INVALID",
                }.get(decision, "ACTIVE")
                # Keep the canonical Evidence governance fields in sync with
                # the review-revision ledger.  The legacy ``status`` field is
                # still emitted for compatibility, but lifecycle_status,
                # review_trust_state and review_revision are what the Case
                # snapshot and Pi runtime use to decide whether an Evidence
                # item remains citable.  Previously a review was recorded in
                # evidence_review_revisions while these fields stayed at
                # ACTIVE/UNREVIEWED/0, making a successful EXCLUDED review
                # appear to the runtime as if it had not persisted.
                evidence.lifecycle_status = {
                    "EXCLUDED": "EXCLUDED",
                    "SUPERSEDED": "SUPERSEDED",
                    "INVALID": "INVALID",
                    "RESTORED": "ACTIVE",
                }.get(decision, "ACTIVE")
                evidence.review_trust_state = {
                    "TRUSTED": "TRUSTED",
                    "LOW_TRUST": "LOW_TRUST",
                    "EXCLUDED": "EXCLUDED",
                    "RESTORED": "UNREVIEWED",
                }.get(decision, "UNREVIEWED")
                evidence.review_revision = revision
                evidence.status = (
                    evidence.lifecycle_status
                    if evidence.lifecycle_status != "ACTIVE"
                    else "LOW_TRUST" if evidence.review_trust_state == "LOW_TRUST"
                    else "ACTIVE"
                )
                evidence.updated_at = now
                if evidence.lifecycle_status != "ACTIVE" or evidence.review_trust_state == "LOW_TRUST":
                    self._invalidate_reuse_decisions_for_evidence_in_session(
                        session,
                        case_id=case_id,
                        tenant_id=tenant_id,
                        evidence_id=evidence_id,
                        reason=(
                            "EVIDENCE_REVIEW_EXCLUDED"
                            if evidence.lifecycle_status != "ACTIVE"
                            else "EVIDENCE_REVIEW_LOW_TRUST"
                        ),
                    )
            self._invalidate_analysis_rows(
                session,
                evidence_id,
                tenant_id,
                input_state="STALE_INPUT",
            )
            session.flush()
            return row.to_dict()

    def list_evidence_review_revisions(
        self, case_id: str, tenant_id: str, *, evidence_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(EvidenceReviewRevisionModel).filter(
                EvidenceReviewRevisionModel.case_id == case_id,
                EvidenceReviewRevisionModel.tenant_id == tenant_id,
            )
            if evidence_id:
                query = query.filter(EvidenceReviewRevisionModel.evidence_id == evidence_id)
            rows = query.order_by(
                EvidenceReviewRevisionModel.review_revision.desc(),
                EvidenceReviewRevisionModel.created_at.desc(),
            ).all()
            return [row.to_dict() for row in rows]

    def _revalidate_conclusions_after_evidence_status(
        self,
        session: OrmSession,
        evidence: CaseEvidenceModel,
        status: str,
        now: datetime,
    ) -> None:
        """Append a machine-downgraded Conclusion revision after invalidation."""
        affected = session.query(ClaimEvidenceBindingModel).filter(
            ClaimEvidenceBindingModel.evidence_id == evidence.evidence_id,
            # A CONTRADICTS binding is a counter-signal, not a prerequisite
            # for the claim.  Removing it must not retract the conclusion.
            ClaimEvidenceBindingModel.support_kind == "SUPPORTS",
        ).all()
        if not affected:
            return
        for binding in affected:
            binding.verifier_result = f"EVIDENCE_{status}"
        affected_ids = {binding.conclusion_id for binding in affected}
        latest = session.query(ConclusionRevisionModel).filter(
            ConclusionRevisionModel.case_id == evidence.case_id,
            ConclusionRevisionModel.tenant_id == evidence.tenant_id,
        ).order_by(ConclusionRevisionModel.revision.desc()).first()
        if latest is None or latest.conclusion_id not in affected_ids:
            return
        previous_bindings = session.query(ClaimEvidenceBindingModel).filter(
            ClaimEvidenceBindingModel.conclusion_id == latest.conclusion_id,
        ).all()
        evidence_rows = {
            row.evidence_id: row
            for row in session.query(CaseEvidenceModel).filter(
                CaseEvidenceModel.evidence_id.in_([
                    binding.evidence_id for binding in previous_bindings
                ]),
            ).all()
        }
        directional_support = [
            binding for binding in previous_bindings
            if (evidence_rows.get(binding.evidence_id) is not None)
            and evidence_rows[binding.evidence_id].lifecycle_status == "ACTIVE"
            and evidence_rows[binding.evidence_id].status in {"ACTIVE", "LOW_TRUST"}
            and binding.support_kind == "SUPPORTS"
        ]
        strong_support = [
            binding for binding in directional_support
            if evidence_rows[binding.evidence_id].review_trust_state != "LOW_TRUST"
        ]
        new_state = (
            ("PARTIALLY_CONFIRMED" if latest.state == "INSUFFICIENT_EVIDENCE" else latest.state)
            if strong_support
            else "PARTIALLY_CONFIRMED" if directional_support
            else "INSUFFICIENT_EVIDENCE"
        )
        if new_state == latest.state:
            return
        limitation = (
            f"evidence_restored_requires_reinvestigation:{evidence.evidence_id}:{status}"
            if evidence.lifecycle_status == "ACTIVE" and evidence.review_trust_state == "TRUSTED"
            else f"evidence_invalidated:{evidence.evidence_id}:{status}"
        )
        downgraded = ConclusionRevisionModel(
            conclusion_id=self._new_id("concl"),
            case_id=latest.case_id,
            tenant_id=latest.tenant_id,
            investigation_run_id=latest.investigation_run_id,
            revision=int(latest.revision or 0) + 1,
            state=new_state,
            # Do not carry values derived from an invalidated Evidence item
            # into the new effective conclusion. The prior revision remains in
            # history for audit, while this revision is an explicit abstention.
            primary_root_causes=[],
            ranked_primary_candidates=[],
            contributing_factors=[],
            amplifiers=[],
            propagated_effects=[],
            symptoms=[],
            coincidental_anomalies=[],
            ruled_out=latest.ruled_out or [],
            causal_graph_revision_id=latest.causal_graph_revision_id,
            claims=[],
            evidence_gap_ids=latest.evidence_gap_ids or [],
            recommendation_ids=[],
            limitations=list(dict.fromkeys([*(latest.limitations or []), limitation])),
            abstention_reason=(
                "Previously supporting evidence is no longer active"
                if new_state == "INSUFFICIENT_EVIDENCE" else latest.abstention_reason
            ),
            report_text=(
                "结论已因 Evidence 生命周期变更而失效；不得继续使用原结论中的数值或机制，"
                "请先重新读取 Evidence lifecycle 和 review_revision。"
            ),
            created_from_cycle_id=latest.created_from_cycle_id,
            model_request_id=latest.model_request_id,
            verifier_version="causal-report-verifier.v2-revalidation",
            created_at=now,
        )
        session.add(downgraded)
        session.flush()
        for old in previous_bindings:
            current_evidence = evidence_rows.get(old.evidence_id)
            result = (
                (
                    "VALIDATED"
                    if str(old.verifier_result or "").startswith("EVIDENCE_")
                    else old.verifier_result
                )
                if (
                    current_evidence is not None
                    and current_evidence.lifecycle_status == "ACTIVE"
                    and current_evidence.review_trust_state != "LOW_TRUST"
                )
                else (
                    "EVIDENCE_LOW_TRUST"
                    if current_evidence is not None
                    and current_evidence.lifecycle_status == "ACTIVE"
                    and current_evidence.review_trust_state == "LOW_TRUST"
                    else f"EVIDENCE_{getattr(current_evidence, 'lifecycle_status', 'MISSING')}"
                )
            )
            session.add(ClaimEvidenceBindingModel(
                claim_id=self._new_id("claim"),
                conclusion_id=downgraded.conclusion_id,
                evidence_id=old.evidence_id,
                projection_hash=old.projection_hash,
                field_path=old.field_path,
                extractor_id=old.extractor_id,
                extractor_version=old.extractor_version,
                extractor_hash=old.extractor_hash,
                target_ref=old.target_ref,
                resource_incarnation=old.resource_incarnation,
                event_window=old.event_window or {},
                predicate=old.predicate or {},
                observed_value=old.observed_value or {},
                support_kind=old.support_kind,
                verifier_result=result,
                created_at=now,
            ))

    def enqueue_domain_outbox(
        self, *, aggregate_type: str, aggregate_id: str, event_type: str,
        payload: dict[str, Any] | None = None, dedupe_key: str | None = None,
        available_at: datetime | None = None,
        aggregate_revision: int = 0, payload_schema_version: str = "1.0",
        max_attempts: int = 8,
    ) -> dict[str, Any]:
        now = now_utc()
        dedupe_key = dedupe_key or f"{aggregate_type}:{aggregate_id}:{event_type}:{uuid4().hex}"
        try:
            with self._write_session() as session:
                return self._enqueue_domain_outbox_in_session(
                    session,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                    payload=payload,
                    dedupe_key=dedupe_key,
                    available_at=available_at or now,
                    aggregate_revision=aggregate_revision,
                    payload_schema_version=payload_schema_version,
                    max_attempts=max_attempts,
                ).to_dict()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            if not isinstance(exc, IntegrityError):
                raise
            with self._read_session() as session:
                existing = session.query(DomainOutboxModel).filter(
                    DomainOutboxModel.dedupe_key == dedupe_key,
                ).first()
                if existing is None:
                    raise
                return existing.to_dict()

    def _enqueue_domain_outbox_in_session(
        self,
        session: OrmSession,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any] | None,
        dedupe_key: str,
        available_at: datetime | None = None,
        aggregate_revision: int = 0,
        payload_schema_version: str = "1.0",
        max_attempts: int = 8,
    ) -> DomainOutboxModel:
        existing = session.query(DomainOutboxModel).filter(
            DomainOutboxModel.dedupe_key == dedupe_key,
        ).first()
        if existing is not None:
            return existing
        now = now_utc()
        row = DomainOutboxModel(
            outbox_id=self._new_id("outbox"),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            aggregate_revision=max(0, int(aggregate_revision)),
            payload_schema_version=(payload_schema_version or "1.0")[:32],
            payload=payload or {},
            dedupe_key=dedupe_key,
            status="PENDING",
            available_at=available_at or now,
            attempts=0,
            max_attempts=max(1, min(int(max_attempts), 100)),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    def claim_domain_outbox(
        self,
        claimer: str,
        limit: int = 10,
        *,
        lease_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        now = now_utc()
        with self._write_session() as session:
            query = session.query(DomainOutboxModel).filter(
                DomainOutboxModel.status == "PENDING",
                DomainOutboxModel.available_at <= now,
            ).order_by(DomainOutboxModel.created_at.asc()).limit(limit)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            rows = query.all()
            results = []
            for row in rows:
                row.status = "CLAIMED"
                row.claimed_by = claimer
                row.claim_token = uuid4().hex
                row.claimed_at = now
                row.claim_expires_at = now + timedelta(
                    seconds=max(5, min(int(lease_seconds), 3600)),
                )
                row.attempts = int(row.attempts or 0) + 1
                row.updated_at = now
                results.append(row.to_dict())
            session.flush()
            return results

    def reclaim_expired_outbox(self, claimer: str, limit: int = 20) -> list[dict[str, Any]]:
        now = now_utc()
        with self._write_session() as session:
            query = session.query(DomainOutboxModel).filter(
                DomainOutboxModel.status == "CLAIMED",
                DomainOutboxModel.claim_expires_at < now,
            ).order_by(DomainOutboxModel.created_at.asc()).limit(limit)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            rows = query.all()
            results = []
            for row in rows:
                exhausted = int(row.attempts or 0) >= int(row.max_attempts or 8)
                row.status = "DEAD" if exhausted else "PENDING"
                row.dead_at = now if exhausted else None
                row.last_error = row.last_error or f"claim expired; reclaimed by {claimer}"
                row.claimed_by = None
                row.claim_token = None
                row.claim_expires_at = None
                row.claimed_at = None
                if not exhausted:
                    row.available_at = now + timedelta(
                        seconds=min(300, 2 ** min(int(row.attempts or 1) - 1, 8)),
                    )
                row.updated_at = now
                results.append(row.to_dict())
            session.flush()
            return results

    def mark_outbox_delivered(
        self, outbox_id: str, *, claim_token: str | None = None,
        dispatch_outcome: str | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(DomainOutboxModel, outbox_id)
            if row is None:
                return None
            if row.status == "DELIVERED":
                return row.to_dict()
            if row.status != "CLAIMED" or not claim_token or row.claim_token != claim_token:
                return row.to_dict()
            row.status = "DELIVERED"
            row.dispatch_outcome = dispatch_outcome or "DELIVERED"
            row.delivered_at = now
            row.claimed_by = None
            row.claim_token = None
            row.claim_expires_at = None
            row.claimed_at = None
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def fail_domain_outbox(
        self,
        outbox_id: str,
        *,
        claim_token: str,
        error: str,
        base_delay_seconds: int = 1,
        max_delay_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """NACK one claim with bounded exponential backoff or DEAD state."""

        now = now_utc()
        with self._write_session() as session:
            query = session.query(DomainOutboxModel).filter(
                DomainOutboxModel.outbox_id == outbox_id,
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            row = query.first()
            if row is None:
                return None
            if row.status != "CLAIMED" or row.claim_token != claim_token:
                return row.to_dict()
            exhausted = int(row.attempts or 0) >= int(row.max_attempts or 8)
            row.status = "DEAD" if exhausted else "PENDING"
            row.last_error = str(error)[:2000]
            row.dead_at = now if exhausted else None
            if not exhausted:
                delay = min(
                    max(1, int(max_delay_seconds)),
                    max(1, int(base_delay_seconds))
                    * (2 ** min(max(0, int(row.attempts or 1) - 1), 16)),
                )
                row.available_at = now + timedelta(seconds=delay)
            row.claimed_by = None
            row.claim_token = None
            row.claim_expires_at = None
            row.claimed_at = None
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def list_domain_outbox(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(DomainOutboxModel)
            if status:
                query = query.filter(DomainOutboxModel.status == status)
            rows = query.order_by(DomainOutboxModel.created_at.asc()).limit(limit).all()
            return [row.to_dict() for row in rows]

    def recover_dead_outbox(self, outbox_id: str) -> dict[str, Any] | None:
        """Explicit operator recovery; DEAD items never retry themselves."""

        now = now_utc()
        with self._write_session() as session:
            row = session.get(DomainOutboxModel, outbox_id)
            if row is None:
                return None
            if row.status != "DEAD":
                return row.to_dict()
            row.status = "PENDING"
            row.attempts = 0
            row.available_at = now
            row.dead_at = None
            row.last_error = None
            row.dispatch_outcome = "RECOVERED_BY_OPERATOR"
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def record_outbox_consumer_effect(
        self,
        *,
        event_id: str,
        consumer_name: str,
        effect_key: str,
        effect_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically store one consumer effect and its idempotency receipt."""

        def _existing() -> dict[str, Any] | None:
            with self._read_session() as read_session:
                row = read_session.query(OutboxConsumerEffectModel).filter(
                    OutboxConsumerEffectModel.event_id == event_id,
                    OutboxConsumerEffectModel.consumer_name == consumer_name,
                ).first()
                if row is None:
                    return None
                return {"applied": False, "effect": row.to_dict()}

        prior = _existing()
        if prior is not None:
            return prior
        try:
            with self._write_session() as session:
                row = OutboxConsumerEffectModel(
                    receipt_id=self._new_id("receipt"),
                    event_id=event_id,
                    consumer_name=consumer_name,
                    effect_key=effect_key,
                    effect_payload=effect_payload or {},
                    created_at=now_utc(),
                )
                session.add(row)
                session.flush()
                return {"applied": True, "effect": row.to_dict()}
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError

            if not isinstance(exc, IntegrityError):
                raise
            prior = _existing()
            if prior is None:
                raise
            return prior

    def create_runtime_wakeup(
        self, *, case_id: str, tenant_id: str, investigation_run_id: str,
        reason: str, source_refs: list[str] | None = None,
        control_revision: int = 1, scope_revision: int = 1,
        reason_class: str = "EVIDENCE_COMMITTED",
        from_evidence_watermark: int = 0, to_evidence_watermark: int = 0,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        dedupe_key = dedupe_key or f"wakeup:{case_id}:{investigation_run_id}:{control_revision}:{scope_revision}:{reason_class}"
        with self._write_session() as session:
            existing_pending = session.query(RuntimeWakeupModel).filter(
                RuntimeWakeupModel.dedupe_key == dedupe_key,
                RuntimeWakeupModel.status == "PENDING",
            ).first()
            if existing_pending is not None:
                existing_pending.to_evidence_watermark = max(
                    existing_pending.to_evidence_watermark, int(to_evidence_watermark or 0),
                )
                existing_pending.source_refs = sorted(set(
                    (existing_pending.source_refs or []) + list(source_refs or []),
                ))
                existing_pending.updated_at = now
                return existing_pending.to_dict()
            existing_sealed = session.query(RuntimeWakeupModel).filter(
                RuntimeWakeupModel.dedupe_key == dedupe_key,
            ).first()
            if existing_sealed is not None:
                # A sealed wakeup already fixed its evidence watermark.  New
                # evidence must open the next PENDING wakeup, never collide on
                # the immutable unique dedupe key.
                dedupe_key = f"{dedupe_key}:next:{existing_sealed.sealed_to_evidence_watermark or existing_sealed.to_evidence_watermark}:{uuid4().hex[:8]}"
            row = RuntimeWakeupModel(
                wakeup_id=self._new_id("wake"),
                case_id=case_id,
                tenant_id=tenant_id,
                investigation_run_id=investigation_run_id,
                reason=reason,
                source_refs=list(source_refs or []),
                control_revision=int(control_revision),
                scope_revision=int(scope_revision),
                reason_class=reason_class,
                from_evidence_watermark=int(from_evidence_watermark or 0),
                to_evidence_watermark=int(to_evidence_watermark or 0),
                status="PENDING",
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def list_runtime_wakeups(
        self, case_id: str, tenant_id: str, *, status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(RuntimeWakeupModel).filter(
                RuntimeWakeupModel.case_id == case_id,
                RuntimeWakeupModel.tenant_id == tenant_id,
            )
            if status:
                query = query.filter(RuntimeWakeupModel.status == status)
            rows = query.order_by(RuntimeWakeupModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def seal_runtime_wakeup(
        self, wakeup_id: str, *, cycle_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(RuntimeWakeupModel, wakeup_id)
            if row is None or row.status != "PENDING":
                return row.to_dict() if row else None
            row.status = "SEALED"
            row.sealed_at = now
            row.sealed_to_evidence_watermark = row.to_evidence_watermark
            row.cycle_id = cycle_id or row.cycle_id
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def consume_runtime_wakeup(self, wakeup_id: str, status: str = "CONSUMED") -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(RuntimeWakeupModel, wakeup_id)
            if row is None:
                return None
            row.status = status
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def requeue_runtime_wakeup(
        self, wakeup_id: str, *, cycle_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a failed delivery to PENDING without losing its audit cycle."""

        now = now_utc()
        with self._write_session() as session:
            row = session.get(RuntimeWakeupModel, wakeup_id)
            if row is None:
                return None
            if row.status != "SEALED":
                return row.to_dict()
            if cycle_id and row.cycle_id and row.cycle_id != cycle_id:
                return row.to_dict()
            row.status = "PENDING"
            row.updated_at = now
            session.flush()
            return row.to_dict()

    def add_runtime_wakeup_source(
        self, *, wakeup_id: str, outbox_id: str, source_ref: str,
        evidence_watermark: int = 0,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            existing = session.query(RuntimeWakeupSourceModel).filter(
                RuntimeWakeupSourceModel.outbox_id == outbox_id,
            ).first()
            if existing is not None:
                return existing.to_dict()
            row = RuntimeWakeupSourceModel(
                wakeup_id=wakeup_id,
                outbox_id=outbox_id,
                source_ref=source_ref,
                evidence_watermark=int(evidence_watermark or 0),
                mapped_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def get_runtime_wakeup_by_outbox(self, outbox_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            mapping = session.query(RuntimeWakeupSourceModel).filter(
                RuntimeWakeupSourceModel.outbox_id == outbox_id,
            ).first()
            if mapping is None:
                return None
            wakeup = session.get(RuntimeWakeupModel, mapping.wakeup_id)
            return wakeup.to_dict() if wakeup is not None else None

    def upsert_operation_spec(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            existing = session.query(OperationSpecModel).filter(
                OperationSpecModel.operation_id == payload["operation_id"],
                OperationSpecModel.version == payload.get("version", "v1"),
            ).first()
            values = {
                "execution_kind": payload.get("execution_kind", "QUERY"),
                "backend_ref": payload.get("backend_ref", payload["operation_id"]),
                "description": payload.get("description", ""),
                "supported_target_types": payload.get("supported_target_types") or [],
                "parameters_schema": payload.get("parameters_schema") or {},
                "evidence_schema": payload.get("evidence_schema") or {},
                "required_capabilities": payload.get("required_capabilities") or [],
                "capability_version": payload.get("capability_version"),
                "risk": payload.get("risk", "READ_LOW"),
                "timeout_sec": int(payload.get("timeout_sec", 30)),
                "max_output_bytes": int(payload.get("max_output_bytes", 1048576)),
                "parser_version": payload.get("parser_version"),
                "renderer_hash": payload.get("renderer_hash"),
                "cache_ttl": int(payload.get("cache_ttl", 0)),
                "fingerprint_fields": payload.get("fingerprint_fields") or [],
                "enabled": bool(payload.get("enabled", True)),
                "auto_allowed": bool(payload.get("auto_allowed", False)),
                "updated_at": now,
            }
            if existing is None:
                row = OperationSpecModel(
                    operation_id=payload["operation_id"],
                    version=payload.get("version", "v1"),
                    **values,
                )
                session.add(row)
                session.flush()
                return row.to_dict()
            for key, value in values.items():
                setattr(existing, key, value)
            session.flush()
            return existing.to_dict()

    def list_operation_specs(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(OperationSpecModel)
            if enabled_only:
                query = query.filter(OperationSpecModel.enabled.is_(True))
            rows = query.order_by(OperationSpecModel.operation_id.asc()).all()
            return [row.to_dict() for row in rows]

    def create_campaign_revision(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            existing = session.query(CampaignRevisionModel).filter(
                CampaignRevisionModel.case_id == payload["case_id"],
                CampaignRevisionModel.tenant_id == payload["tenant_id"],
                CampaignRevisionModel.campaign_id == payload.get("campaign_id", ""),
            ).order_by(CampaignRevisionModel.revision.desc()).first() if payload.get("campaign_id") else None
            revision = int(existing.revision or 0) + 1 if existing else int(payload.get("revision") or 1)
            campaign_id = payload.get("campaign_id") or self._new_id("camp")
            if existing is not None:
                campaign_id = existing.campaign_id
            row = CampaignRevisionModel(
                campaign_id=campaign_id,
                revision=revision,
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                plan_step_revision_id=payload.get("plan_step_revision_id"),
                membership_snapshot_id=payload.get("membership_snapshot_id"),
                coverage_policy=payload.get("coverage_policy", "REQUIRED_ALL"),
                status=payload.get("status", "DRAFT"),
                common_baseline_assignment_ids=payload.get("common_baseline_assignment_ids") or [],
                differential_assignment_ids=payload.get("differential_assignment_ids") or [],
                actor=payload.get("actor", "USER"),
                created_by=payload.get("created_by", "operator"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def create_acquisition_assignment(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = AcquisitionAssignmentModel(
                assignment_id=payload.get("assignment_id") or self._new_id("assign"),
                campaign_id=payload["campaign_id"],
                campaign_revision=int(payload.get("campaign_revision") or 1),
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                role=payload.get("role"),
                operation_ref=payload["operation_ref"],
                target_selector=payload.get("target_selector") or {},
                parameters=payload.get("parameters") or {},
                requested_window=payload.get("requested_window") or {},
                required_fact_ids=payload.get("required_fact_ids") or [],
                risk=payload.get("risk", "READ_LOW"),
                priority=int(payload.get("priority", 50)),
                depends_on=payload.get("depends_on") or [],
                required_coverage=int(payload.get("required_coverage", 1)),
                status=payload.get("status", "PLANNED"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def create_execution_unit(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = ExecutionUnitModel(
                execution_unit_id=payload.get("execution_unit_id") or self._new_id("exec"),
                assignment_id=payload["assignment_id"],
                campaign_id=payload["campaign_id"],
                campaign_revision=int(payload.get("campaign_revision") or 1),
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                resource_ref=payload["resource_ref"],
                operation_id=payload["operation_id"],
                operation_version=payload.get("operation_version", "v1"),
                normalized_parameters=payload.get("normalized_parameters") or {},
                evaluation_run_id=payload.get("evaluation_run_id"),
                deployment_epoch=int(payload.get("deployment_epoch") or 1),
                control_revision=int(payload.get("control_revision") or 1),
                scope_revision=int(payload.get("scope_revision") or 1),
                plan_revision=int(payload.get("plan_revision") or 0),
                fingerprint=payload["fingerprint"],
                status=payload.get("status", "PLANNED"),
                task_id=payload.get("task_id"),
                source_call_id=payload.get("source_call_id"),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def list_execution_units(self, case_id: str, tenant_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(ExecutionUnitModel).filter(
                ExecutionUnitModel.case_id == case_id,
                ExecutionUnitModel.tenant_id == tenant_id,
            ).order_by(ExecutionUnitModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def submit_causal_graph_revision(
        self, *, case_id: str, tenant_id: str,
        investigation_run_id: str | None = None,
        evidence_watermark: int = 0,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        created_from_cycle_id: str | None = None,
        verifier_version: str = "causal-graph-verifier.v1",
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            previous = session.query(CausalGraphRevisionModel).filter(
                CausalGraphRevisionModel.case_id == case_id,
                CausalGraphRevisionModel.tenant_id == tenant_id,
            ).order_by(CausalGraphRevisionModel.graph_revision.desc()).first()
            revision = int(previous.graph_revision or 0) + 1 if previous else 1
            graph = CausalGraphRevisionModel(
                graph_id=self._new_id("graph"),
                case_id=case_id,
                tenant_id=tenant_id,
                investigation_run_id=investigation_run_id,
                graph_revision=revision,
                evidence_watermark=int(evidence_watermark or 0),
                status="PROPOSED",
                model_proposed_json={"nodes": nodes or [], "edges": edges or []},
                verifier_json={},
                verifier_version=verifier_version,
                created_from_cycle_id=created_from_cycle_id,
                created_at=now,
            )
            session.add(graph)
            session.flush()
            for node in nodes or []:
                session.add(CausalNodeModel(
                    node_id=node.get("node_id") or self._new_id("node"),
                    graph_id=graph.graph_id,
                    case_id=case_id,
                    entity_ref=node.get("entity_ref", ""),
                    mechanism=node.get("mechanism", ""),
                    role=node.get("role", "SYMPTOM"),
                    model_proposed_role=node.get("role"),
                    verifier_role=node.get("verifier_role"),
                    onset_start=_parse_aware_datetime(node.get("onset_start")),
                    onset_end=_parse_aware_datetime(node.get("onset_end")),
                    supporting_evidence_refs=node.get("supporting_evidence_refs") or [],
                    opposing_evidence_refs=node.get("opposing_evidence_refs") or [],
                    confidence=float(node.get("confidence", 0.0) or 0.0),
                    role_rationale=node.get("role_rationale"),
                    created_at=now,
                ))
            for edge in edges or []:
                session.add(CausalEdgeModel(
                    edge_id=edge.get("edge_id") or self._new_id("edge"),
                    graph_id=graph.graph_id,
                    case_id=case_id,
                    source_node_id=edge["source_node_id"],
                    target_node_id=edge["target_node_id"],
                    relation=edge.get("relation", "CAUSES"),
                    model_proposed_relation=edge.get("relation", "CAUSES"),
                    mechanism=edge.get("mechanism"),
                    expected_lag=edge.get("expected_lag"),
                    observed_lag=edge.get("observed_lag"),
                    topology_path_refs=edge.get("topology_path_refs") or [],
                    supporting_evidence_refs=edge.get("supporting_evidence_refs") or [],
                    knowledge_refs=edge.get("knowledge_refs") or [],
                    verification_state=edge.get("verification_state", "UNVERIFIED"),
                    dependency_status=edge.get("dependency_status", "ACTIVE"),
                    invalidated_evidence_refs=edge.get("invalidated_evidence_refs") or [],
                    remaining_active_support=edge.get("remaining_active_support") or [],
                    created_at=now,
                ))
            session.flush()
            return graph.to_dict()

    def get_causal_graph(
        self, case_id: str, tenant_id: str, graph_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            query = session.query(CausalGraphRevisionModel).filter(
                CausalGraphRevisionModel.case_id == case_id,
                CausalGraphRevisionModel.tenant_id == tenant_id,
            )
            if graph_id:
                query = query.filter(CausalGraphRevisionModel.graph_id == graph_id)
            graph = query.order_by(CausalGraphRevisionModel.graph_revision.desc()).first()
            if graph is None:
                return None
            nodes = session.query(CausalNodeModel).filter(
                CausalNodeModel.graph_id == graph.graph_id,
            ).all()
            edges = session.query(CausalEdgeModel).filter(
                CausalEdgeModel.graph_id == graph.graph_id,
            ).all()
            result = graph.to_dict()
            result["nodes"] = [row.to_dict() for row in nodes]
            result["edges"] = [row.to_dict() for row in edges]
            return result

    def add_evidence_gap(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = EvidenceGapModel(
                gap_id=payload.get("gap_id") or self._new_id("gap"),
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                investigation_run_id=payload.get("investigation_run_id"),
                blocked_claim=payload.get("blocked_claim"),
                required_fact=payload.get("required_fact", ""),
                attempted_execution=payload.get("attempted_execution"),
                target=payload.get("target"),
                requested_time_window=payload.get("requested_time_window") or {},
                status=payload.get("status", "OPEN"),
                reason_code=payload.get("reason_code", "COLLECTION_FAILED"),
                raw_error_ref=payload.get("raw_error_ref"),
                observed_evidence=payload.get("observed_evidence") or [],
                what_it_supports=payload.get("what_it_supports"),
                what_it_does_not_support=payload.get("what_it_does_not_support"),
                conflicting_evidence_refs=payload.get("conflicting_evidence_refs") or [],
                retryable=bool(payload.get("retryable", False)),
                next_best_action=payload.get("next_best_action"),
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def list_evidence_gaps(
        self, case_id: str, tenant_id: str, *, status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(EvidenceGapModel).filter(
                EvidenceGapModel.case_id == case_id,
                EvidenceGapModel.tenant_id == tenant_id,
            )
            if status:
                query = query.filter(EvidenceGapModel.status == status)
            rows = query.order_by(EvidenceGapModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def resolve_evidence_gap(
        self,
        gap_id: str,
        *,
        observed_evidence: list[str] | None = None,
        conflicting_evidence_refs: list[str] | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(EvidenceGapModel, gap_id)
            if row is None:
                return None
            row.status = "RESOLVED"
            row.resolved_at = now
            if observed_evidence is not None:
                row.observed_evidence = list(dict.fromkeys(observed_evidence))
            if conflicting_evidence_refs is not None:
                row.conflicting_evidence_refs = list(dict.fromkeys(conflicting_evidence_refs))
            session.flush()
            return row.to_dict()

    def submit_conclusion_revision(
        self, *, case_id: str, tenant_id: str, investigation_run_id: str,
        state: str, causal_graph_revision_id: str | None = None,
        claims: list[dict[str, Any]] | None = None,
        root_location: dict[str, Any] | None = None,
        mechanism: dict[str, Any] | None = None,
        confidence_reason: str | None = None,
        primary_root_causes: list[dict[str, Any]] | None = None,
        contributing_factors: list[dict[str, Any]] | None = None,
        amplifiers: list[dict[str, Any]] | None = None,
        propagated_effects: list[dict[str, Any]] | None = None,
        symptoms: list[dict[str, Any]] | None = None,
        coincidental_anomalies: list[dict[str, Any]] | None = None,
        ruled_out: list[dict[str, Any]] | None = None,
        evidence_gap_ids: list[str] | None = None,
        recommendation_ids: list[str] | None = None,
        recommendations: list[dict[str, Any]] | None = None,
        limitations: list[str] | None = None,
        abstention_reason: str | None = None,
        report_text: str | None = None,
        created_from_cycle_id: str | None = None,
        model_request_id: str | None = None,
        verifier_version: str = "causal-report-verifier.v1",
        conclusion_id: str | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            if conclusion_id:
                existing = session.get(ConclusionRevisionModel, conclusion_id)
                if existing is not None:
                    if existing.case_id != case_id or existing.tenant_id != tenant_id:
                        raise ValueError("CONCLUSION_ID_CONFLICT")
                    return existing.to_dict()
            previous = session.query(ConclusionRevisionModel).filter(
                ConclusionRevisionModel.case_id == case_id,
                ConclusionRevisionModel.tenant_id == tenant_id,
            ).order_by(ConclusionRevisionModel.revision.desc()).first()
            revision = int(previous.revision or 0) + 1 if previous else 1
            evidence_rows = {
                row.evidence_id: row for row in session.query(CaseEvidenceModel).filter(
                    CaseEvidenceModel.case_id == case_id,
                    CaseEvidenceModel.tenant_id == tenant_id,
                ).all()
            }
            active_ids = {
                evidence_id for evidence_id, row in evidence_rows.items()
                if str(row.lifecycle_status or "ACTIVE").upper() == "ACTIVE"
                and str(row.status or "ACTIVE").upper() in {"ACTIVE", "LOW_TRUST"}
            }
            invalidated_claims: list[str] = []
            remaining_active_support: dict[str, list[str]] = {}
            effective_conclusion_id = conclusion_id or self._new_id("concl")
            for claim in claims or []:
                claim_id = str(claim.get("claim_id") or "")
                if not claim_id:
                    continue
                refs = [str(ref) for ref in claim.get("evidence_refs") or []]
                if claim.get("evidence_id"):
                    refs.append(str(claim.get("evidence_id")))
                remaining = sorted(set(ref for ref in refs if ref in active_ids))
                remaining_active_support[f"{effective_conclusion_id}:{claim_id}"] = remaining
                if refs and not remaining:
                    invalidated_claims.append(claim_id)
            conclusion = ConclusionRevisionModel(
                conclusion_id=effective_conclusion_id,
                case_id=case_id,
                tenant_id=tenant_id,
                investigation_run_id=investigation_run_id,
                revision=revision,
                state=state,
                primary_root_causes=primary_root_causes or [],
                ranked_primary_candidates=[],
                contributing_factors=contributing_factors or [],
                amplifiers=amplifiers or [],
                propagated_effects=propagated_effects or [],
                symptoms=symptoms or [],
                coincidental_anomalies=coincidental_anomalies or [],
                ruled_out=ruled_out or [],
                causal_graph_revision_id=causal_graph_revision_id,
                claims=claims or [],
                root_location_json=root_location or {},
                mechanism_json=mechanism or {},
                confidence_reason=confidence_reason or "",
                evidence_gap_ids=evidence_gap_ids or [],
                recommendation_ids=recommendation_ids or [],
                limitations=limitations or [],
                abstention_reason=abstention_reason,
                report_text=report_text,
                created_from_cycle_id=created_from_cycle_id,
                model_request_id=model_request_id,
                verifier_version=verifier_version,
                invalidated_claims=invalidated_claims,
                remaining_active_support=remaining_active_support,
                created_at=now,
            )
            session.add(conclusion)
            session.flush()
            for claim in claims or []:
                binding = ClaimEvidenceBindingModel(
                    claim_id=claim.get("claim_id") or self._new_id("claim"),
                    conclusion_id=conclusion.conclusion_id,
                    evidence_id=claim["evidence_id"],
                    projection_hash=claim.get("projection_hash", ""),
                    field_path=claim.get("field_path"),
                    extractor_id=claim.get("extractor_id"),
                    extractor_version=claim.get("extractor_version"),
                    extractor_hash=claim.get("extractor_hash"),
                    target_ref=claim.get("target_ref"),
                    resource_incarnation=claim.get("resource_incarnation"),
                    event_window=claim.get("event_window") or {},
                    predicate=claim.get("predicate") or {},
                    observed_value=claim.get("observed_value") or {},
                    support_kind=claim.get("support_kind", "SUPPORTS"),
                    verifier_result=claim.get("verifier_result", "VERIFIED"),
                    claim_status=("RETRACTED" if str(claim.get("evidence_id") or "") not in active_ids else "ACTIVE"),
                    invalidated_evidence_refs=(
                        [str(claim.get("evidence_id"))]
                        if claim.get("evidence_id") and str(claim.get("evidence_id")) not in active_ids else []
                    ),
                    remaining_active_support=sorted(
                        set(str(ref) for ref in (claim.get("evidence_refs") or [])) & active_ids
                    ),
                    created_at=now,
                )
                session.add(binding)
            for recommendation, recommendation_id in zip(
                recommendations or [], recommendation_ids or [],
            ):
                session.add(RepairRecommendationModel(
                    recommendation_id=recommendation_id,
                    case_id=case_id,
                    tenant_id=tenant_id,
                    conclusion_id=conclusion.conclusion_id,
                    cause_or_edge_ref=recommendation["cause_or_edge_ref"],
                    category=recommendation.get("category", "root_fix"),
                    target=recommendation["target"],
                    concrete_action=recommendation["concrete_action"],
                    rationale=recommendation.get("rationale"),
                    evidence_refs=recommendation.get("evidence_refs") or [],
                    prerequisites=recommendation.get("prerequisites") or [],
                    risk=recommendation.get("risk"),
                    approval=recommendation.get("approval"),
                    expected_effect=recommendation.get("expected_effect"),
                    verification_operations=recommendation.get("verification_operations") or [],
                    success_criteria=recommendation.get("success_criteria") or [],
                    rollback_or_failure_condition=recommendation.get("rollback_or_failure_condition"),
                    confidence=float(recommendation.get("confidence", 0.0) or 0.0),
                    limitations=recommendation.get("limitations") or [],
                    created_at=now,
                ))
            session.flush()
            return conclusion.to_dict()

    def get_conclusion(
        self, case_id: str, tenant_id: str, conclusion_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            query = session.query(ConclusionRevisionModel).filter(
                ConclusionRevisionModel.case_id == case_id,
                ConclusionRevisionModel.tenant_id == tenant_id,
            )
            if conclusion_id:
                query = query.filter(ConclusionRevisionModel.conclusion_id == conclusion_id)
                conclusion = query.order_by(ConclusionRevisionModel.revision.desc()).first()
            else:
                published = session.query(AssistantMessageModel).filter(
                    AssistantMessageModel.case_id == case_id,
                    AssistantMessageModel.tenant_id == tenant_id,
                    AssistantMessageModel.conclusion_revision_id.is_not(None),
                ).order_by(AssistantMessageModel.created_at.desc()).first()
                if published is not None:
                    published_conclusion = query.filter(
                        ConclusionRevisionModel.conclusion_id == published.conclusion_revision_id,
                    ).first()
                    if published_conclusion is None:
                        conclusion = None
                    else:
                        # Message bindings are immutable; revalidation advances current state.
                        revalidated = query.filter(
                            ConclusionRevisionModel.revision > published_conclusion.revision,
                            ConclusionRevisionModel.verifier_version
                            == "causal-report-verifier.v2-revalidation",
                        ).order_by(ConclusionRevisionModel.revision.desc()).first()
                        conclusion = revalidated or published_conclusion
                else:
                    conclusion = query.order_by(ConclusionRevisionModel.revision.desc()).first()
            if conclusion is None:
                return None
            bindings = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == conclusion.conclusion_id,
            ).all()
            result = conclusion.to_dict()
            result["claim_evidence_bindings"] = [row.to_dict() for row in bindings]
            latest_revision = session.query(ConclusionRevisionModel.revision).filter(
                ConclusionRevisionModel.case_id == case_id,
                ConclusionRevisionModel.tenant_id == tenant_id,
            ).order_by(ConclusionRevisionModel.revision.desc()).first()
            latest_revision_value = int(latest_revision[0]) if latest_revision else int(conclusion.revision or 0)
            result["is_current"] = int(conclusion.revision or 0) == latest_revision_value
            result["revision_status"] = (
                "CURRENT" if result["is_current"] else "SUPERSEDED"
            )
            return result

    def list_conclusion_revisions(
        self, case_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        """Return every conclusion revision, newest first, without deleting history."""
        with self._read_session() as session:
            rows = session.query(ConclusionRevisionModel).filter(
                ConclusionRevisionModel.case_id == case_id,
                ConclusionRevisionModel.tenant_id == tenant_id,
            ).order_by(ConclusionRevisionModel.revision.desc()).all()
            latest_revision = int(rows[0].revision or 0) if rows else 0
            result: list[dict[str, Any]] = []
            for row in rows:
                item = row.to_dict()
                bindings = session.query(ClaimEvidenceBindingModel).filter(
                    ClaimEvidenceBindingModel.conclusion_id == row.conclusion_id,
                ).all()
                item["claim_evidence_bindings"] = [binding.to_dict() for binding in bindings]
                item["is_current"] = int(row.revision or 0) == latest_revision
                item["revision_status"] = "CURRENT" if item["is_current"] else "SUPERSEDED"
                result.append(item)
            return result

    def add_repair_recommendation(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = RepairRecommendationModel(
                recommendation_id=payload.get("recommendation_id") or self._new_id("rec"),
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                conclusion_id=payload.get("conclusion_id"),
                cause_or_edge_ref=payload["cause_or_edge_ref"],
                category=payload.get("category", "root_fix"),
                target=payload.get("target", ""),
                concrete_action=payload.get("concrete_action", ""),
                rationale=payload.get("rationale"),
                evidence_refs=payload.get("evidence_refs") or [],
                prerequisites=payload.get("prerequisites") or [],
                risk=payload.get("risk"),
                approval=payload.get("approval"),
                expected_effect=payload.get("expected_effect"),
                verification_operations=payload.get("verification_operations") or [],
                success_criteria=payload.get("success_criteria") or [],
                rollback_or_failure_condition=payload.get("rollback_or_failure_condition"),
                confidence=float(payload.get("confidence", 0.0) or 0.0),
                limitations=payload.get("limitations") or [],
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def list_repair_recommendations(
        self, case_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(RepairRecommendationModel).filter(
                RepairRecommendationModel.case_id == case_id,
                RepairRecommendationModel.tenant_id == tenant_id,
            ).order_by(RepairRecommendationModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def persist_deployment_assessment(self, **payload: Any) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            row = DeploymentAssessmentModel(
                assessment_id=payload.get("assessment_id") or self._new_id("dasm"),
                case_id=payload["case_id"],
                tenant_id=payload["tenant_id"],
                verdict=payload.get("verdict", "INSUFFICIENT_DATA"),
                summary=payload.get("summary", ""),
                requirements_json=payload.get("requirements") or {},
                eligible_nodes=payload.get("eligible_nodes") or [],
                rejected_nodes=payload.get("rejected_nodes") or [],
                missing_inputs=payload.get("missing_inputs") or [],
                assumptions=payload.get("assumptions") or [],
                evidence_refs=payload.get("evidence_refs") or [],
                created_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    # ------------------------------------------------------------------
