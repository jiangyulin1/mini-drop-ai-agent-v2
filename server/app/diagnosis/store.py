"""诊断控制层持久化访问。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_

from server.app.database import new_session
from server.app.models import (
    DiagnosisEventModel,
    DiagnosisEvidenceModel,
    DiagnosisNodeRunModel,
    DiagnosisOutboxModel,
    DiagnosisSessionModel,
    ProbeExecutionModel,
    TopologySnapshotModel,
)
from server.app.diagnosis.pipeline import PIPELINE_NODES, PIPELINE_VERSION, node_run_id
from server.app.diagnosis.audit_trace import TRACE_EVENT_TYPE, build_trace_step


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosisStore:
    def create_topology_snapshot(self, snapshot: dict[str, Any]) -> None:
        session = new_session()
        try:
            session.add(TopologySnapshotModel(
                id=snapshot["snapshot_id"],
                effective_at=snapshot["effective_at"],
                generated_at=snapshot["generated_at"],
                nodes_json=snapshot.get("nodes", []),
                edges_json=snapshot.get("edges", []),
                source_versions_json=snapshot.get("source_versions", {}),
                confidence_summary_json=snapshot.get("confidence_summary", {}),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_session(self, data: dict[str, Any]) -> None:
        now = utcnow()
        session = new_session()
        try:
            model = DiagnosisSessionModel(
                id=data["diagnosis_id"],
                creator_id=data["creator_id"],
                raw_query=data["raw_query"],
                normalized_intent_json=data.get("normalized_intent", {}),
                target_scope_json=data.get("target_scope", {}),
                requested_time_range_json=data.get("requested_time_range", {}),
                effective_time_range_json=data.get("effective_time_range", {}),
                topology_snapshot_id=data.get("topology_snapshot_id"),
                baseline_snapshot_id=data.get("baseline_snapshot_id"),
                status=data["status"],
                policy_profile=data["policy_profile"],
                risk_budget_json=data.get("risk_budget", {}),
                resource_budget_json=data.get("resource_budget", {}),
                budget_used_json=data.get("budget_used", {}),
                hypothesis_graph_json=data.get("hypothesis_graph", {}),
                evaluation_oracle_json=data.get("evaluation_oracle", {}),
                child_task_ids_json=data.get("child_task_ids", []),
                conclusion_versions_json=data.get("conclusion_versions", []),
                model_version=data["model_version"],
                planner_version=data["planner_version"],
                row_version=0,
                deadline_at=data["deadline_at"],
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            # 先 flush 父行再插入子行：无 relationship 的模型 SQLAlchemy
            # 不做拓扑排序，同事务内子行先 INSERT 会在外键检查时失败
            # （SQLite PRAGMA foreign_keys=ON 与 PostgreSQL 均立即检查）。
            session.flush()
            session.add(DiagnosisEventModel(
                diagnosis_id=model.id,
                event_type="diagnosis_created",
                from_status=None,
                to_status=model.status,
                payload_json={},
                created_at=now,
            ))
            for sequence, node_name in enumerate(PIPELINE_NODES, start=1):
                session.add(DiagnosisNodeRunModel(
                    id=node_run_id(model.id, node_name),
                    diagnosis_id=model.id,
                    node_name=node_name,
                    sequence=sequence,
                    status="PENDING",
                    attempt=0,
                    input_refs_json=[],
                    output_refs_json=[],
                    metrics_json={},
                    implementation_version=PIPELINE_VERSION,
                    updated_at=now,
                ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def initialize_pipeline(self, diagnosis_id: str) -> None:
        now = utcnow()
        session = new_session()
        try:
            for sequence, node_name in enumerate(PIPELINE_NODES, start=1):
                key = node_run_id(diagnosis_id, node_name)
                if session.get(DiagnosisNodeRunModel, key) is None:
                    session.add(DiagnosisNodeRunModel(
                        id=key,
                        diagnosis_id=diagnosis_id,
                        node_name=node_name,
                        sequence=sequence,
                        status="PENDING",
                        attempt=0,
                        input_refs_json=[],
                        output_refs_json=[],
                        metrics_json={},
                        implementation_version=PIPELINE_VERSION,
                        updated_at=now,
                    ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_pipeline_node(
        self,
        diagnosis_id: str,
        node_name: str,
        status: str,
        *,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        allowed_statuses = {"PENDING", "RUNNING", "WAITING", "COMPLETED", "SKIPPED", "FAILED"}
        if status not in allowed_statuses:
            raise ValueError(f"非法节点状态: {status}")
        session = new_session()
        try:
            model = session.get(DiagnosisNodeRunModel, node_run_id(diagnosis_id, node_name))
            if model is None:
                raise ValueError(f"诊断节点不存在: {diagnosis_id}/{node_name}")
            now = utcnow()
            if status == "RUNNING" and model.status != "RUNNING":
                model.attempt += 1
                model.started_at = now
                model.finished_at = None
                model.error_code = None
                model.error_message = None
            if status in {"COMPLETED", "SKIPPED", "FAILED"}:
                model.finished_at = now
            model.status = status
            if input_refs is not None:
                model.input_refs_json = list(dict.fromkeys(input_refs))
            if output_refs is not None:
                model.output_refs_json = list(dict.fromkeys(output_refs))
            if metrics is not None:
                model.metrics_json = metrics
            model.error_code = error_code
            model.error_message = (error_message or "")[:2000] or None
            model.updated_at = now
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_pipeline_nodes(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisNodeRunModel)
                .filter(DiagnosisNodeRunModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisNodeRunModel.sequence.asc())
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def get_session(self, diagnosis_id: str) -> dict[str, Any] | None:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            return model.to_dict() if model else None
        finally:
            session.close()

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisSessionModel)
                .order_by(DiagnosisSessionModel.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def list_active_sessions(self, terminal_statuses: set[str], limit: int = 100) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisSessionModel)
                .filter(
                    ~DiagnosisSessionModel.status.in_(terminal_statuses),
                    DiagnosisSessionModel.status != "PAUSED",
                )
                .order_by(DiagnosisSessionModel.updated_at.asc())
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def count_sessions(self) -> int:
        session = new_session()
        try:
            return session.query(DiagnosisSessionModel).count()
        finally:
            session.close()

    def update_session(self, diagnosis_id: str, *, expected_version: int | None = None, **fields: Any) -> dict[str, Any]:
        column_map = {
            "normalized_intent": "normalized_intent_json",
            "target_scope": "target_scope_json",
            "effective_time_range": "effective_time_range_json",
            "budget_used": "budget_used_json",
            "hypothesis_graph": "hypothesis_graph_json",
            "child_task_ids": "child_task_ids_json",
            "conclusion_versions": "conclusion_versions_json",
            "status": "status",
            "lease_owner": "lease_owner",
            "lease_until": "lease_until",
            "paused_from_status": "paused_from_status",
        }
        unknown = set(fields) - set(column_map)
        if unknown:
            raise ValueError(f"不允许更新诊断字段: {sorted(unknown)}")
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            version = model.row_version
            if expected_version is not None and version != expected_version:
                raise RuntimeError("diagnosis session CAS conflict")
            values = {column_map[key]: value for key, value in fields.items()}
            values.update({"row_version": version + 1, "updated_at": utcnow()})
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(DiagnosisSessionModel.id == diagnosis_id, DiagnosisSessionModel.row_version == version)
                .update(values, synchronize_session=False)
            )
            if changed != 1:
                raise RuntimeError("diagnosis session CAS conflict")
            session.commit()
            session.expire_all()
            return session.get(DiagnosisSessionModel, diagnosis_id).to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def transition(
        self,
        diagnosis_id: str,
        to_status: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            previous = model.status
            previous_version = model.row_version
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.status == previous,
                    DiagnosisSessionModel.row_version == previous_version,
                )
                .update({
                    "status": to_status, "row_version": previous_version + 1, "updated_at": utcnow(),
                }, synchronize_session=False)
            )
            if changed != 1:
                raise RuntimeError("diagnosis transition CAS conflict")
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type=event_type,
                from_status=previous,
                to_status=to_status,
                payload_json=payload or {},
                created_at=utcnow(),
            ))
            session.commit()
            session.expire_all()
            return session.get(DiagnosisSessionModel, diagnosis_id).to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def pause_session(self, diagnosis_id: str) -> dict[str, Any]:
        """Atomically suspend orchestration while preserving the resumable state."""
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            if model.status == "PAUSED":
                return model.to_dict()
            previous = model.status
            previous_version = model.row_version
            changed = session.query(DiagnosisSessionModel).filter(
                DiagnosisSessionModel.id == diagnosis_id,
                DiagnosisSessionModel.status == previous,
                DiagnosisSessionModel.row_version == previous_version,
            ).update({
                "status": "PAUSED",
                "paused_from_status": previous,
                "row_version": previous_version + 1,
                "updated_at": utcnow(),
            }, synchronize_session=False)
            if changed != 1:
                raise RuntimeError("diagnosis pause CAS conflict")
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type="diagnosis_paused",
                from_status=previous,
                to_status="PAUSED",
                payload_json={"paused_from_status": previous},
                created_at=utcnow(),
            ))
            session.commit()
            session.expire_all()
            return session.get(DiagnosisSessionModel, diagnosis_id).to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def resume_session(self, diagnosis_id: str) -> dict[str, Any]:
        """Atomically restore the workflow state captured by ``pause_session``."""
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            if model.status != "PAUSED":
                raise ValueError("诊断当前未暂停")
            target = model.paused_from_status
            if not target or target == "PAUSED":
                raise ValueError("诊断缺少可恢复状态")
            previous_version = model.row_version
            changed = session.query(DiagnosisSessionModel).filter(
                DiagnosisSessionModel.id == diagnosis_id,
                DiagnosisSessionModel.status == "PAUSED",
                DiagnosisSessionModel.row_version == previous_version,
            ).update({
                "status": target,
                "paused_from_status": None,
                "row_version": previous_version + 1,
                "updated_at": utcnow(),
            }, synchronize_session=False)
            if changed != 1:
                raise RuntimeError("diagnosis resume CAS conflict")
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type="diagnosis_resumed",
                from_status="PAUSED",
                to_status=target,
                payload_json={"resumed_to_status": target},
                created_at=utcnow(),
            ))
            session.commit()
            session.expire_all()
            return session.get(DiagnosisSessionModel, diagnosis_id).to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_event(
        self,
        diagnosis_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type=event_type,
                from_status=model.status,
                to_status=model.status,
                payload_json=payload or {},
                created_at=utcnow(),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_decision_trace(
        self,
        diagnosis_id: str,
        *,
        stage: str,
        component: str,
        decision: str,
        summary: str,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        alternatives: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one observable decision and chain it to the previous record."""
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"diagnosis {diagnosis_id} does not exist")
            previous_event = (
                session.query(DiagnosisEventModel)
                .filter(
                    DiagnosisEventModel.diagnosis_id == diagnosis_id,
                    DiagnosisEventModel.event_type == TRACE_EVENT_TYPE,
                )
                .order_by(DiagnosisEventModel.id.desc())
                .first()
            )
            previous = previous_event.payload_json if previous_event else {}
            payload = build_trace_step(
                diagnosis_id=diagnosis_id,
                sequence=int(previous.get("sequence", 0)) + 1,
                stage=stage,
                component=component,
                decision=decision,
                summary=summary,
                input_refs=input_refs,
                output_refs=output_refs,
                evidence_refs=evidence_refs,
                alternatives=alternatives,
                details=details,
                previous_hash=previous.get("step_hash"),
            )
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type=TRACE_EVENT_TYPE,
                from_status=model.status,
                to_status=model.status,
                payload_json=payload,
                created_at=utcnow(),
            ))
            session.commit()
            return payload
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def acquire_lease(self, diagnosis_id: str, owner: str, ttl_seconds: int = 30) -> bool:
        """短租约避免多个 API 实例同时推进同一会话。"""
        now = utcnow()
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                return False
            lease_until = model.lease_until
            if lease_until is not None and lease_until.tzinfo is None:
                lease_until = lease_until.replace(tzinfo=timezone.utc)
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.row_version == model.row_version,
                    or_(DiagnosisSessionModel.lease_until.is_(None), DiagnosisSessionModel.lease_until <= now,
                        DiagnosisSessionModel.lease_owner == owner),
                )
                .update({
                    "lease_owner": owner,
                    "lease_until": now + timedelta(seconds=ttl_seconds),
                    "row_version": model.row_version + 1,
                    "updated_at": now,
                }, synchronize_session=False)
            )
            session.commit()
            return changed == 1
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def renew_lease(self, diagnosis_id: str, owner: str, ttl_seconds: int = 30) -> bool:
        """续租：只延长 lease_until，不 bump row_version。

        诊断推进期间（_advance_locked）内部的 update/transition 全部依赖
        row_version CAS；若续租也 bump version 会让后续 CAS 全部失败。
        仅当仍是同一 owner 时续租，租约丢失后自然失效。
        """
        now = utcnow()
        session = new_session()
        try:
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.lease_owner == owner,
                )
                .update(
                    {"lease_until": now + timedelta(seconds=ttl_seconds)},
                    synchronize_session=False,
                )
            )
            session.commit()
            return changed == 1
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def release_lease(self, diagnosis_id: str, owner: str) -> None:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is not None:
                session.query(DiagnosisSessionModel).filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.lease_owner == owner,
                    DiagnosisSessionModel.row_version == model.row_version,
                ).update({
                    "lease_owner": None, "lease_until": None,
                    "row_version": model.row_version + 1, "updated_at": utcnow(),
                }, synchronize_session=False)
                session.commit()
        finally:
            session.close()

    def add_probe(self, probe: dict[str, Any]) -> dict[str, Any]:
        session = new_session()
        try:
            existing = session.get(ProbeExecutionModel, probe["step_id"])
            if existing is not None:
                return existing.to_dict()
            now = utcnow()
            model = ProbeExecutionModel(
                id=probe["step_id"],
                diagnosis_id=probe["diagnosis_id"],
                probe_id=probe["probe_id"],
                target_json=probe.get("target", {}),
                parameters_json=probe.get("parameters", {}),
                reason=probe["reason"],
                risk_level=probe["risk_level"],
                status=probe["status"],
                task_id=probe.get("task_id"),
                requires_approval=1 if probe.get("requires_approval") else 0,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_probe(self, step_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "task_id", "approved_by", "approved_at", "retry_count", "error_code", "error_message"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新探针字段: {sorted(unknown)}")
        session = new_session()
        try:
            model = session.get(ProbeExecutionModel, step_id)
            if model is None:
                raise ValueError(f"探针步骤 {step_id} 不存在")
            for key, value in fields.items():
                setattr(model, key, value)
            model.updated_at = utcnow()
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def enqueue_probe(self, step_id: str) -> dict[str, Any]:
        """Atomically mark a ready/approved step scheduled and create one outbox row."""
        session = new_session()
        try:
            probe = session.query(ProbeExecutionModel).filter(ProbeExecutionModel.id == step_id).with_for_update().first()
            if probe is None:
                raise ValueError(f"探针步骤 {step_id} 不存在")
            outbox = session.query(DiagnosisOutboxModel).filter(DiagnosisOutboxModel.step_id == step_id).first()
            if outbox is None:
                now = utcnow()
                outbox = DiagnosisOutboxModel(
                    id=f"outbox:{step_id}", diagnosis_id=probe.diagnosis_id, step_id=step_id,
                    status="PENDING", attempt=0, created_at=now, updated_at=now,
                )
                session.add(outbox)
            if probe.status in {"READY", "APPROVED", "PLANNED"}:
                probe.status = "SCHEDULED"
                probe.updated_at = utcnow()
            session.commit()
            return probe.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_pending_outbox(self, diagnosis_id: str, limit: int = 100) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisOutboxModel)
                .filter(DiagnosisOutboxModel.diagnosis_id == diagnosis_id, DiagnosisOutboxModel.status == "PENDING")
                .order_by(DiagnosisOutboxModel.created_at.asc()).limit(limit).all()
            )
            return [{"outbox_id": row.id, "step_id": row.step_id, "attempt": row.attempt} for row in rows]
        finally:
            session.close()

    def complete_outbox(self, outbox_id: str, error: str | None = None) -> None:
        session = new_session()
        try:
            row = session.get(DiagnosisOutboxModel, outbox_id)
            if row is not None:
                row.attempt += 1
                row.status = "FAILED" if error else "COMPLETED"
                row.last_error = (error or "")[:2000] or None
                row.updated_at = utcnow()
                session.commit()
        finally:
            session.close()

    def get_probe(self, step_id: str) -> dict[str, Any] | None:
        session = new_session()
        try:
            model = session.get(ProbeExecutionModel, step_id)
            return model.to_dict() if model else None
        finally:
            session.close()

    def list_probes(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(ProbeExecutionModel)
                .filter(ProbeExecutionModel.diagnosis_id == diagnosis_id)
                .order_by(ProbeExecutionModel.created_at.asc())
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def add_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        session = new_session()
        try:
            existing = session.get(DiagnosisEvidenceModel, evidence["evidence_id"])
            if existing is not None:
                return existing.to_dict()
            model = DiagnosisEvidenceModel(
                id=evidence["evidence_id"],
                diagnosis_id=evidence["diagnosis_id"],
                source_type=evidence["source_type"],
                source_system=evidence["source_system"],
                evidence_role=evidence.get("evidence_role", "incident"),
                target_json=evidence.get("target", {}),
                event_time_range_json=evidence.get("event_time_range", {}),
                ingestion_time=evidence.get("ingestion_time", utcnow()),
                query_or_probe=evidence["query_or_probe"],
                raw_artifact_ref=evidence.get("raw_artifact_ref"),
                derived_artifact_ref=evidence.get("derived_artifact_ref"),
                derivation_version=evidence.get("derivation_version", "v1"),
                observed_value_json=evidence.get("observed_value", {}),
                baseline_value_json=evidence.get("baseline_value", {}),
                anomaly_score_json=evidence.get("anomaly_score", {}),
                data_quality_json=evidence.get("data_quality", {}),
                integrity_hash=evidence["integrity_hash"],
                claim_links_json=evidence.get("claim_links", []),
            )
            session.add(model)
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_evidence(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisEvidenceModel)
                .filter(DiagnosisEvidenceModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisEvidenceModel.ingestion_time.asc())
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def get_topology(self, snapshot_id: str | None) -> dict[str, Any] | None:
        if not snapshot_id:
            return None
        session = new_session()
        try:
            model = session.get(TopologySnapshotModel, snapshot_id)
            return model.to_dict() if model else None
        finally:
            session.close()

    def get_detail(self, diagnosis_id: str) -> dict[str, Any] | None:
        item = self.get_session(diagnosis_id)
        if item is None:
            return None
        session = new_session()
        try:
            events = (
                session.query(DiagnosisEventModel)
                .filter(DiagnosisEventModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisEventModel.id.asc())
                .all()
            )
            item["events"] = [event.to_dict() for event in events]
        finally:
            session.close()
        item["topology_snapshot"] = self.get_topology(item.get("topology_snapshot_id"))
        item["probes"] = self.list_probes(diagnosis_id)
        coverage_status = {
            "READY": "QUEUED", "PLANNED": "PLANNED", "SCHEDULED": "SCHEDULED",
            "RUNNING": "RUNNING", "COMPLETED": "COMPLETED", "FAILED": "FAILED",
            "TIMED_OUT": "TIMED_OUT", "UNAVAILABLE": "UNAVAILABLE",
            "REJECTED": "REJECTED", "REJECTED_POLICY": "REJECTED",
            "WAITING_APPROVAL": "WAITING_APPROVAL", "SKIPPED": "SKIPPED",
        }
        item["coverage"] = [{
            "target": probe.get("target", {}).get("instance_id"),
            "requirement": probe.get("probe_id"),
            "status": coverage_status.get(probe.get("status"), probe.get("status")),
            "step_id": probe.get("step_id"),
            "task_id": probe.get("task_id"),
            "error_code": probe.get("error_code"),
        } for probe in item["probes"]]
        item["evidence"] = self.list_evidence(diagnosis_id)
        pipeline_nodes = self.list_pipeline_nodes(diagnosis_id)
        if not pipeline_nodes:
            # create_all 只会创建新表；为升级前的历史会话补一组明确标记的节点，
            # 避免前端把“没有历史节点数据”误判为仍在执行。
            self.initialize_pipeline(diagnosis_id)
            for node_name in PIPELINE_NODES:
                self.update_pipeline_node(
                    diagnosis_id, node_name, "SKIPPED",
                    metrics={"reason": "legacy_session_without_node_history"},
                )
            pipeline_nodes = self.list_pipeline_nodes(diagnosis_id)
        item["pipeline_nodes"] = pipeline_nodes
        conclusions = item.get("conclusion_versions", [])
        item["latest_conclusion"] = conclusions[-1] if conclusions else None
        return item
