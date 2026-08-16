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

from sqlalchemy.orm import Session as OrmSession

from server.app.models import (
    AcquisitionAssignmentModel,
    AgentCycleModel,
    AgentDecisionRecordModel,
    AgentProposalModel,
    AgentRuntimeTurnModel,
    AssistantMessageModel,
    CampaignRevisionModel,
    CaseContextSnapshotModel,
    CaseEvidenceModel,
    CausalEdgeModel,
    CausalGraphRevisionModel,
    CausalNodeModel,
    ClaimEvidenceBindingModel,
    ConclusionRevisionModel,
    DeploymentAssessmentModel,
    DomainOutboxModel,
    EvidenceGapModel,
    EvidenceProjectionModel,
    EvidenceReviewRevisionModel,
    ExecutionUnitModel,
    InvestigationRunModel,
    ModelRequestModel,
    ModelResponseModel,
    OperationSpecModel,
    RepairRecommendationModel,
    RuntimeWakeupModel,
    RuntimeWakeupSourceModel,
)
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
            return row.to_dict()

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
                existing.content_json = content
                existing.projection_hash = projection_hash
                existing.truncated = bool(truncated)
                existing.source_bytes = int(source_bytes or 0)
                existing.projected_bytes = projected_bytes
                existing.parser_version = parser_version
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
                evidence.status = "EXCLUDED" if decision == "EXCLUDED" else (
                    "LOW_TRUST" if decision == "LOW_TRUST" else "ACTIVE"
                )
                evidence.updated_at = now
            session.flush()
            return row.to_dict()

    def enqueue_domain_outbox(
        self, *, aggregate_type: str, aggregate_id: str, event_type: str,
        payload: dict[str, Any] | None = None, dedupe_key: str | None = None,
        available_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        dedupe_key = dedupe_key or f"{aggregate_type}:{aggregate_id}:{event_type}:{uuid4().hex}"
        with self._write_session() as session:
            existing = session.query(DomainOutboxModel).filter(
                DomainOutboxModel.dedupe_key == dedupe_key,
            ).first()
            if existing is not None:
                return existing.to_dict()
            row = DomainOutboxModel(
                outbox_id=self._new_id("outbox"),
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload or {},
                dedupe_key=dedupe_key,
                status="PENDING",
                available_at=available_at or now,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    def claim_domain_outbox(self, claimer: str, limit: int = 10) -> list[dict[str, Any]]:
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
                row.claim_expires_at = now + timedelta(seconds=120)
                row.attempts = int(row.attempts or 0) + 1
                row.updated_at = now
                results.append(row.to_dict())
            session.flush()
            return results

    def reclaim_expired_outbox(self, claimer: str, limit: int = 20) -> list[dict[str, Any]]:
        now = now_utc()
        with self._write_session() as session:
            rows = session.query(DomainOutboxModel).filter(
                DomainOutboxModel.status == "CLAIMED",
                DomainOutboxModel.claim_expires_at < now,
            ).order_by(DomainOutboxModel.created_at.asc()).limit(limit).all()
            results = []
            for row in rows:
                row.status = "PENDING"
                row.claimed_by = None
                row.claim_token = None
                row.claim_expires_at = None
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
            if claim_token and row.claim_token != claim_token:
                return row.to_dict()
            row.status = "DELIVERED"
            row.dispatch_outcome = dispatch_outcome or "DELIVERED"
            row.updated_at = now
            session.flush()
            return row.to_dict()

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
                    verification_state="UNVERIFIED",
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

    def resolve_evidence_gap(self, gap_id: str) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            row = session.get(EvidenceGapModel, gap_id)
            if row is None:
                return None
            row.status = "RESOLVED"
            row.resolved_at = now
            session.flush()
            return row.to_dict()

    def submit_conclusion_revision(
        self, *, case_id: str, tenant_id: str, investigation_run_id: str,
        state: str, causal_graph_revision_id: str | None = None,
        claims: list[dict[str, Any]] | None = None,
        primary_root_causes: list[dict[str, Any]] | None = None,
        contributing_factors: list[dict[str, Any]] | None = None,
        amplifiers: list[dict[str, Any]] | None = None,
        propagated_effects: list[dict[str, Any]] | None = None,
        symptoms: list[dict[str, Any]] | None = None,
        coincidental_anomalies: list[dict[str, Any]] | None = None,
        ruled_out: list[dict[str, Any]] | None = None,
        evidence_gap_ids: list[str] | None = None,
        limitations: list[str] | None = None,
        abstention_reason: str | None = None,
        report_text: str | None = None,
        created_from_cycle_id: str | None = None,
        model_request_id: str | None = None,
        verifier_version: str = "causal-report-verifier.v1",
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            previous = session.query(ConclusionRevisionModel).filter(
                ConclusionRevisionModel.case_id == case_id,
                ConclusionRevisionModel.tenant_id == tenant_id,
            ).order_by(ConclusionRevisionModel.revision.desc()).first()
            revision = int(previous.revision or 0) + 1 if previous else 1
            conclusion = ConclusionRevisionModel(
                conclusion_id=self._new_id("concl"),
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
                evidence_gap_ids=evidence_gap_ids or [],
                recommendation_ids=[],
                limitations=limitations or [],
                abstention_reason=abstention_reason,
                report_text=report_text,
                created_from_cycle_id=created_from_cycle_id,
                model_request_id=model_request_id,
                verifier_version=verifier_version,
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
                    created_at=now,
                )
                session.add(binding)
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
            if conclusion is None:
                return None
            bindings = session.query(ClaimEvidenceBindingModel).filter(
                ClaimEvidenceBindingModel.conclusion_id == conclusion.conclusion_id,
            ).all()
            result = conclusion.to_dict()
            result["claim_evidence_bindings"] = [row.to_dict() for row in bindings]
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
