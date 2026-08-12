"""SQLAlchemy 持久化 Repository。

接口与 InMemoryRepository 保持一致，替换时 gRPC 服务和 HTTP handler
无需修改调用代码。通过 DATABASE_URL 切换 PostgreSQL / SQLite 后端。
"""

from __future__ import annotations

import json
import hashlib
import os
import threading
import time

from collections import deque
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from server.app.database import new_session
from server.app.models import (
    AnalysisJobModel,
    AnalyzerWorkerModel,
    AgentMetricSnapshotModel,
    AgentModel,
    ArtifactModel,
    AuditLogModel,
    AuthorizationGrantModel,
    CaseHypothesisEdgeModel,
    CaseHypothesisNodeModel,
    CaseEventModel,
    ContextPacketModel,
    DiagnosisReportModel,
    DiagnosisRunModel,
    DiagnosisSessionModel,
    DiagnosisToolResultModel,
    ActionAttemptModel,
    CaseCommandModel,
    CaseRuntimeLeaseModel,
    SystemControlModel,
    IncidentCaseModel,
    InvestigationIterationModel,
    ModelAttemptModel,
    ProbeExecutionModel,
    RCAFeedbackModel,
    RCAFeedbackWeightModel,
    RepairPlanModel,
    StatusEventModel,
    TaskModel,
    TaskAttemptModel,
)
from server.app import storage as store
from server.app.logging_utils import log_event
from server.app.prometheus_metrics import record_task_transition
from server.app.rca.models import FeedbackPrior
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import (
    AnalysisStatus,
    Actor,
    CollectionStatus,
    StatusEvent,
    TaskStatus,
    build_status_event,
    now_utc,
)


def _collection_queue_ttl_sec(duration_sec: int) -> int:
    """Bound how long a collection request may remain undispatched."""

    try:
        configured = int(os.getenv("MINI_DROP_COLLECTION_QUEUE_TTL_SEC", "900"))
    except ValueError:
        configured = 900
    return max(int(duration_sec) + 60, configured, 60)


class SqlRepository:
    """SQLAlchemy 持久化 Repository。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Compatibility shim for older tests; dispatch now reads PENDING tasks from DB.
        self._task_queues: dict[str, deque[str]] = {}
        self.agent_metrics: dict[str, dict[str, Any]] = {}
        # TTL 缓存：key → (expires_at, value)
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cached(self, key: str, ttl_sec: float, factory):
        """带 TTL 的简单缓存。

        如果 key 未过期则返回缓存值，否则调用 factory() 重新计算并缓存。
        用 try/except KeyError 替代先 in 后 [] 的两步访问：写事务会在
        self._lock 内 clear() 整个缓存，无锁读若恰好落在两步之间会抛
        KeyError（偶发 500）。
        """
        now = time.monotonic()
        try:
            expires_at, value = self._cache[key]
        except KeyError:
            pass
        else:
            if now < expires_at:
                return value
        value = factory()
        self._cache[key] = (now + ttl_sec, value)
        return value

    @contextmanager
    def _write_session(self):
        """写事务 context manager：加锁 → 建 session → 提交/回滚 → 关闭 → 清缓存 → 提交后通知。

        用于 register_agent / create_task / transition_task 等写操作。
        自动处理 lock → new_session → commit → close → cache_invalidation。

        SSE 事件通过 ``session.info["_post_commit_notifications"]`` 注册，
        只在 commit 成功后才发布——事务回滚时订阅者不会收到虚假事件。
        """
        with self._lock:
            session = new_session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                hooks = list(session.info.get("_post_commit_notifications", []))
                session.close()
            # 写入后清除所有 TTL 缓存，确保下次读取拿到最新数据
            self._cache.clear()
            for hook in hooks:
                hook()

    @staticmethod
    def _locked_task(session: OrmSession, task_id: str) -> TaskModel | None:
        """按主键读取任务；PostgreSQL 下加行锁，防止多副本并发迁移同一任务。"""
        query = session.query(TaskModel).filter(TaskModel.id == task_id)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def _locked_case(
        session: OrmSession, case_id: str, tenant_id: str,
    ) -> IncidentCaseModel | None:
        query = session.query(IncidentCaseModel).filter(
            IncidentCaseModel.id == case_id,
            IncidentCaseModel.tenant_id == tenant_id,
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def _notify_after_commit(session: OrmSession, event_type: str, data: dict[str, Any]) -> None:
        """注册一个只在事务提交成功后执行的 SSE 通知。"""
        hooks = session.info.setdefault("_post_commit_notifications", [])
        from server.app.event_bus import BUS
        hooks.append(lambda: BUS.publish(event_type, data))

    @contextmanager
    def _read_session(self):
        """只读 session context manager：建 session → 查询 → 关闭。

        用于 agents / tasks / events / artifacts 等只读查询。
        不加锁，不提交事务。
        """
        session = new_session()
        try:
            yield session
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Incident Case collaboration
    # ------------------------------------------------------------------

    def create_incident_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        from server.app.case_collaboration import initial_case_state, initial_summary

        now = now_utc()
        target_scope = payload.get("target_scope") or {}
        state, state_reason = initial_case_state(target_scope)
        summary = initial_summary(
            target_scope=target_scope,
            recovery_goal=payload["recovery_goal"],
            state=state,
        )
        case = IncidentCaseModel(
            id=f"case_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:10]}",
            tenant_id=payload["tenant_id"],
            created_by=payload["created_by"],
            diagnosis_session_id=payload.get("diagnosis_session_id"),
            source_task_id=payload.get("source_task_id"),
            title=payload["title"],
            problem_description=payload["problem_description"],
            recovery_goal=payload["recovery_goal"],
            run_mode=payload["run_mode"],
            environment=payload["environment"],
            target_scope_json=target_scope,
            time_range_json=payload.get("time_range") or {},
            state=state.value,
            state_reason=state_reason,
            impact_json=summary["impact"],
            current_finding_json=summary["current_finding"],
            current_activity_json=summary["what_ai_is_doing"],
            need_user_json=summary["need_you"],
            recovery_json=summary["recovery"],
            scope_revision=1,
            row_version=0,
            created_at=now,
            updated_at=now,
        )
        with self._write_session() as session:
            if case.diagnosis_session_id and session.get(
                DiagnosisSessionModel, case.diagnosis_session_id,
            ) is None:
                raise ValueError("DIAGNOSIS_SESSION_NOT_FOUND")
            if case.source_task_id and session.get(TaskModel, case.source_task_id) is None:
                raise ValueError("SOURCE_TASK_NOT_FOUND")
            session.add(case)
            session.flush()
            event = CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="case_created",
                actor_id=case.created_by,
                payload_json={
                    "state": case.state,
                    "state_reason": case.state_reason,
                    "run_mode": case.run_mode,
                    "scope_revision": case.scope_revision,
                },
                created_at=now,
            )
            session.add(event)
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": case.tenant_id,
                "state": case.state,
            })
            if case.state == "NEEDS_SCOPE_CONFIRMATION":
                self._notify_after_commit(session, "scope_confirmation_required", {
                    "case_id": case.id,
                    "tenant_id": case.tenant_id,
                })
            result = case.to_dict()
        return result

    def get_incident_case(self, case_id: str, tenant_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(IncidentCaseModel).filter(
                IncidentCaseModel.id == case_id,
                IncidentCaseModel.tenant_id == tenant_id,
            ).first()
            return row.to_dict() if row else None

    def list_incident_cases(
        self,
        tenant_id: str,
        *,
        state: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(IncidentCaseModel).filter(
                IncidentCaseModel.tenant_id == tenant_id,
            )
            if state:
                query = query.filter(IncidentCaseModel.state == state)
            rows = query.order_by(IncidentCaseModel.updated_at.desc()).offset(offset).limit(limit).all()
            return [row.to_dict() for row in rows]

    def count_incident_cases(self, tenant_id: str, *, state: str = "") -> int:
        with self._read_session() as session:
            query = session.query(IncidentCaseModel).filter(
                IncidentCaseModel.tenant_id == tenant_id,
            )
            if state:
                query = query.filter(IncidentCaseModel.state == state)
            return query.count()

    def list_case_events(
        self,
        case_id: str,
        tenant_id: str,
        *,
        limit: int = 200,
        after_id: int = 0,
    ) -> list[dict[str, Any]] | None:
        with self._read_session() as session:
            exists = session.query(IncidentCaseModel.id).filter(
                IncidentCaseModel.id == case_id,
                IncidentCaseModel.tenant_id == tenant_id,
            ).first()
            if exists is None:
                return None
            rows = session.query(CaseEventModel).filter(
                CaseEventModel.case_id == case_id,
                CaseEventModel.tenant_id == tenant_id,
                CaseEventModel.id > after_id,
            ).order_by(CaseEventModel.id.asc()).limit(limit).all()
            return [row.to_dict() for row in rows]

    def append_case_message(
        self,
        case_id: str,
        tenant_id: str,
        *,
        actor_id: str,
        content: str,
        kind: str,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            if case.state == "STOPPED":
                raise ValueError("CASE_STOPPED")
            event = CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="user_message",
                actor_id=actor_id,
                payload_json={"kind": kind, "content": content},
                created_at=now,
            )
            session.add(event)
            case.updated_at = now
            case.row_version += 1
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": case.state,
                "row_version": case.row_version,
            })
            return event.to_dict()

    def record_case_event(
        self,
        case_id: str,
        tenant_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        actor_id: str = "system",
    ) -> dict[str, Any] | None:
        """写入一条非用户消息的 Case 时间线事件（验证/人工动作/系统事实）。"""
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            if case.state == "STOPPED":
                raise ValueError("CASE_STOPPED")
            event = CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type=event_type,
                actor_id=actor_id,
                payload_json=payload,
                created_at=now,
            )
            session.add(event)
            case.updated_at = now
            case.row_version += 1
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": case.state,
                "row_version": case.row_version,
            })
            return event.to_dict()

    def correct_incident_case(
        self,
        case_id: str,
        tenant_id: str,
        *,
        actor_id: str,
        changes: dict[str, Any],
        reason: str,
        expected_row_version: int | None = None,
    ) -> dict[str, Any] | None:
        from server.app.case_collaboration import initial_case_state, initial_summary

        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            if case.state in {"STOPPED", "RESOLVED"}:
                raise ValueError("CASE_TERMINAL")
            if expected_row_version is not None and case.row_version != expected_row_version:
                raise ValueError("CASE_VERSION_CONFLICT")

            superseded_diagnosis_id = case.diagnosis_session_id
            session.query(CaseHypothesisNodeModel).filter(
                CaseHypothesisNodeModel.case_id == case.id,
                CaseHypothesisNodeModel.tenant_id == tenant_id,
                CaseHypothesisNodeModel.status.notin_(["RULED_OUT", "WEAKENED"]),
            ).update({
                CaseHypothesisNodeModel.status: "WEAKENED",
                CaseHypothesisNodeModel.missing_evidence_json: [
                    "Case scope revision changed; hypothesis requires revalidation",
                ],
                CaseHypothesisNodeModel.updated_at: now,
            }, synchronize_session=False)
            changed_fields: list[str] = []
            direct_fields = {
                "problem_description": "problem_description",
                "recovery_goal": "recovery_goal",
                "environment": "environment",
            }
            for input_name, attr_name in direct_fields.items():
                if input_name in changes:
                    setattr(case, attr_name, changes[input_name])
                    changed_fields.append(input_name)
            if "target_scope" in changes:
                case.target_scope_json = changes["target_scope"] or {}
                changed_fields.append("target_scope")
            if "time_range" in changes:
                case.time_range_json = changes["time_range"] or {}
                changed_fields.append("time_range")

            state, state_reason = initial_case_state(case.target_scope_json or {})
            summary = initial_summary(
                target_scope=case.target_scope_json or {},
                recovery_goal=case.recovery_goal,
                state=state,
            )
            if case.state != "PAUSED":
                case.state = state.value
                case.state_reason = f"case_corrected:{state_reason}"
            case.current_finding_json = {
                "status": "invalidated",
                "statement": "范围或恢复目标已被用户修正，旧判断等待重新验证",
                "evidence_refs": [],
            }
            case.current_activity_json = summary["what_ai_is_doing"]
            case.need_user_json = summary["need_you"]
            case.recovery_json = {**summary["recovery"], "status": "not_started"}
            case.diagnosis_session_id = None
            case.scope_revision += 1
            case.row_version += 1
            case.updated_at = now
            event = CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="case_corrected",
                actor_id=actor_id,
                payload_json={
                    "reason": reason,
                    "changed_fields": changed_fields,
                    "scope_revision": case.scope_revision,
                    "invalidates_pending_plan": True,
                    "superseded_diagnosis_id": superseded_diagnosis_id,
                    "state": case.state,
                },
                created_at=now,
            )
            session.add(event)
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": case.state,
                "scope_revision": case.scope_revision,
            })
            if case.state == "NEEDS_SCOPE_CONFIRMATION":
                self._notify_after_commit(session, "scope_confirmation_required", {
                    "case_id": case.id,
                    "tenant_id": tenant_id,
                })
            return case.to_dict()

    def transition_incident_case(
        self,
        case_id: str,
        tenant_id: str,
        *,
        actor_id: str,
        action: str,
        reason: str,
        expected_row_version: int | None = None,
    ) -> dict[str, Any] | None:
        from server.app.case_collaboration import initial_case_state

        if action not in {"pause", "resume", "stop", "resolve"}:
            raise ValueError("INVALID_CASE_ACTION")
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            if expected_row_version is not None and case.row_version != expected_row_version:
                raise ValueError("CASE_VERSION_CONFLICT")

            previous = case.state
            if action == "pause":
                if previous in {"RESOLVED", "INSUFFICIENT_EVIDENCE", "STOPPED"}:
                    raise ValueError("CASE_TERMINAL")
                target = "PAUSED"
                event_type = "case_paused"
            elif action == "resume":
                if previous != "PAUSED":
                    raise ValueError("CASE_NOT_PAUSED")
                target, _ = initial_case_state(case.target_scope_json or {})
                target = target.value
                event_type = "case_resumed"
            elif action == "stop":
                if previous in {"RESOLVED", "INSUFFICIENT_EVIDENCE"}:
                    raise ValueError("CASE_TERMINAL")
                target = "STOPPED"
                event_type = "case_stopped"
            else:
                if previous == "STOPPED":
                    raise ValueError("CASE_TERMINAL")
                target = "RESOLVED"
                event_type = "case_resolved"

            if previous == target:
                return case.to_dict()
            case.state = target
            case.state_reason = reason
            case.updated_at = now
            case.row_version += 1
            if target == "STOPPED":
                case.stopped_at = now
                case.current_activity_json = {
                    "status": "stopped",
                    "message": "Case 已停止，不再执行新的调查动作",
                }
                session.query(AuthorizationGrantModel).filter(
                    AuthorizationGrantModel.case_id == case.id,
                    AuthorizationGrantModel.tenant_id == tenant_id,
                    AuthorizationGrantModel.status == "ACTIVE",
                ).update({
                    AuthorizationGrantModel.status: "REVOKED",
                    AuthorizationGrantModel.revoked_at: now,
                    AuthorizationGrantModel.revoked_by: actor_id,
                }, synchronize_session=False)
            elif target == "RESOLVED":
                case.resolved_at = now
                case.current_activity_json = {
                    "status": "resolved",
                    "message": "用户确认问题已解决",
                }
                case.need_user_json = {"required": False, "question": ""}
                case.recovery_json = {
                    **(case.recovery_json or {}),
                    "status": "verified",
                    "stable_since": now.isoformat(),
                }
                session.query(AuthorizationGrantModel).filter(
                    AuthorizationGrantModel.case_id == case.id,
                    AuthorizationGrantModel.tenant_id == tenant_id,
                    AuthorizationGrantModel.status == "ACTIVE",
                ).update({
                    AuthorizationGrantModel.status: "REVOKED",
                    AuthorizationGrantModel.revoked_at: now,
                    AuthorizationGrantModel.revoked_by: actor_id,
                }, synchronize_session=False)

            session.add(CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type=event_type,
                actor_id=actor_id,
                payload_json={
                    "from_state": previous,
                    "to_state": target,
                    "reason": reason,
                },
                created_at=now,
            ))
            session.add(AuditLogModel(
                event_type=event_type.upper(),
                message=f"Case {case.id}: {event_type}",
                meta_json={
                    "case_id": case.id,
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "from_state": previous,
                    "to_state": target,
                    "reason": reason,
                },
                created_at=now,
            ))
            session.flush()
            self._notify_after_commit(session, event_type, {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": target,
            })
            return case.to_dict()

    def update_case_agent_loop(
        self,
        case_id: str,
        tenant_id: str,
        *,
        actor_id: str,
        loop: dict[str, Any],
        event_type: str,
        detail: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist autonomous loop progress in the Case recovery envelope."""
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            if case.state in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
                return case.to_dict()
            case.recovery_json = {**(case.recovery_json or {}), "agent_loop": loop}
            case.current_activity_json = {
                "status": str(loop.get("phase") or "unknown").lower(),
                "message": event_type,
                "diagnosis_session_id": loop.get("diagnosis_id"),
            }
            case.updated_at = now
            case.row_version += 1
            session.add(CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type=event_type,
                actor_id=actor_id,
                payload_json={"agent_loop": loop, **detail},
                created_at=now,
            ))
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": case.state,
                "agent_phase": loop.get("phase"),
            })
            return case.to_dict()

    def update_case_instance_pid(
        self,
        case_id: str,
        tenant_id: str,
        *,
        actor_id: str,
        agent_id: str,
        previous_pid: int,
        new_pid: int,
        container_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Refresh a replaced process identity without invalidating diagnosis history."""
        if new_pid <= 0:
            raise ValueError("INVALID_PID")
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            scope = dict(case.target_scope_json or {})
            instances = [dict(item) for item in scope.get("instances") or []]
            matched = False
            for item in instances:
                if item.get("agent_id") != agent_id or int(item.get("pid") or 0) != int(previous_pid):
                    continue
                item["pid"] = int(new_pid)
                if container_id:
                    item["container_id"] = container_id
                matched = True
            if not matched:
                return case.to_dict()
            scope["instances"] = instances
            case.target_scope_json = scope
            case.updated_at = now
            case.row_version += 1
            session.add(CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="agent_target_refreshed",
                actor_id=actor_id,
                payload_json={
                    "agent_id": agent_id,
                    "previous_pid": previous_pid,
                    "new_pid": new_pid,
                    "container_id": container_id,
                },
                created_at=now,
            ))
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": case.state,
            })
            return case.to_dict()

    def attach_case_diagnosis(
        self,
        case_id: str,
        tenant_id: str,
        *,
        diagnosis_id: str,
        actor_id: str,
        expected_row_version: int | None = None,
    ) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                return None
            if case.state in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
                raise ValueError("CASE_NOT_INVESTIGATABLE")
            if case.diagnosis_session_id:
                if case.diagnosis_session_id == diagnosis_id:
                    return case.to_dict()
                raise ValueError("CASE_DIAGNOSIS_ALREADY_ATTACHED")
            if expected_row_version is not None and case.row_version != expected_row_version:
                raise ValueError("CASE_VERSION_CONFLICT")
            diagnosis = session.get(DiagnosisSessionModel, diagnosis_id)
            if diagnosis is None:
                raise ValueError("DIAGNOSIS_SESSION_NOT_FOUND")
            previous_state = case.state
            case.diagnosis_session_id = diagnosis_id
            if diagnosis.status in {"NEEDS_SCOPE_CONFIRMATION", "WAITING_APPROVAL"}:
                case.state = "WAITING_USER"
                case.state_reason = diagnosis.status.lower()
                case.need_user_json = {
                    "required": True,
                    "question": (
                        "请确认服务实例、宿主机或 PID 范围"
                        if diagnosis.status == "NEEDS_SCOPE_CONFIRMATION"
                        else "请审查待批准的诊断探针"
                    ),
                }
                case.current_activity_json = {
                    "status": "waiting_user",
                    "message": "关联诊断正在等待用户输入",
                    "diagnosis_session_id": diagnosis_id,
                }
            elif diagnosis.status in {
                "INSUFFICIENT_EVIDENCE", "BUDGET_EXHAUSTED", "TOPOLOGY_UNAVAILABLE",
            }:
                case.state = "INSUFFICIENT_EVIDENCE"
                case.state_reason = diagnosis.status.lower()
                case.current_activity_json = {
                    "status": "stopped",
                    "message": f"关联诊断结束：{diagnosis.status}",
                    "diagnosis_session_id": diagnosis_id,
                }
            elif diagnosis.status in {"COMPLETED", "PARTIAL_COMPLETED"}:
                case.state = "RECOVERY_PLANNING"
                case.state_reason = "diagnosis_concluded"
                case.current_activity_json = {
                    "status": "recovery_planning",
                    "message": "诊断已形成结论，等待恢复方案",
                    "diagnosis_session_id": diagnosis_id,
                }
            else:
                case.state = "INVESTIGATING"
                case.state_reason = "diagnosis_started"
                case.current_activity_json = {
                    "status": "investigating",
                    "message": "关联诊断正在推进",
                    "diagnosis_session_id": diagnosis_id,
                }
            case.row_version += 1
            case.updated_at = now
            session.add(CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="diagnosis_started",
                actor_id=actor_id,
                payload_json={
                    "diagnosis_session_id": diagnosis_id,
                    "from_state": previous_state,
                    "to_state": case.state,
                },
                created_at=now,
            ))
            session.flush()
            self._notify_after_commit(session, "case_summary_updated", {
                "case_id": case.id,
                "tenant_id": tenant_id,
                "state": case.state,
                "diagnosis_session_id": diagnosis_id,
            })
            return case.to_dict()

    def create_context_packet(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, payload["case_id"], payload["tenant_id"])
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            latest = session.query(ContextPacketModel.iteration_no).filter(
                ContextPacketModel.case_id == case.id,
                ContextPacketModel.tenant_id == case.tenant_id,
            ).order_by(ContextPacketModel.iteration_no.desc()).first()
            iteration_no = int(payload.get("iteration_no", (latest[0] + 1) if latest else 0))
            packet = ContextPacketModel(
                id=f"ctx_{uuid4().hex}",
                case_id=case.id,
                tenant_id=case.tenant_id,
                schema_version=payload["schema_version"],
                purpose=payload["purpose"],
                iteration_no=iteration_no,
                payload_json=payload["payload"],
                projection_stats_json=payload.get("projection_stats") or {},
                source_versions_json=payload.get("source_versions") or {},
                content_hash=payload["content_hash"],
                created_by=payload["created_by"],
                created_at=now,
            )
            session.add(packet)
            session.flush()
            session.add(CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="context_packet_created",
                actor_id=payload["created_by"],
                payload_json={
                    "context_packet_id": packet.id,
                    "schema_version": packet.schema_version,
                    "purpose": packet.purpose,
                    "iteration_no": packet.iteration_no,
                    "content_hash": packet.content_hash,
                },
                created_at=now,
            ))
            return packet.to_dict()

    def list_context_packets(
        self,
        case_id: str,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]] | None:
        with self._read_session() as session:
            exists = session.query(IncidentCaseModel.id).filter(
                IncidentCaseModel.id == case_id,
                IncidentCaseModel.tenant_id == tenant_id,
            ).first()
            if exists is None:
                return None
            rows = session.query(ContextPacketModel).filter(
                ContextPacketModel.case_id == case_id,
                ContextPacketModel.tenant_id == tenant_id,
            ).order_by(ContextPacketModel.iteration_no.desc()).offset(offset).limit(limit).all()
            return [row.to_dict() for row in rows]

    def record_model_attempt(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._write_session() as session:
            packet = session.query(ContextPacketModel).filter(
                ContextPacketModel.id == payload["context_packet_id"],
                ContextPacketModel.case_id == payload["case_id"],
                ContextPacketModel.tenant_id == payload["tenant_id"],
            ).first()
            if packet is None:
                raise ValueError("CONTEXT_PACKET_NOT_FOUND")
            attempt = ModelAttemptModel(
                id=f"model_attempt_{uuid4().hex}",
                context_packet_id=packet.id,
                case_id=packet.case_id,
                tenant_id=packet.tenant_id,
                provider=payload["provider"],
                model=payload["model"],
                model_snapshot=payload.get("model_snapshot"),
                prompt_version=payload["prompt_version"],
                output_schema=payload["output_schema"],
                status=payload["status"],
                latency_ms=max(0, int(payload.get("latency_ms", 0))),
                input_tokens=payload.get("input_tokens"),
                output_tokens=payload.get("output_tokens"),
                response_hash=payload.get("response_hash"),
                error_code=payload.get("error_code"),
                started_at=payload["started_at"],
                finished_at=payload["finished_at"],
            )
            session.add(attempt)
            session.flush()
            return attempt.to_dict()

    def list_model_attempts(
        self,
        case_id: str,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]] | None:
        with self._read_session() as session:
            exists = session.query(IncidentCaseModel.id).filter(
                IncidentCaseModel.id == case_id,
                IncidentCaseModel.tenant_id == tenant_id,
            ).first()
            if exists is None:
                return None
            rows = session.query(ModelAttemptModel).filter(
                ModelAttemptModel.case_id == case_id,
                ModelAttemptModel.tenant_id == tenant_id,
            ).order_by(ModelAttemptModel.started_at.desc()).offset(offset).limit(limit).all()
            return [row.to_dict() for row in rows]

    def sync_case_hypothesis_graph(
        self,
        case_id: str,
        tenant_id: str,
        *,
        graph: dict[str, Any],
        source: str,
        actor_id: str,
    ) -> dict[str, Any]:
        now = now_utc()
        status_map = {
            "UNTESTED": "PROPOSED",
            "PROPOSED": "PROPOSED",
            "ACTIVE": "ACTIVE",
            "SUPPORTED": "ACTIVE",
            "WEAKENED": "WEAKENED",
            "RULED_OUT": "RULED_OUT",
            "CONFIRMED": "CONFIRMED",
            "UNKNOWN": "UNKNOWN",
        }
        hypotheses = list(graph.get("hypotheses") or [])
        if not any(item.get("hypothesis_id") == "OTHER_UNKNOWN" for item in hypotheses):
            hypotheses.append({
                "hypothesis_id": "OTHER_UNKNOWN",
                "statement": "当前候选集合之外仍可能存在未知原因",
                "status": "UNKNOWN",
                "missing_evidence": ["需要能够区分开放集未知原因的新证据"],
            })
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            incoming_ids: set[str] = set()
            changes: list[dict[str, Any]] = []
            for item in hypotheses:
                hypothesis_id = str(item.get("hypothesis_id") or "").strip()
                if not hypothesis_id:
                    continue
                incoming_ids.add(hypothesis_id)
                status = status_map.get(str(item.get("status") or "UNTESTED"), "PROPOSED")
                statement = str(
                    item.get("statement")
                    or item.get("description")
                    or item.get("type")
                    or hypothesis_id
                )[:4000]
                row = session.query(CaseHypothesisNodeModel).filter(
                    CaseHypothesisNodeModel.case_id == case_id,
                    CaseHypothesisNodeModel.tenant_id == tenant_id,
                    CaseHypothesisNodeModel.hypothesis_id == hypothesis_id,
                ).first()
                previous_status = row.status if row else None
                if row is None:
                    row = CaseHypothesisNodeModel(
                        id=f"hyp_node_{uuid4().hex}",
                        case_id=case_id,
                        tenant_id=tenant_id,
                        hypothesis_id=hypothesis_id,
                        created_at=now,
                        revision=1,
                    )
                    session.add(row)
                else:
                    row.revision += 1
                row.statement = statement
                row.root_entity = item.get("root_entity") or item.get("target_ref")
                row.mechanism = item.get("mechanism") or item.get("type")
                row.affected_entities_json = item.get("affected_entities") or []
                row.status = status
                row.supporting_evidence_refs_json = item.get("supporting_evidence_refs") or []
                row.contradicting_evidence_refs_json = item.get("contradicting_evidence_refs") or []
                row.missing_evidence_json = item.get("missing_evidence") or []
                row.alternatives_json = item.get("alternatives") or ["OTHER_UNKNOWN"]
                row.score_components_json = item.get("score_components") or {
                    "evidence_score": item.get("evidence_score", 0),
                }
                row.source = source
                row.updated_at = now
                changes.append({
                    "hypothesis_id": hypothesis_id,
                    "from_status": previous_status,
                    "to_status": status,
                    "revision": row.revision,
                })

            session.query(CaseHypothesisEdgeModel).filter(
                CaseHypothesisEdgeModel.case_id == case_id,
                CaseHypothesisEdgeModel.tenant_id == tenant_id,
            ).delete(synchronize_session=False)
            for edge in graph.get("edges") or []:
                source_id = edge.get("source") or edge.get("source_hypothesis_id")
                target_id = edge.get("target") or edge.get("target_hypothesis_id")
                if source_id not in incoming_ids or target_id not in incoming_ids:
                    continue
                session.add(CaseHypothesisEdgeModel(
                    id=f"hyp_edge_{uuid4().hex}",
                    case_id=case_id,
                    tenant_id=tenant_id,
                    source_hypothesis_id=source_id,
                    target_hypothesis_id=target_id,
                    relation=str(edge.get("relation") or "ALTERNATIVE_TO")[:32],
                    metadata_json=edge.get("metadata") or {},
                    created_at=now,
                ))
            session.add(CaseEventModel(
                case_id=case_id,
                tenant_id=tenant_id,
                event_type="hypothesis_graph_updated",
                actor_id=actor_id,
                payload_json={"changes": changes, "source": source},
                created_at=now,
            ))
            session.flush()
            nodes = session.query(CaseHypothesisNodeModel).filter(
                CaseHypothesisNodeModel.case_id == case_id,
                CaseHypothesisNodeModel.tenant_id == tenant_id,
            ).order_by(CaseHypothesisNodeModel.hypothesis_id.asc()).all()
            edges = session.query(CaseHypothesisEdgeModel).filter(
                CaseHypothesisEdgeModel.case_id == case_id,
                CaseHypothesisEdgeModel.tenant_id == tenant_id,
            ).all()
            return {
                "hypotheses": [row.to_dict() for row in nodes],
                "edges": [row.to_dict() for row in edges],
            }

    def get_case_hypothesis_graph(
        self, case_id: str, tenant_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            exists = session.query(IncidentCaseModel.id).filter(
                IncidentCaseModel.id == case_id,
                IncidentCaseModel.tenant_id == tenant_id,
            ).first()
            if exists is None:
                return None
            nodes = session.query(CaseHypothesisNodeModel).filter(
                CaseHypothesisNodeModel.case_id == case_id,
                CaseHypothesisNodeModel.tenant_id == tenant_id,
            ).order_by(CaseHypothesisNodeModel.hypothesis_id.asc()).all()
            edges = session.query(CaseHypothesisEdgeModel).filter(
                CaseHypothesisEdgeModel.case_id == case_id,
                CaseHypothesisEdgeModel.tenant_id == tenant_id,
            ).all()
            return {
                "hypotheses": [row.to_dict() for row in nodes],
                "edges": [row.to_dict() for row in edges],
            }

    def create_investigation_iteration(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, payload["case_id"], payload["tenant_id"])
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            latest = session.query(InvestigationIterationModel.iteration_no).filter(
                InvestigationIterationModel.case_id == case.id,
                InvestigationIterationModel.tenant_id == case.tenant_id,
            ).order_by(InvestigationIterationModel.iteration_no.desc()).first()
            iteration_no = int(payload.get("iteration_no", (latest[0] + 1) if latest else 0))
            iteration = InvestigationIterationModel(
                id=f"iteration_{uuid4().hex}",
                case_id=case.id,
                tenant_id=case.tenant_id,
                iteration_no=iteration_no,
                context_packet_id=payload.get("context_packet_id"),
                status=payload.get("status", "COMPLETED"),
                input_evidence_refs_json=payload.get("input_evidence_refs") or [],
                hypothesis_changes_json=payload.get("hypothesis_changes") or [],
                candidate_actions_json=payload.get("candidate_actions") or [],
                selected_action_json=payload.get("selected_action") or {},
                policy_decision_json=payload.get("policy_decision") or {},
                cost_json=payload.get("cost") or {},
                result_json=payload.get("result") or {},
                stop_decision_json=payload.get("stop_decision") or {},
                created_by=payload["created_by"],
                created_at=now,
                finished_at=now if payload.get("status", "COMPLETED") == "COMPLETED" else None,
            )
            session.add(iteration)
            session.flush()
            session.add(CaseEventModel(
                case_id=case.id,
                tenant_id=case.tenant_id,
                event_type="investigation_iteration_completed",
                actor_id=payload["created_by"],
                payload_json={
                    "iteration_id": iteration.id,
                    "iteration_no": iteration.iteration_no,
                    "selected_action": iteration.selected_action_json,
                    "stop_decision": iteration.stop_decision_json,
                },
                created_at=now,
            ))
            return iteration.to_dict()

    def list_investigation_iterations(
        self,
        case_id: str,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]] | None:
        with self._read_session() as session:
            exists = session.query(IncidentCaseModel.id).filter(
                IncidentCaseModel.id == case_id,
                IncidentCaseModel.tenant_id == tenant_id,
            ).first()
            if exists is None:
                return None
            rows = session.query(InvestigationIterationModel).filter(
                InvestigationIterationModel.case_id == case_id,
                InvestigationIterationModel.tenant_id == tenant_id,
            ).order_by(InvestigationIterationModel.iteration_no.desc()).offset(offset).limit(limit).all()
            return [row.to_dict() for row in rows]

    # ------------------------------------------------------------------
    # Action attempts (durable registered-action lifecycle)
    # ------------------------------------------------------------------

    def record_action_attempt(
        self,
        case_id: str,
        tenant_id: str,
        *,
        attempt_id: str,
        action_id: str,
        operation_key: str,
        phase: str,
        parameters: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        actor_id: str = "mini-drop-autonomy",
    ) -> dict[str, Any]:
        """Upsert one action-attempt phase by (case, operation_key, phase).

        幂等：Control 重启后重放同一操作键的同一阶段不会产生重复行。
        """
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            existing = session.query(ActionAttemptModel).filter(
                ActionAttemptModel.case_id == case_id,
                ActionAttemptModel.tenant_id == tenant_id,
                ActionAttemptModel.operation_key == operation_key,
                ActionAttemptModel.phase == phase,
            ).first()
            if existing is not None:
                existing.action_id = action_id
                existing.parameters_json = parameters or existing.parameters_json or {}
                existing.result_json = result or existing.result_json or {}
                existing.row_version += 1
                existing.updated_at = now
                row = existing
            else:
                row = ActionAttemptModel(
                    id=attempt_id or f"act_{uuid4().hex[:16]}",
                    case_id=case_id,
                    tenant_id=tenant_id,
                    action_id=action_id,
                    operation_key=operation_key,
                    phase=phase,
                    parameters_json=parameters or {},
                    result_json=result or {},
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            session.flush()
            session.add(CaseEventModel(
                case_id=case_id,
                tenant_id=tenant_id,
                event_type=f"action_attempt_{phase}",
                actor_id=actor_id,
                payload_json={
                    "attempt_id": row.id,
                    "action_id": action_id,
                    "operation_key": operation_key,
                },
                created_at=now,
            ))
            return row.to_dict()

    def list_action_attempts(
        self,
        case_id: str,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(ActionAttemptModel).filter(
                ActionAttemptModel.case_id == case_id,
                ActionAttemptModel.tenant_id == tenant_id,
            ).order_by(ActionAttemptModel.created_at.asc()).offset(offset).limit(limit).all()
            return [row.to_dict() for row in rows]

    # ------------------------------------------------------------------
    # Case Supervisor: runtime leases + queued commands
    # ------------------------------------------------------------------

    def acquire_case_lease(
        self,
        case_id: str,
        tenant_id: str,
        *,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        """CAS 获取 Case 短租约；已被他人持有且未过期时返回 False。"""
        now = now_utc()
        until = now + timedelta(seconds=ttl_seconds)
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None or case.state in {"STOPPED", "RESOLVED"}:
                return False
            lease = session.query(CaseRuntimeLeaseModel).filter(
                CaseRuntimeLeaseModel.case_id == case_id,
                CaseRuntimeLeaseModel.tenant_id == tenant_id,
            ).first()
            if lease is not None:
                held_until = lease.lease_until
                if held_until.tzinfo is None:
                    held_until = held_until.replace(tzinfo=timezone.utc)
                if held_until >= now and lease.owner != owner:
                    return False
                # 过期或被同一 owner 重新竞争 → 更新持有。
                lease.owner = owner
                lease.lease_until = until
                lease.row_version += 1
                lease.updated_at = now
            else:
                session.add(CaseRuntimeLeaseModel(
                    id=f"lease_{uuid4().hex}",
                    case_id=case_id,
                    tenant_id=tenant_id,
                    owner=owner,
                    lease_until=until,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                ))
            session.commit()
            return True

    def renew_case_lease(
        self,
        case_id: str,
        tenant_id: str,
        *,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        now = now_utc()
        with self._write_session() as session:
            lease = session.query(CaseRuntimeLeaseModel).filter(
                CaseRuntimeLeaseModel.case_id == case_id,
                CaseRuntimeLeaseModel.tenant_id == tenant_id,
                CaseRuntimeLeaseModel.owner == owner,
            ).first()
            if lease is None:
                return False
            lease.lease_until = now + timedelta(seconds=ttl_seconds)
            lease.row_version += 1
            lease.updated_at = now
            session.commit()
            return True

    def release_case_lease(self, case_id: str, tenant_id: str, owner: str) -> None:
        with self._write_session() as session:
            session.query(CaseRuntimeLeaseModel).filter(
                CaseRuntimeLeaseModel.case_id == case_id,
                CaseRuntimeLeaseModel.tenant_id == tenant_id,
                CaseRuntimeLeaseModel.owner == owner,
            ).delete(synchronize_session=False)
            session.commit()

    def list_unleased_cases(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """返回可被推进的非终态自治 Case（未被有效租约持有）。"""
        now = now_utc()
        with self._read_session() as session:
            held = {
                row[0] for row in session.query(CaseRuntimeLeaseModel.case_id).filter(
                    CaseRuntimeLeaseModel.tenant_id == tenant_id,
                    CaseRuntimeLeaseModel.lease_until >= now,
                ).all()
            }
            rows = session.query(IncidentCaseModel).filter(
                IncidentCaseModel.tenant_id == tenant_id,
                IncidentCaseModel.run_mode == "AUTHORIZED_AUTONOMY",
                IncidentCaseModel.state.notin_(
                    {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"},
                ),
            ).order_by(IncidentCaseModel.updated_at.asc()).offset(offset).limit(limit).all()
            return [row.to_dict() for row in rows if row.id not in held]

    def enqueue_case_command(
        self,
        case_id: str,
        tenant_id: str,
        *,
        command_type: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = now_utc()
        with self._write_session() as session:
            case = self._locked_case(session, case_id, tenant_id)
            if case is None:
                raise ValueError("CASE_NOT_FOUND")
            existing = session.query(CaseCommandModel).filter(
                CaseCommandModel.case_id == case_id,
                CaseCommandModel.tenant_id == tenant_id,
                CaseCommandModel.idempotency_key == idempotency_key,
            ).first()
            if existing is not None:
                return existing.to_dict()
            row = CaseCommandModel(
                id=f"cmd_{uuid4().hex}",
                case_id=case_id,
                tenant_id=tenant_id,
                command_type=command_type,
                idempotency_key=idempotency_key,
                status="PENDING",
                payload_json=payload or {},
                created_at=now,
            )
            session.add(row)
            session.commit()
            return row.to_dict()

    def list_pending_case_commands(
        self, case_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(CaseCommandModel).filter(
                CaseCommandModel.case_id == case_id,
                CaseCommandModel.tenant_id == tenant_id,
                CaseCommandModel.status == "PENDING",
            ).order_by(CaseCommandModel.created_at.asc()).all()
            return [row.to_dict() for row in rows]

    def complete_case_command(self, command_id: str) -> None:
        with self._write_session() as session:
            row = session.get(CaseCommandModel, command_id)
            if row is not None:
                row.status = "DONE"
                row.processed_at = now_utc()
                session.commit()

    # ------------------------------------------------------------------
    # Global governance controls (Red Button, capability rotation epoch)
    # ------------------------------------------------------------------

    def get_system_control(self, control_name: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.get(SystemControlModel, control_name)
            return row.to_dict() if row is not None else None

    def set_system_control(
        self,
        control_name: str,
        *,
        enabled: bool,
        value: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._write_session() as session:
            row = session.get(SystemControlModel, control_name)
            if row is None:
                row = SystemControlModel(
                    control_name=control_name, enabled=enabled,
                    value_json=value or {}, updated_at=now_utc(),
                )
                session.add(row)
            else:
                row.enabled = enabled
                row.value_json = value if value is not None else row.value_json or {}
                row.updated_at = now_utc()
            session.commit()
            return row.to_dict()

    def list_system_controls(self) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = session.query(SystemControlModel).order_by(
                SystemControlModel.control_name.asc(),
            ).all()
            return [row.to_dict() for row in rows]

    # ------------------------------------------------------------------
    # AI authorization grants
    # ------------------------------------------------------------------

    def create_authorization_grant(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_utc()
        grant = AuthorizationGrantModel(
            id=f"grant_{uuid4().hex}",
            principal_id=payload["principal_id"],
            tenant_id=payload["tenant_id"],
            source_ids_json=list(dict.fromkeys(payload["source_ids"])),
            operations_json=list(dict.fromkeys(payload["operations"])),
            resource_scope_json=payload.get("resource_scope") or {},
            mode=payload["mode"],
            case_id=payload.get("case_id"),
            constraints_json=payload.get("constraints") or {},
            valid_until=payload["valid_until"],
            uses_remaining=payload.get("uses_remaining"),
            query_count=0,
            status="ACTIVE",
            created_by=payload["created_by"],
            created_at=now,
        )
        with self._write_session() as session:
            session.add(grant)
            session.flush()
            session.add(AuditLogModel(
                event_type="AUTH_GRANT_CREATED",
                message=f"授权 {grant.id} 已创建",
                meta_json={
                    "grant_id": grant.id,
                    "principal_id": grant.principal_id,
                    "tenant_id": grant.tenant_id,
                    "source_ids": grant.source_ids_json,
                    "operations": grant.operations_json,
                    "created_by": grant.created_by,
                },
                created_at=now,
            ))
            result = grant.to_dict()
        return result

    def list_authorization_grants(
        self,
        *,
        principal_id: str = "",
        tenant_id: str = "",
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(AuthorizationGrantModel)
            if principal_id:
                query = query.filter(AuthorizationGrantModel.principal_id == principal_id)
            if tenant_id:
                query = query.filter(AuthorizationGrantModel.tenant_id == tenant_id)
            if not include_inactive:
                query = query.filter(AuthorizationGrantModel.status == "ACTIVE")
            rows = query.order_by(AuthorizationGrantModel.created_at.desc()).all()
            return [row.to_dict() for row in rows]

    def revoke_authorization_grant(self, grant_id: str, revoked_by: str) -> dict[str, Any] | None:
        now = now_utc()
        with self._write_session() as session:
            grant = session.get(AuthorizationGrantModel, grant_id)
            if grant is None:
                return None
            if grant.status == "ACTIVE":
                grant.status = "REVOKED"
                grant.revoked_at = now
                grant.revoked_by = revoked_by
                session.add(AuditLogModel(
                    event_type="AUTH_GRANT_REVOKED",
                    message=f"授权 {grant.id} 已撤销",
                    meta_json={"grant_id": grant.id, "revoked_by": revoked_by},
                    created_at=now,
                ))
            session.flush()
            result = grant.to_dict()
        return result

    def consume_authorization_grant(
        self,
        grant_id: str,
        *,
        principal_id: str,
        tenant_id: str,
        capability_jti: str,
        capability_token_fingerprint: str,
        source_id: str,
        operation: str,
        query_fingerprint: str,
        content_hash: str,
        projection_hash: str,
        result_bytes: int,
    ) -> dict[str, Any]:
        """Atomically consume one authorized source call after output validation."""
        now = now_utc()
        with self._write_session() as session:
            query = session.query(AuthorizationGrantModel).filter(AuthorizationGrantModel.id == grant_id)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            grant = query.first()
            if grant is None:
                raise ValueError("GRANT_NOT_FOUND")
            valid_until = grant.valid_until
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=now.tzinfo)
            if grant.status != "ACTIVE":
                raise ValueError("GRANT_NOT_ACTIVE")
            if valid_until <= now:
                grant.status = "EXPIRED"
                raise ValueError("GRANT_EXPIRED")
            if grant.principal_id != principal_id or grant.tenant_id != tenant_id:
                raise ValueError("GRANT_SUBJECT_MISMATCH")
            max_queries = int((grant.constraints_json or {}).get("max_queries", 0) or 0)
            if max_queries and (grant.query_count or 0) >= max_queries:
                grant.status = "EXHAUSTED"
                raise ValueError("GRANT_QUERY_BUDGET_EXHAUSTED")
            if grant.uses_remaining is not None and grant.uses_remaining <= 0:
                grant.status = "EXHAUSTED"
                raise ValueError("GRANT_EXHAUSTED")

            grant.query_count = (grant.query_count or 0) + 1
            if max_queries and grant.query_count >= max_queries:
                grant.status = "EXHAUSTED"
            if grant.uses_remaining is not None:
                grant.uses_remaining -= 1
                if grant.uses_remaining == 0:
                    grant.status = "EXHAUSTED"
            session.add(AuditLogModel(
                event_type="SOURCE_ACCESS_GRANTED",
                message=f"授权 {grant.id} 已用于受控信息读取",
                meta_json={
                    "grant_id": grant.id,
                    "capability_jti": capability_jti,
                    "capability_token_fingerprint": capability_token_fingerprint,
                    "source_id": source_id,
                    "operation": operation,
                    "query_fingerprint": query_fingerprint,
                    "content_hash": content_hash,
                    "projection_hash": projection_hash,
                    "result_bytes": result_bytes,
                    "principal_id": principal_id,
                    "tenant_id": tenant_id,
                    "query_count": grant.query_count,
                },
                created_at=now,
            ))
            session.flush()
            result = grant.to_dict()
        return result

    def record_source_access_denied(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        source_id: str,
        operation: str,
        reason_codes: list[str],
    ) -> None:
        with self._write_session() as session:
            session.add(AuditLogModel(
                event_type="SOURCE_ACCESS_DENIED",
                message=f"信息源 {source_id} 访问被拒绝",
                meta_json={
                    "principal_id": principal_id,
                    "tenant_id": tenant_id,
                    "source_id": source_id,
                    "operation": operation,
                    "reason_codes": reason_codes,
                },
                created_at=now_utc(),
            ))

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    def register_agent(
        self, agent_id: str, hostname: str, ip_addr: str,
        version: str = "0.1.0", os_info: str = "unknown",
        capabilities: list[str] | None = None,
    ) -> AgentModel:
        caps = list(capabilities or [])
        ts = now_utc()

        with self._write_session() as session:
            existing = session.get(AgentModel, agent_id)
            if existing is not None and existing.status == "OFFLINE":
                self._write_audit(
                    session, "AGENT_ONLINE", agent_id,
                    f"{agent_id} 恢复在线",
                )

            if existing is not None:
                existing.hostname = hostname
                existing.ip_addr = ip_addr
                existing.version = version
                existing.os_info = os_info
                existing.capabilities = caps
                existing.status = "ONLINE"
                existing.last_heartbeat_at = ts
                existing.updated_at = ts
                agent = existing
            else:
                agent = AgentModel(
                    id=agent_id, hostname=hostname, ip_addr=ip_addr,
                    version=version, os_info=os_info, capabilities=caps,
                    status="ONLINE", last_heartbeat_at=ts,
                    created_at=ts, updated_at=ts,
                )
                session.add(agent)

            # 保持与 InMemoryRepository 接口一致（SqlRepository.heartbeat 直接查 DB，不使用此队列）
            if ip_addr not in self._task_queues:
                self._task_queues[ip_addr] = deque()

            self._notify_after_commit(session, "agent_status", {
                "agent_id": agent_id, "status": "ONLINE", "ip_addr": ip_addr,
            })
            return agent

    def heartbeat(self, agent_id: str, ip_addr: str) -> TaskModel | None:
        with self._write_session() as session:
            agent = session.get(AgentModel, agent_id)
            if agent is None:
                return None

            agent.status = "ONLINE"
            agent.last_heartbeat_at = now_utc()
            agent.updated_at = now_utc()

            dispatch_time = now_utc()
            expired_query = (
                session.query(TaskModel)
                .filter(
                    TaskModel.agent_id == agent_id,
                    TaskModel.status == TaskStatus.PENDING.value,
                    TaskModel.collection_deadline_at.is_not(None),
                    TaskModel.collection_deadline_at <= dispatch_time,
                )
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                expired_query = expired_query.with_for_update(skip_locked=True)
            expired_tasks = expired_query.all()
            for expired in expired_tasks:
                self._transition_task_in_session(
                    session,
                    expired.id,
                    TaskStatus.FAILED,
                    "COLLECTION_QUEUE_DEADLINE_EXCEEDED: task was not dispatched before its deadline",
                    Actor.SERVER,
                    {"error_code": "COLLECTION_QUEUE_DEADLINE_EXCEEDED", "retryable": True},
                )
                self._write_audit(
                    session,
                    "TASK_QUEUE_DEADLINE_EXCEEDED",
                    task_id=expired.id,
                    message="Collection queue deadline exceeded before dispatch",
                )
            if expired_tasks:
                session.flush()

            task_query = (
                session.query(TaskModel)
                .filter(
                    TaskModel.agent_id == agent_id,
                    TaskModel.status == TaskStatus.PENDING.value,
                )
                .order_by(TaskModel.created_at.asc())
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                task_query = task_query.with_for_update(skip_locked=True)
            task = task_query.first()
            if task is None:
                return None

            attempt_no = (
                session.query(TaskAttemptModel)
                .filter(TaskAttemptModel.task_id == task.id)
                .count()
            ) + 1
            attempt = TaskAttemptModel(
                id=f"attempt_{uuid4().hex}",
                task_id=task.id,
                attempt_no=attempt_no,
                agent_id=agent_id,
                status="RUNNING",
                runner_version=agent.version,
                resource_usage_json={},
                artifact_ids_json=[],
                created_at=now_utc(),
                started_at=now_utc(),
                updated_at=now_utc(),
            )
            session.add(attempt)
            task.current_attempt_id = attempt.id

            self._transition_task_in_session(
                session, task.id, TaskStatus.RUNNING,
                "Agent 心跳拉取待执行任务", Actor.SERVER,
                {"attempt_id": attempt.id, "attempt_no": attempt_no},
            )
            task.status = TaskStatus.RUNNING.value
            return task

    def heartbeat_only(self, agent_id: str, ip_addr: str) -> None:
        """Update heartbeat timestamp without dispatching a new task."""
        with self._write_session() as session:
            agent = session.get(AgentModel, agent_id)
            if agent is None:
                return
            agent.ip_addr = ip_addr or agent.ip_addr
            agent.status = "ONLINE"
            agent.last_heartbeat_at = now_utc()
            agent.updated_at = now_utc()

    def mark_offline_agents(self, timeout_sec: int = 30) -> list[AgentModel]:
        with self._write_session() as session:
            cutoff = now_utc() - timedelta(seconds=timeout_sec)
            changed_query = (
                session.query(AgentModel)
                .filter(
                    AgentModel.status == "ONLINE",
                    AgentModel.last_heartbeat_at < cutoff,
                )
            )
            # 多副本扫描器并发标记离线时，行锁保证同一 Agent 只被一个副本处理，
            # 避免重复写审计日志和重复发布事件。
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                changed_query = changed_query.with_for_update(skip_locked=True)
            changed = changed_query.all()
            for agent in changed:
                agent.status = "OFFLINE"
                agent.updated_at = now_utc()
                self._write_audit(
                    session, "AGENT_OFFLINE", agent.id,
                    f"{agent.id} 心跳超时 {timeout_sec}s，标记为离线",
                )
                self._notify_after_commit(session, "agent_status", {
                    "agent_id": agent.id, "status": "OFFLINE", "ip_addr": agent.ip_addr,
                })
            return changed

    @property
    def agents(self) -> dict[str, AgentModel]:
        """返回 {agent_id: AgentModel} 字典（兼容旧接口的 dict 访问）。

        2 秒 TTL 缓存，避免高频场景下每请求查全表。
        """
        return self._cached("agents", 2.0, lambda: self._query_all_agents())

    def _query_all_agents(self) -> dict[str, AgentModel]:
        s = new_session()
        try:
            return {a.id: a for a in s.query(AgentModel).all()}
        finally:
            s.close()

    def find_agent_by_ip(self, ip_addr: str) -> AgentModel | None:
        with self._read_session() as session:
            return session.query(AgentModel).filter(AgentModel.ip_addr == ip_addr).first()

    def record_agent_metrics(self, agent_id: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            self.agent_metrics[agent_id] = dict(metrics)

    def persist_agent_metric_snapshots(self) -> int:
        """将内存中的 agent metrics 批量写入数据库快照表。

        每次调用对所有在线 agent 生成一条快照记录，用于趋势分析。
        返回写入的快照数量。
        """
        with self._write_session() as session:
            ts = now_utc()
            count = 0
            for agent_id, metrics in self.agent_metrics.items():
                self_data = metrics.get("self", {})
                session.add(AgentMetricSnapshotModel(
                    agent_id=agent_id,
                    cpu_percent=int(self_data.get("cpu_percent", 0) or 0),
                    rss_mb=int(self_data.get("rss_mb", 0) or 0),
                    read_kb_s=int(self_data.get("read_kb_s", 0) or 0),
                    write_kb_s=int(self_data.get("write_kb_s", 0) or 0),
                    children_count=int(self_data.get("children_count", 0) or 0),
                    created_at=ts,
                ))
                count += 1
            return count

    def get_agent_metric_history(self, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """查询指定 Agent 的历史指标快照。"""
        with self._read_session() as session:
            rows = (
                session.query(AgentMetricSnapshotModel)
                .filter(AgentMetricSnapshotModel.agent_id == agent_id)
                .order_by(AgentMetricSnapshotModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]

    # ------------------------------------------------------------------
    # Task
    # ------------------------------------------------------------------

    def delete_task(self, task_id: str) -> bool:
        """删除任务及其关联数据（事件、产物、诊断结果）。返回是否实际删除。

        必须按外键依赖顺序清理：diagnosis_runs 的子表
        （tool_results / reports / repair_plans / rca_feedback）先于 runs 删除，
        probe_executions 先于 tasks 删除；否则 PostgreSQL 的 NO ACTION 外键
        约束会抛 IntegrityError 使整个事务回滚（HTTP 500）。
        """
        object_keys: list[tuple[str, str]] = []
        deleted = False
        with self._write_session() as session:
            task = self._locked_task(session, task_id)
            if task is None:
                return False
            # 在删除 ArtifactModel 之前收集对象存储 key，提交后清理
            # （对象删除失败不影响数据库删除）。
            for art in session.query(ArtifactModel).filter(
                ArtifactModel.task_id == task_id
            ).all():
                if art.object_key:
                    object_keys.append((
                        art.bucket or os.getenv("MINIO_BUCKET", "mini-drop"),
                        art.object_key,
                    ))
            diagnosis_run_ids = [
                row[0] for row in
                session.query(DiagnosisRunModel.id).filter(DiagnosisRunModel.task_id == task_id).all()
            ]
            if diagnosis_run_ids:
                session.query(DiagnosisToolResultModel).filter(
                    DiagnosisToolResultModel.diagnosis_id.in_(diagnosis_run_ids)
                ).delete(synchronize_session=False)
                session.query(DiagnosisReportModel).filter(
                    DiagnosisReportModel.diagnosis_id.in_(diagnosis_run_ids)
                ).delete(synchronize_session=False)
                session.query(RepairPlanModel).filter(
                    RepairPlanModel.diagnosis_id.in_(diagnosis_run_ids)
                ).delete(synchronize_session=False)
                session.query(RCAFeedbackModel).filter(
                    RCAFeedbackModel.diagnosis_id.in_(diagnosis_run_ids)
                ).delete(synchronize_session=False)
            # 级联删除关联数据
            session.query(DiagnosisRunModel).filter(
                DiagnosisRunModel.task_id == task_id
            ).delete()
            session.query(ProbeExecutionModel).filter(
                ProbeExecutionModel.task_id == task_id
            ).delete(synchronize_session=False)
            session.query(StatusEventModel).filter(
                StatusEventModel.task_id == task_id
            ).delete()
            session.query(ArtifactModel).filter(
                ArtifactModel.task_id == task_id
            ).delete()
            session.query(AnalysisJobModel).filter(
                AnalysisJobModel.task_id == task_id
            ).delete()
            session.query(TaskAttemptModel).filter(
                TaskAttemptModel.task_id == task_id
            ).delete()
            session.delete(task)
            # 插入审计日志
            self._write_audit(
                session,
                event_type="task_deleted",
                task_id=task_id,
                message=f"任务 {task.name or task_id} 已删除",
            )
            # 清除缓存，下次读取时重新查询
            self._cache.pop("tasks", None)
            self._cache.pop("events", None)
            deleted = True
        for bucket, object_key in object_keys:
            try:
                store.remove_object(bucket, object_key)
            except Exception as exc:
                log_event(
                    "warning", "artifact_object_cleanup_failed",
                    bucket=bucket, object_key=object_key, error=type(exc).__name__,
                )
        return deleted

    def create_task(
        self,
        payload: CreateTaskRequest,
        idempotency_key: str | None = None,
        request_id: str | None = None,
        traceparent: str | None = None,
    ) -> TaskModel:
        """Create a task, returning the original row for a repeated key.

        The unique DB index closes the race across API replicas; the pre-check
        provides a useful conflict error when a key is reused with a different
        payload.
        """

        normalized_key = (idempotency_key or "").strip() or None
        if normalized_key:
            existing = self.get_task_by_idempotency_key(normalized_key)
            if existing is not None:
                self._assert_same_idempotent_payload(existing, payload)
                return existing

        try:
            with self._write_session() as session:
                ts = now_utc()
                hex_suffix = uuid4().hex[:6]
                task_id = f"task_{ts.strftime('%Y%m%d_%H%M%S')}_{hex_suffix}"
                agent = session.get(AgentModel, payload.agent_id)
                if agent is None:
                    raise ValueError(f"Agent {payload.agent_id} 不存在")

                task = TaskModel(
                    id=task_id,
                    name=payload.name,
                    agent_id=payload.agent_id,
                    target_pid=payload.target_pid,
                    collector_type=payload.collector_type,
                    sample_rate=payload.sample_rate,
                    duration_sec=payload.duration_sec,
                    status=TaskStatus.PENDING.value,
                    status_reason="Web 请求创建任务",
                    collection_status=CollectionStatus.PENDING.value,
                    analysis_status=AnalysisStatus.WAITING.value,
                    row_version=0,
                    collection_deadline_at=ts + timedelta(
                        seconds=_collection_queue_ttl_sec(payload.duration_sec),
                    ),
                    request_id=(request_id or "")[:64] or None,
                    traceparent=(traceparent or "")[:64] or None,
                    request_params=payload.model_dump(),
                    idempotency_key=normalized_key,
                    diagnosis_step_id=(payload.options or {}).get("diagnosis_step_id"),
                    created_at=ts,
                )
                session.add(task)
                session.flush()

                self._write_event(
                    session,
                    task_id,
                    None,
                    TaskStatus.PENDING,
                    "Web 请求创建任务",
                    Actor.WEB,
                    payload.model_dump(),
                )
                record_task_transition("NONE", TaskStatus.PENDING.value)
                self._write_audit(
                    session,
                    "TASK_CREATED",
                    task_id=task_id,
                    message=f"任务 {task_id} 已创建",
                    metadata={
                        **payload.model_dump(),
                        "idempotency_key_present": bool(normalized_key),
                    },
                )
                return task
        except IntegrityError:
            if not normalized_key:
                raise
            existing = self.get_task_by_idempotency_key(normalized_key)
            if existing is None:
                raise
            self._assert_same_idempotent_payload(existing, payload)
            return existing

    def get_task_by_idempotency_key(self, key: str) -> TaskModel | None:
        with self._read_session() as session:
            return session.query(TaskModel).filter(TaskModel.idempotency_key == key).first()

    @staticmethod
    def _assert_same_idempotent_payload(task: TaskModel, payload: CreateTaskRequest) -> None:
        if (task.request_params or {}) != payload.model_dump():
            raise ValueError("IDEMPOTENCY_CONFLICT: 同一幂等键不能用于不同任务参数")

    def cancel_task(self, task_id: str, reason: str, actor: Actor = Actor.WEB) -> TaskModel:
        """Cancel an active task. Repeated cancellation is safe."""

        with self._write_session() as session:
            task = self._locked_task(session, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")
            current = TaskStatus(task.status)
            if current == TaskStatus.CANCELLED:
                return task
            if current in {TaskStatus.DONE, TaskStatus.FAILED}:
                raise ValueError(f"任务已处于终态 {current.value}，不能取消")
            build_status_event(task_id, current, TaskStatus.CANCELLED, reason, actor)
            self._transition_task_in_session(
                session,
                task_id,
                TaskStatus.CANCELLED,
                reason,
                actor,
                {"error_code": "TASK_CANCELLED", "retryable": True},
            )
            if task.current_attempt_id:
                attempt = session.get(TaskAttemptModel, task.current_attempt_id)
                if attempt is not None and attempt.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    attempt.status = "CANCEL_REQUESTED"
                    attempt.error_code = "TASK_CANCELLED"
                    attempt.error_message = reason
                    attempt.updated_at = now_utc()
            analysis_jobs = (
                session.query(AnalysisJobModel)
                .filter(
                    AnalysisJobModel.task_id == task_id,
                    AnalysisJobModel.status.in_(["PENDING", "RUNNING"]),
                )
                .all()
            )
            for job in analysis_jobs:
                job.status = "CANCELLED"
                job.error_code = "TASK_CANCELLED"
                job.error_message = reason[:1024]
                job.lease_owner = None
                job.lease_expires_at = None
                job.finished_at = now_utc()
                job.updated_at = now_utc()
            self._write_audit(
                session,
                "TASK_CANCELLED",
                task_id=task_id,
                message=reason,
            )
            return task

    def should_cancel_attempt(self, task_id: str, attempt_id: str | None) -> tuple[bool, str]:
        """Return the durable cancellation directive for an active Agent attempt."""

        if not task_id:
            return False, ""
        with self._read_session() as session:
            task = session.get(TaskModel, task_id)
            if task is None or task.status != TaskStatus.CANCELLED.value:
                return False, ""
            if attempt_id and task.current_attempt_id and attempt_id != task.current_attempt_id:
                return False, ""
            return True, task.status_reason or "任务已取消"

    def get_attempt(self, attempt_id: str | None) -> TaskAttemptModel | None:
        if not attempt_id:
            return None
        with self._read_session() as session:
            return session.get(TaskAttemptModel, attempt_id)

    def list_task_attempts(self, task_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = (
                session.query(TaskAttemptModel)
                .filter(TaskAttemptModel.task_id == task_id)
                .order_by(TaskAttemptModel.attempt_no.asc())
                .all()
            )
            return [row.to_dict() for row in rows]

    def finish_attempt(
        self,
        task_id: str,
        attempt_id: str | None,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        result_message: str | None = None,
        exit_code: int | None = None,
        resource_usage: dict[str, Any] | None = None,
    ) -> TaskAttemptModel | None:
        """Persist one Agent result without changing the aggregate Task state."""

        with self._write_session() as session:
            task = self._locked_task(session, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")
            resolved_id = attempt_id or task.current_attempt_id
            attempt = session.get(TaskAttemptModel, resolved_id) if resolved_id else None
            if attempt is None:
                return None
            if attempt.task_id != task_id:
                raise ValueError("ATTEMPT_TASK_MISMATCH")
            # A replay must not overwrite a terminal attempt with a different
            # outcome.  Identical terminal updates are acknowledged.
            if attempt.status in {"COLLECTED", "SUCCEEDED", "FAILED", "CANCELLED"}:
                if attempt.status != status:
                    raise ValueError("ATTEMPT_ALREADY_TERMINAL")
                return attempt
            attempt.status = status
            attempt.error_code = error_code
            attempt.error_message = error_message
            if result_message is not None:
                attempt.result_message = result_message[:1024]
            attempt.exit_code = exit_code
            if resource_usage is not None:
                attempt.resource_usage_json = resource_usage
            attempt.finished_at = now_utc()
            attempt.updated_at = now_utc()
            return attempt

    def record_task_retry(
        self,
        original_task_id: str,
        retried_task_id: str,
    ) -> None:
        """Write one retry audit event even when the HTTP request is replayed."""

        with self._write_session() as session:
            existing = (
                session.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "TASK_RETRIED",
                    AuditLogModel.task_id == retried_task_id,
                )
                .first()
            )
            if existing is not None:
                return
            self._write_audit(
                session,
                "TASK_RETRIED",
                task_id=retried_task_id,
                message=f"任务 {original_task_id} 已重试为 {retried_task_id}",
                metadata={"retry_of": original_task_id},
            )

    def recover_stale_tasks(self, timeout_sec: int = 900) -> list[str]:
        """Fail non-terminal tasks that can no longer make progress."""

        cutoff = now_utc() - timedelta(seconds=max(60, timeout_sec))
        recovered: list[str] = []
        with self._write_session() as session:
            pending_query = (
                session.query(TaskModel)
                .filter(
                    TaskModel.status == TaskStatus.PENDING.value,
                    TaskModel.created_at < cutoff,
                )
            )
            active_query = (
                session.query(TaskModel)
                .filter(
                    TaskModel.status.in_([
                        TaskStatus.RUNNING.value,
                        TaskStatus.UPLOADING.value,
                        TaskStatus.ANALYZING.value,
                    ]),
                    TaskModel.started_at.is_not(None),
                    TaskModel.started_at < cutoff,
                )
            )
            # 多副本扫描器同时 recover 时，行锁 + skip_locked 保证同一任务
            # 只被一个副本处理，配合 _transition_task_in_session 的同状态
            # 幂等，避免重复审计日志与 row_version 跳变。
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                pending_query = pending_query.with_for_update(skip_locked=True)
                active_query = active_query.with_for_update(skip_locked=True)
            pending_tasks = pending_query.all()
            active_tasks = active_query.all()
            for task in [*pending_tasks, *active_tasks]:
                reason = "TASK_EXECUTION_STALE: 任务超过恢复阈值且无终态结果"
                if task.current_attempt_id:
                    attempt = session.get(TaskAttemptModel, task.current_attempt_id)
                    if attempt is not None and attempt.status not in {
                        "COLLECTED", "SUCCEEDED", "FAILED", "CANCELLED", "LOST",
                    }:
                        attempt.status = "LOST"
                        attempt.error_code = "AGENT_RESULT_TIMEOUT"
                        attempt.error_message = reason
                        attempt.finished_at = now_utc()
                        attempt.updated_at = now_utc()
                self._transition_task_in_session(
                    session,
                    task.id,
                    TaskStatus.FAILED,
                    reason,
                    Actor.SERVER,
                    {"error_code": "TASK_EXECUTION_STALE", "retryable": True},
                )
                self._write_audit(
                    session,
                    "TASK_STALE_RECOVERED",
                    task_id=task.id,
                    message=reason,
                )
                recovered.append(task.id)
        return recovered

    def get_task_by_diagnosis_step_id(self, step_id: str) -> TaskModel | None:
        session = new_session()
        try:
            return session.query(TaskModel).filter(TaskModel.diagnosis_step_id == step_id).first()
        finally:
            session.close()

    def transition_task(
        self, task_id: str, to_status: TaskStatus,
        reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
    ) -> TaskModel:
        with self._write_session() as session:
            task = self._locked_task(session, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")

            _ = build_status_event(
                task_id, TaskStatus(task.status), to_status,
                reason, actor, metadata or {},
            )

            self._transition_task_in_session(
                session, task_id, to_status, reason, actor, metadata,
            )
            return task

    @property
    def tasks(self) -> dict[str, TaskModel]:
        return self._cached("tasks", 2.0, self._query_all_tasks)

    def _query_all_tasks(self) -> dict[str, TaskModel]:
        s = new_session()
        try:
            return {t.id: t for t in s.query(TaskModel).all()}
        finally:
            s.close()

    @property
    def events(self) -> list[StatusEvent]:
        """返回所有状态事件，兼容原有 list[StatusEvent] 接口。"""
        return self._cached("events", 2.0, self._query_all_events)

    def _query_all_events(self) -> list[StatusEvent]:
        s = new_session()
        try:
            models = s.query(StatusEventModel).all()
            result: list[StatusEvent] = []
            for m in models:
                result.append(StatusEvent(
                    task_id=m.task_id if m.task_id else "",
                    from_status=TaskStatus(m.from_status) if m.from_status else None,
                    to_status=TaskStatus(m.to_status),
                    reason=m.reason if m.reason else "",
                    actor=Actor(m.actor) if m.actor else Actor.SERVER,
                    metadata=m.meta_json if isinstance(m.meta_json, dict) else {},
                    created_at=m.created_at if m.created_at else now_utc(),
                ))
            return result
        finally:
            s.close()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def add_artifacts(
        self,
        task_id: str,
        artifacts: list[dict[str, Any]],
        attempt_id: str | None = None,
    ) -> list[int]:
        """Insert or refresh artifact metadata and return durable row ids.

        ``identity_key`` closes the duplicate-result race.  It is derived only
        from immutable provenance fields, never from a presigned URL.
        """

        with self._write_session() as session:
            ts = now_utc()
            task = self._locked_task(session, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")
            resolved_attempt_id = attempt_id or task.current_attempt_id
            artifact_ids: list[int] = []
            for art in artifacts:
                artifact_type = art.get("artifact_type", "raw")
                object_key = art.get("object_key", "")
                filename = art.get("filename")
                window_index = (art.get("metadata") or {}).get("window_index")
                identity_key = self._artifact_identity(
                    task_id, resolved_attempt_id, art, window_index,
                )
                existing = (
                    session.query(ArtifactModel)
                    .filter(ArtifactModel.identity_key == identity_key)
                    .first()
                )
                if existing is not None:
                    existing_window = (existing.meta_json or {}).get("window_index")
                    if existing_window == window_index:
                        existing.bucket = art.get("bucket", existing.bucket or "mini-drop")
                        existing.local_path = art.get("local_path") or existing.local_path
                        existing.content_type = art.get("content_type", existing.content_type)
                        existing.size_bytes = art.get("size_bytes", existing.size_bytes or 0)
                        existing.sha256 = art.get("sha256") or existing.sha256
                        existing.meta_json = art.get("metadata", existing.meta_json or {})
                        session.flush()
                        artifact_ids.append(existing.id)
                        continue
                model = ArtifactModel(
                    task_id=task_id,
                    attempt_id=resolved_attempt_id,
                    identity_key=identity_key,
                    artifact_type=artifact_type,
                    bucket=art.get("bucket", "mini-drop"),
                    object_key=object_key,
                    filename=filename,
                    local_path=art.get("local_path"),
                    content_type=art.get("content_type", "application/octet-stream"),
                    size_bytes=art.get("size_bytes", 0),
                    sha256=art.get("sha256"),
                    meta_json=art.get("metadata", {}),
                    created_at=ts,
                )
                session.add(model)
                session.flush()
                artifact_ids.append(model.id)

            if resolved_attempt_id:
                attempt = session.get(TaskAttemptModel, resolved_attempt_id)
                if attempt is not None:
                    merged = list(dict.fromkeys([*(attempt.artifact_ids_json or []), *artifact_ids]))
                    attempt.artifact_ids_json = merged
                    attempt.updated_at = now_utc()
            return artifact_ids

    @staticmethod
    def _artifact_identity(
        task_id: str,
        attempt_id: str | None,
        artifact: dict[str, Any],
        window_index: Any,
    ) -> str:
        # 只取不可变出处字段：sha256 / local_path / filename 是内容字段，
        # 重传（内容变化）必须命中同一 identity_key 走"更新"分支而不是
        # 插入重复行。bucket 不可变（同一对象不会换 bucket）。
        payload = {
            "task_id": task_id,
            "attempt_id": attempt_id or "legacy",
            "artifact_type": artifact.get("artifact_type", "raw"),
            "bucket": artifact.get("bucket", "mini-drop"),
            "object_key": artifact.get("object_key") or artifact.get("cos_key") or "",
            "window_index": window_index,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def create_analysis_job(
        self,
        task_id: str,
        attempt_id: str | None,
        input_artifact_ids: list[int],
        pipeline: str | None = None,
    ) -> AnalysisJobModel:
        """Create the one analysis job for a collection attempt."""

        with self._write_session() as session:
            task = self._locked_task(session, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")
            resolved_attempt_id = attempt_id or task.current_attempt_id
            if not resolved_attempt_id:
                raise ValueError("ANALYSIS_ATTEMPT_REQUIRED")
            resolved_pipeline = pipeline or self._analysis_pipeline(task.collector_type)
            existing = (
                session.query(AnalysisJobModel)
                .filter(
                    AnalysisJobModel.task_id == task_id,
                    AnalysisJobModel.attempt_id == resolved_attempt_id,
                    AnalysisJobModel.pipeline == resolved_pipeline,
                )
                .first()
            )
            if existing is not None:
                return existing
            ts = now_utc()
            job = AnalysisJobModel(
                id=f"analysis_{uuid4().hex}",
                task_id=task_id,
                attempt_id=resolved_attempt_id,
                pipeline=resolved_pipeline,
                status="PENDING",
                priority=0,
                retry_count=0,
                max_retries=3,
                input_artifact_ids_json=list(dict.fromkeys(input_artifact_ids)),
                output_artifact_ids_json=[],
                created_at=ts,
                updated_at=ts,
            )
            session.add(job)
            task.analysis_status = AnalysisStatus.PENDING.value
            return job

    @staticmethod
    def _analysis_pipeline(collector_type: str) -> str:
        if collector_type in {"perf_cpu", "continuous_perf"}:
            return "perf_flamegraph"
        return f"{collector_type}_result_validation"

    def claim_analysis_job(
        self,
        worker_id: str,
        lease_sec: int = 120,
    ) -> AnalysisJobModel | None:
        """Claim pending or lease-expired work.

        PostgreSQL uses ``SKIP LOCKED``; SQLite is serialized by the repository
        write lock, which is sufficient for local development and tests.
        """

        with self._write_session() as session:
            now = now_utc()
            query = (
                session.query(AnalysisJobModel)
                .filter(
                    (
                        AnalysisJobModel.status == "PENDING"
                    ) | (
                        (AnalysisJobModel.status == "RUNNING")
                        & (AnalysisJobModel.lease_expires_at < now)
                    )
                )
                .order_by(AnalysisJobModel.priority.desc(), AnalysisJobModel.created_at.asc())
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            job = query.first()
            if job is None:
                return None
            if job.status == "RUNNING":
                job.retry_count += 1
            job.status = "RUNNING"
            job.lease_owner = worker_id
            job.lease_expires_at = now + timedelta(seconds=max(10, lease_sec))
            job.started_at = job.started_at or now
            job.updated_at = now
            task = session.get(TaskModel, job.task_id)
            if task is not None:
                task.analysis_status = AnalysisStatus.RUNNING.value
            return job

    def renew_analysis_lease(self, job_id: str, worker_id: str, lease_sec: int = 120) -> bool:
        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None or job.status != "RUNNING" or job.lease_owner != worker_id:
                return False
            job.lease_expires_at = now_utc() + timedelta(seconds=max(10, lease_sec))
            job.updated_at = now_utc()
            return True

    def complete_analysis_job(
        self,
        job_id: str,
        worker_id: str,
        output_artifact_ids: list[int],
        reason: str,
        analyzer_version: str = "0.1.0",
    ) -> AnalysisJobModel:
        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None:
                raise ValueError("ANALYSIS_JOB_NOT_FOUND")
            if job.status == "SUCCEEDED":
                return job
            if job.status != "RUNNING" or job.lease_owner != worker_id:
                raise ValueError("ANALYSIS_LEASE_LOST")
            job.status = "SUCCEEDED"
            job.output_artifact_ids_json = list(dict.fromkeys(output_artifact_ids))
            job.analyzer_version = analyzer_version
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = now_utc()
            job.updated_at = now_utc()
            task = session.get(TaskModel, job.task_id)
            if task is not None and task.status == TaskStatus.ANALYZING.value:
                self._transition_task_in_session(
                    session, task.id, TaskStatus.DONE, reason, Actor.ANALYZER,
                    {"analysis_job_id": job.id, "attempt_id": job.attempt_id},
                )
            return job

    def fail_analysis_job(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        *,
        retryable: bool = True,
    ) -> AnalysisJobModel:
        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None:
                raise ValueError("ANALYSIS_JOB_NOT_FOUND")
            if job.status != "RUNNING" or job.lease_owner != worker_id:
                raise ValueError("ANALYSIS_LEASE_LOST")
            job.retry_count += 1
            exhausted = not retryable or job.retry_count >= job.max_retries
            job.status = "FAILED" if exhausted else "PENDING"
            job.error_code = error_code
            job.error_message = error_message[:1024]
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now_utc()
            if exhausted:
                job.finished_at = now_utc()
                task = session.get(TaskModel, job.task_id)
                if task is not None and task.status == TaskStatus.ANALYZING.value:
                    self._transition_task_in_session(
                        session, task.id, TaskStatus.FAILED,
                        f"{error_code}: {error_message[:900]}", Actor.ANALYZER,
                        {"analysis_job_id": job.id, "retryable": retryable},
                    )
            return job

    def get_analysis_job(self, job_id: str) -> AnalysisJobModel | None:
        with self._read_session() as session:
            return session.get(AnalysisJobModel, job_id)

    def list_task_analysis_jobs(self, task_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            rows = (
                session.query(AnalysisJobModel)
                .filter(AnalysisJobModel.task_id == task_id)
                .order_by(AnalysisJobModel.created_at.asc())
                .all()
            )
            return [row.to_dict() for row in rows]

    def heartbeat_analyzer(
        self,
        worker_id: str,
        *,
        status: str = "IDLE",
        current_job_id: str | None = None,
        version: str = "0.1.0",
    ) -> None:
        with self._write_session() as session:
            now = now_utc()
            worker = session.get(AnalyzerWorkerModel, worker_id)
            if worker is None:
                worker = AnalyzerWorkerModel(
                    id=worker_id,
                    version=version,
                    status=status,
                    current_job_id=current_job_id,
                    last_heartbeat_at=now,
                    started_at=now,
                    updated_at=now,
                )
                session.add(worker)
            else:
                worker.version = version
                worker.status = status
                worker.current_job_id = current_job_id
                worker.last_heartbeat_at = now
                worker.updated_at = now

    def analysis_health(self, timeout_sec: int = 30) -> dict[str, Any]:
        with self._read_session() as session:
            cutoff = now_utc() - timedelta(seconds=max(5, timeout_sec))
            online = (
                session.query(AnalyzerWorkerModel)
                .filter(AnalyzerWorkerModel.last_heartbeat_at >= cutoff)
                .count()
            )
            pending = (
                session.query(AnalysisJobModel)
                .filter(AnalysisJobModel.status == "PENDING")
                .count()
            )
            running = (
                session.query(AnalysisJobModel)
                .filter(AnalysisJobModel.status == "RUNNING")
                .count()
            )
            failed = (
                session.query(AnalysisJobModel)
                .filter(AnalysisJobModel.status == "FAILED")
                .count()
            )
            return {
                "status": "ok" if online > 0 else "unavailable",
                "workers_online": online,
                "jobs_pending": pending,
                "jobs_running": running,
                "jobs_failed": failed,
            }

    @property
    def artifacts(self) -> dict[str, list[dict[str, Any]]]:
        return self._cached("artifacts", 2.0, self._query_all_artifacts)

    def _query_all_artifacts(self) -> dict[str, list[dict[str, Any]]]:
        s = new_session()
        try:
            result: dict[str, list[dict[str, Any]]] = {}
            for art in s.query(ArtifactModel).all():
                tid = art.task_id if art.task_id else ""
                result.setdefault(tid, []).append(art.to_dict())
            return result
        finally:
            s.close()

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _write_audit(
        self, session: OrmSession, event_type: str, agent_id: str | None = None,
        task_id: str | None = None, message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(AuditLogModel(
            event_type=event_type,
            message=message,
            agent_id=agent_id,
            task_id=task_id,
            meta_json=metadata or {},
            created_at=now_utc(),
        ))

    def record_audit(
        self, event_type: str, message: str = "",
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None, agent_id: str | None = None,
    ) -> None:
        """公开的审计写入接口（供 HTTP 层与 Actuation Gateway 使用）。"""
        with self._write_session() as session:
            self._write_audit(
                session, event_type, agent_id=agent_id, task_id=task_id,
                message=message, metadata=metadata,
            )

    @property
    def audit_logs(self) -> list[AuditLogModel]:
        return self._cached("audit_logs", 5.0, self._query_all_audit_logs)

    def _query_all_audit_logs(self) -> list[AuditLogModel]:
        s = new_session()
        try:
            return s.query(AuditLogModel).all()
        finally:
            s.close()

    # ------------------------------------------------------------------
    # RCA
    # ------------------------------------------------------------------

    def create_diagnosis_run(self, task_id: str, model_name: str) -> str:
        with self._write_session() as session:
            ts = now_utc()
            diagnosis_id = f"diag_{ts.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
            session.add(DiagnosisRunModel(
                id=diagnosis_id,
                task_id=task_id,
                status="RUNNING",
                model_name=model_name,
                created_at=ts,
            ))
            return diagnosis_id

    def finish_diagnosis_run(
        self, diagnosis_id: str, status: str, summary: str,
        validated: bool, retry_count: int,
    ) -> None:
        with self._write_session() as session:
            run = session.get(DiagnosisRunModel, diagnosis_id)
            if run is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            run.status = status
            run.summary = summary
            run.validated = 1 if validated else 0
            run.retry_count = retry_count
            run.finished_at = now_utc()

    def add_diagnosis_tool_result(
        self, diagnosis_id: str, tool_name: str, status: str,
        evidence_ref: str, input_json: dict[str, Any],
        output_json: dict[str, Any], error_message: str | None = None,
    ) -> None:
        with self._write_session() as session:
            session.add(DiagnosisToolResultModel(
                diagnosis_id=diagnosis_id,
                tool_name=tool_name,
                status=status,
                evidence_ref=evidence_ref,
                input_json=_json_safe(input_json),
                output_json=_json_safe(output_json),
                error_message=error_message,
                created_at=now_utc(),
            ))

    def add_diagnosis_report(
        self, diagnosis_id: str, report_json: dict[str, Any],
        ranked_causes: list[dict[str, Any]], confidence: float,
        not_enough_evidence: bool,
    ) -> str:
        with self._write_session() as session:
            report_id = f"report_{uuid4().hex[:10]}"
            session.add(DiagnosisReportModel(
                id=report_id,
                diagnosis_id=diagnosis_id,
                report_json=_json_safe(report_json),
                ranked_causes_json=_json_safe(ranked_causes),
                confidence=int(max(0.0, min(confidence, 1.0)) * 1000),
                not_enough_evidence=1 if not_enough_evidence else 0,
                created_at=now_utc(),
            ))
            return report_id

    def add_repair_plan(
        self, diagnosis_id: str, plan_id: str, cause_id: str,
        risk_level: str, actions: list[dict[str, Any]],
        executed_actions: list[dict[str, Any]],
        requires_user_confirm: bool, status: str,
    ) -> None:
        with self._write_session() as session:
            session.add(RepairPlanModel(
                id=plan_id,
                diagnosis_id=diagnosis_id,
                cause_id=cause_id,
                risk_level=risk_level,
                actions_json=_json_safe(actions),
                executed_actions_json=_json_safe(executed_actions),
                requires_user_confirm=1 if requires_user_confirm else 0,
                status=status,
                created_at=now_utc(),
            ))

    def get_diagnosis(self, diagnosis_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            run = session.get(DiagnosisRunModel, diagnosis_id)
            if run is None:
                return None
            report = (
                session.query(DiagnosisReportModel)
                .filter(DiagnosisReportModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisReportModel.created_at.desc())
                .first()
            )
            plan = (
                session.query(RepairPlanModel)
                .filter(RepairPlanModel.diagnosis_id == diagnosis_id)
                .order_by(RepairPlanModel.created_at.desc())
                .first()
            )
            tools = (
                session.query(DiagnosisToolResultModel)
                .filter(DiagnosisToolResultModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisToolResultModel.id.asc())
                .all()
            )
            return {
                "run": run.to_dict(),
                "report": report.to_dict() if report else None,
                "repair_plan": plan.to_dict() if plan else None,
                "tool_results": [tool.to_dict() for tool in tools],
            }

    def list_diagnoses_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            runs = (
                session.query(DiagnosisRunModel)
                .filter(DiagnosisRunModel.task_id == task_id)
                .order_by(DiagnosisRunModel.created_at.desc())
                .all()
            )
            return [run.to_dict() for run in runs]

    def list_diagnosis_history(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return legacy RCA history in one query-oriented API response."""

        with self._read_session() as session:
            total = session.query(DiagnosisRunModel).count()
            runs = (
                session.query(DiagnosisRunModel)
                .order_by(DiagnosisRunModel.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            diagnosis_ids = [run.id for run in runs]
            if not diagnosis_ids:
                return [], total

            reports: dict[str, DiagnosisReportModel] = {}
            for report in (
                session.query(DiagnosisReportModel)
                .filter(DiagnosisReportModel.diagnosis_id.in_(diagnosis_ids))
                .order_by(DiagnosisReportModel.created_at.desc())
                .all()
            ):
                reports.setdefault(report.diagnosis_id, report)

            feedback: dict[str, RCAFeedbackModel] = {}
            for item in (
                session.query(RCAFeedbackModel)
                .filter(RCAFeedbackModel.diagnosis_id.in_(diagnosis_ids))
                .order_by(RCAFeedbackModel.created_at.desc())
                .all()
            ):
                feedback.setdefault(item.diagnosis_id, item)

            items = []
            for run in runs:
                report = reports.get(run.id)
                feedback_item = feedback.get(run.id)
                items.append({
                    "id": run.id,
                    "created_at": run.created_at,
                    "run": run.to_dict(),
                    "report": report.to_dict() if report else None,
                    "feedback": {
                        "feedback_label": feedback_item.feedback_label,
                        "predicted_cause_id": feedback_item.predicted_cause_id,
                        "corrected_cause_id": feedback_item.corrected_cause_id,
                        "feedback_note": feedback_item.feedback_note,
                        "created_at": feedback_item.created_at,
                    } if feedback_item else None,
                })
            return items, total

    def get_feedback_priors(self) -> dict[str, FeedbackPrior]:
        with self._read_session() as session:
            priors: dict[str, FeedbackPrior] = {}
            for row in session.query(RCAFeedbackWeightModel).all():
                priors[row.candidate_id] = FeedbackPrior(
                    candidate_id=row.candidate_id,
                    positive_count=row.positive_count or 0,
                    negative_count=row.negative_count or 0,
                    weight_delta=(row.weight_delta or 0) / 1000,
                )
            return priors

    def record_rca_feedback(
        self, diagnosis_id: str, task_id: str, predicted_cause_id: str,
        feedback_label: str, corrected_cause_id: str | None = None,
        feedback_note: str | None = None,
    ) -> None:
        with self._write_session() as session:
            ts = now_utc()
            session.add(RCAFeedbackModel(
                diagnosis_id=diagnosis_id,
                task_id=task_id,
                predicted_cause_id=predicted_cause_id,
                feedback_label=feedback_label,
                corrected_cause_id=corrected_cause_id,
                feedback_note=feedback_note,
                created_at=ts,
            ))

            candidate_id = corrected_cause_id if feedback_label == "wrong" and corrected_cause_id else predicted_cause_id
            weight = session.get(RCAFeedbackWeightModel, candidate_id)
            if weight is None:
                weight = RCAFeedbackWeightModel(
                    candidate_id=candidate_id,
                    positive_count=0,
                    negative_count=0,
                    partial_count=0,
                    weight_delta=0,
                    updated_at=ts,
                )
                session.add(weight)

            if feedback_label == "correct":
                weight.positive_count += 1
            elif feedback_label == "partial":
                weight.partial_count += 1
            elif feedback_label == "wrong":
                weight.negative_count += 1

            weight.weight_delta = _feedback_delta(
                weight.positive_count, weight.partial_count, weight.negative_count,
            )
            weight.updated_at = ts

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _write_event(
        self, session: OrmSession, task_id: str,
        from_status, to_status: TaskStatus,
        reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(StatusEventModel(
            task_id=task_id,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            reason=reason,
            actor=actor.value,
            meta_json=metadata or {},
            created_at=now_utc(),
        ))

    def _transition_task_in_session(
        self, session: OrmSession, task_id: str,
        to_status: TaskStatus, reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        task = session.get(TaskModel, task_id)
        if task.status == to_status.value:
            # 幂等：双副本扫描器可能对同一任务重复 recover；同状态迁移
            # 不写重复事件、不递增 row_version。
            return
        # 事件：from 用旧 status value
        from_status = task.status
        session.add(StatusEventModel(
            task_id=task_id,
            from_status=from_status,
            to_status=to_status.value,
            reason=reason,
            actor=actor.value,
            meta_json=metadata or {},
            created_at=now_utc(),
        ))
        record_task_transition(from_status, to_status.value)
        task.status = to_status.value
        task.status_reason = reason
        task.row_version = int(task.row_version or 0) + 1
        if to_status == TaskStatus.PENDING:
            task.collection_status = CollectionStatus.PENDING.value
            task.analysis_status = AnalysisStatus.WAITING.value
        elif to_status == TaskStatus.RUNNING:
            task.collection_status = CollectionStatus.RUNNING.value
        elif to_status == TaskStatus.UPLOADING:
            task.collection_status = CollectionStatus.UPLOADING.value
        elif to_status == TaskStatus.ANALYZING:
            task.collection_status = CollectionStatus.COLLECTED.value
            if task.analysis_status not in {
                AnalysisStatus.RUNNING.value,
                AnalysisStatus.SUCCEEDED.value,
            }:
                task.analysis_status = AnalysisStatus.PENDING.value
        elif to_status == TaskStatus.DONE:
            task.collection_status = CollectionStatus.COLLECTED.value
            task.analysis_status = AnalysisStatus.SUCCEEDED.value
        elif to_status == TaskStatus.FAILED:
            if task.collection_status != CollectionStatus.COLLECTED.value:
                task.collection_status = CollectionStatus.FAILED.value
            else:
                task.analysis_status = AnalysisStatus.FAILED.value
        elif to_status == TaskStatus.CANCELLED:
            if task.collection_status != CollectionStatus.COLLECTED.value:
                task.collection_status = CollectionStatus.CANCELLED.value
            task.analysis_status = AnalysisStatus.CANCELLED.value
        if to_status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = now_utc()
        if to_status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.finished_at = now_utc()

        # 发布 SSE 事件（仅在事务提交成功后）
        self._notify_after_commit(session, "task_changed", {
            "task_id": task_id,
            "from_status": from_status,
            "to_status": to_status.value,
            "reason": reason,
        })

    def as_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, StatusEvent):
            data = asdict(value)
            data["from_status"] = value.from_status.value if value.from_status else None
            data["to_status"] = value.to_status.value
            data["actor"] = value.actor.value
            return data
        if isinstance(value, (
            AgentModel, TaskModel, StatusEventModel, AuditLogModel, ArtifactModel,
            DiagnosisRunModel, DiagnosisToolResultModel, DiagnosisReportModel,
            RepairPlanModel,
        )):
            return value.to_dict()
        return json.loads(json.dumps(value, default=str))


def _feedback_delta(positive: int, partial: int, negative: int) -> int:
    raw = positive * 60 + partial * 25 - negative * 80
    return max(-200, min(200, raw))


def _json_safe(value: Any):
    return json.loads(json.dumps(value, default=str))
