"""可恢复、受预算约束的 AI 集群诊断编排器。"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from server.app import storage
from server.app.ai_provider import get_ai_settings, is_feature_enabled
from server.app.common_utils import status_value
from server.app.diagnosis.intent import parse_diagnosis_intent
from server.app.diagnosis.actions import collect_action, inspect_command_action, inspect_session_action
from server.app.diagnosis.domain_analyzers import (
    analyze_observations,
    assess_cluster,
    cluster_finding,
)
from server.app.diagnosis.knowledge import retrieve_knowledge
from server.app.diagnosis.probe_registry import choose_probe_ids, get_probe, list_probes
from server.app.diagnosis.report_verifier import evidence_integrity_hash, verify_report
from server.app.diagnosis.schemas import (
    ApprovalRequest,
    CreateDiagnosisRequest,
    DiagnosisBudget,
    DiagnosisMode,
    DiagnosisStatus,
    ProbePlan,
    TERMINAL_DIAGNOSIS_STATUSES,
)
from server.app.diagnosis.store import DiagnosisStore, utcnow
from server.app.diagnosis.sys_metrics import normalize_sys_metrics
from server.app.event_bus import BUS
from server.app.rca.calibrator import calibrate
from server.app.rca.candidates import generate_candidates
from server.app.rca.evidence import collect_evidence
from server.app.schemas import CreateTaskRequest, MAX_SAMPLE_RATE, MAX_TASK_DURATION_SEC, MIN_SAMPLE_RATE


PLANNER_VERSION = "diagnosis-orchestrator-v1"
ACTIVE_TASK_STATUSES = {"PENDING", "RUNNING", "UPLOADING", "ANALYZING"}
TERMINAL_TASK_STATUSES = {"DONE", "FAILED"}
STRUCTURED_ARTIFACT_TYPES = {
    "top_json", "ebpf_metrics", "sys_metrics", "memory_json",
    "network_metrics", "database_metrics", "runtime_metrics",
}
ALLOWED_DIAGNOSIS_TRANSITIONS = {
    "CREATED": {"UNDERSTANDING", "USER_CANCELED", "FAILED"},
    "UNDERSTANDING": {"PLANNING", "NEEDS_SCOPE_CONFIRMATION", "TOPOLOGY_UNAVAILABLE", "FAILED"},
    "PLANNING": {"ANALYZING_EXISTING_DATA", "BUDGET_EXHAUSTED", "FAILED"},
    "ANALYZING_EXISTING_DATA": {"ANALYZING", "COLLECTING", "WAITING_APPROVAL", "INSUFFICIENT_EVIDENCE", "BUDGET_EXHAUSTED", "FAILED"},
    "COLLECTING": {"ANALYZING", "WAITING_APPROVAL", "NEED_MORE_EVIDENCE", "BUDGET_EXHAUSTED", "FAILED"},
    "ANALYZING": {"CONCLUDING", "WAITING_APPROVAL", "COLLECTING", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED", "FAILED"},
    "WAITING_APPROVAL": {"COLLECTING", "NEED_MORE_EVIDENCE", "BUDGET_EXHAUSTED", "USER_CANCELED", "FAILED"},
    "NEED_MORE_EVIDENCE": {"ANALYZING", "COLLECTING", "WAITING_APPROVAL", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED", "FAILED"},
    "CONCLUDING": {"COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED", "FAILED"},
}


class DiagnosisOrchestrator:
    def __init__(self, task_repository, store: DiagnosisStore | None = None):
        self.repo = task_repository
        self.store = store or DiagnosisStore()
        self.owner_prefix = f"{socket.gethostname()}:{os.getpid()}"
        self._operation_locks: dict[str, threading.Lock] = {}
        self._operation_locks_guard = threading.Lock()

    def _operation_lock(self, diagnosis_id: str) -> threading.Lock:
        with self._operation_locks_guard:
            return self._operation_locks.setdefault(diagnosis_id, threading.Lock())

    def _complete_node(
        self,
        diagnosis_id: str,
        node_name: str,
        *,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self.store.update_pipeline_node(
            diagnosis_id, node_name, "RUNNING", input_refs=input_refs,
        )
        self.store.update_pipeline_node(
            diagnosis_id, node_name, "COMPLETED",
            input_refs=input_refs, output_refs=output_refs, metrics=metrics,
        )

    def create(self, request: CreateDiagnosisRequest, creator_id: str = "demo_user") -> dict[str, Any]:
        intent = parse_diagnosis_intent(request)
        self._enforce_service_scope(intent.target_service)
        budget = self._effective_budget(request.budget_profile, request.budget)
        diagnosis_id = f"diag_session_{utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        snapshot = self._build_topology_snapshot(request, intent)
        self.store.create_topology_snapshot(snapshot)

        target_scope = self._build_target_scope(request, intent, budget)
        hypotheses = self._build_hypotheses(intent.symptom, target_scope)
        budget_usage = self._empty_budget_usage()
        budget_usage["model_calls"] = 1 if is_feature_enabled("nlp") else 0
        self.store.create_session({
            "diagnosis_id": diagnosis_id,
            "creator_id": creator_id,
            "raw_query": request.query,
            "normalized_intent": intent.model_dump(mode="json"),
            "target_scope": target_scope,
            "requested_time_range": intent.time_range.model_dump(mode="json"),
            "effective_time_range": self._effective_time_range(intent, budget),
            "topology_snapshot_id": snapshot["snapshot_id"],
            "status": DiagnosisStatus.CREATED.value,
            "policy_profile": request.budget_profile,
            "risk_budget": {
                "max_medium_risk_probes": budget.max_medium_risk_probes,
                "no_automatic_remediation": True,
                "registered_probes_only": True,
            },
            "resource_budget": budget.model_dump(mode="json"),
            "budget_used": budget_usage,
            "hypothesis_graph": {"hypotheses": hypotheses, "edges": []},
            "evaluation_oracle": (
                request.evaluation_oracle.model_dump(mode="json", exclude_none=True)
                if request.evaluation_oracle
                else {}
            ),
            "child_task_ids": [],
            "conclusion_versions": [],
            "model_version": get_ai_settings().model,
            "planner_version": f"{PLANNER_VERSION}:{intent.analysis_strategy.value.lower()}",
            "deadline_at": utcnow() + timedelta(minutes=budget.max_duration_minutes),
        })
        self._complete_node(
            diagnosis_id, "understand_intent",
            output_refs=["normalized_intent"],
            metrics={"ambiguity_count": len(intent.ambiguities)},
        )
        self._complete_node(
            diagnosis_id, "resolve_scope",
            input_refs=["normalized_intent", snapshot["snapshot_id"]],
            output_refs=["target_scope", snapshot["snapshot_id"]],
            metrics={"instance_count": len(target_scope["instances"])},
        )
        self._complete_node(
            diagnosis_id, "build_hypotheses",
            input_refs=["target_scope"],
            output_refs=[item["hypothesis_id"] for item in hypotheses],
            metrics={"hypothesis_count": len(hypotheses)},
        )
        self._transition(diagnosis_id, DiagnosisStatus.UNDERSTANDING, "intent_parsed")

        # Model notes are useful for the report but are not automatically
        # blocking. Scope confirmation is required only when the deterministic
        # resolver cannot establish a credible target anchor.
        if target_scope.get("scope_completeness") == "unresolved" or not target_scope["instances"]:
            self._transition(
                diagnosis_id,
                DiagnosisStatus.NEEDS_SCOPE_CONFIRMATION,
                "scope_confirmation_required",
                {"ambiguities": intent.ambiguities},
            )
            self._append_scope_help_conclusion(diagnosis_id, request.query, intent.ambiguities)
            for node_name in ("plan_evidence", "risk_gate", "run_probes", "normalize_evidence",
                              "analyze_evidence", "assess_cluster", "retrieve_knowledge"):
                self.store.update_pipeline_node(diagnosis_id, node_name, "SKIPPED")
            return self.store.get_detail(diagnosis_id) or {}

        self._transition(diagnosis_id, DiagnosisStatus.PLANNING, "plan_created")
        self._transition(
            diagnosis_id,
            DiagnosisStatus.ANALYZING_EXISTING_DATA,
            "existing_data_analysis_started",
        )

        existing_ids = self._find_reusable_tasks(
            target_scope,
            intent.time_range.start,
            intent.time_range.end,
            require_fresh=intent.diagnosis_mode == DiagnosisMode.LIVE,
        )
        if existing_ids:
            for task_id in existing_ids:
                task = self.repo.tasks.get(task_id)
                if task is None:
                    continue
                target = next((
                    item for item in target_scope.get("instances", [])
                    if item.get("agent_id") == task.agent_id
                    and int(item.get("pid", 0) or 0) == int(task.target_pid)
                ), None)
                if target is None:
                    continue
                reuse_key = f"{diagnosis_id}:reuse:{task_id}"
                self.store.add_probe({
                    "step_id": f"step_{hashlib.sha256(reuse_key.encode()).hexdigest()[:14]}",
                    "diagnosis_id": diagnosis_id,
                    "probe_id": "host_process_metrics",
                    "target": target,
                    "parameters": {"reused_task_id": task_id},
                    "reason": "复用时间窗、目标和质量均满足策略的已有结构化证据。",
                    "risk_level": "R1",
                    "requires_approval": False,
                    "status": "COMPLETED",
                    "task_id": task_id,
                })
            self._complete_node(
                diagnosis_id, "plan_evidence",
                input_refs=["target_scope", "hypothesis_graph"],
                output_refs=[f"task:{task_id}" for task_id in existing_ids],
                metrics={"reusable_task_count": len(existing_ids), "planned_probe_count": 0},
            )
            self._complete_node(
                diagnosis_id, "risk_gate",
                input_refs=[f"task:{task_id}" for task_id in existing_ids],
                output_refs=["reuse_existing_evidence"],
                metrics={"new_probe_count": 0},
            )
            self.store.update_pipeline_node(diagnosis_id, "run_probes", "SKIPPED")
            self.store.update_session(diagnosis_id, child_task_ids=existing_ids)
            existing_tasks = [self.repo.tasks[task_id] for task_id in existing_ids if task_id in self.repo.tasks]
            self._transition(
                diagnosis_id,
                DiagnosisStatus.ANALYZING,
                "evidence_analysis_started",
            )
            if self._analyze_tasks(diagnosis_id, existing_tasks):
                self._transition(diagnosis_id, DiagnosisStatus.CONCLUDING, "conclusion_generated")
                self._transition(diagnosis_id, DiagnosisStatus.COMPLETED, "diagnosis_completed")
                return self.store.get_detail(diagnosis_id) or {}

        if intent.diagnosis_mode == DiagnosisMode.HISTORICAL:
            # 历史诊断绝不通过当前采集来填补历史证据缺口。
            self._ensure_insufficient_conclusion(diagnosis_id, [])
            self._transition(
                diagnosis_id,
                DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                "historical_evidence_unavailable",
            )
            return self.store.get_detail(diagnosis_id) or {}

        self._plan_and_schedule(diagnosis_id, intent.symptom, target_scope, budget)
        probes = self.store.list_probes(diagnosis_id)
        self._complete_node(
            diagnosis_id, "plan_evidence",
            input_refs=["target_scope", "hypothesis_graph"],
            output_refs=[item["step_id"] for item in probes],
            metrics={"reusable_task_count": 0, "planned_probe_count": len(probes)},
        )
        self._complete_node(
            diagnosis_id, "risk_gate",
            input_refs=[item["step_id"] for item in probes],
            output_refs=[item["step_id"] for item in probes if item["status"] != "REJECTED_POLICY"],
            metrics={
                "planned_probe_count": len(probes),
                "approval_required_count": sum(1 for item in probes if item["requires_approval"]),
            },
        )
        self.store.update_pipeline_node(
            diagnosis_id, "run_probes", "RUNNING",
            input_refs=[item["step_id"] for item in probes],
            output_refs=[f"task:{item['task_id']}" for item in probes if item.get("task_id")],
        )
        if not probes:
            self._ensure_insufficient_conclusion(diagnosis_id, [])
            terminal = (
                DiagnosisStatus.BUDGET_EXHAUSTED
                if budget.max_total_probe_cpu_seconds == 0
                else DiagnosisStatus.INSUFFICIENT_EVIDENCE
            )
            self._transition(diagnosis_id, terminal, "empty_plan_terminal")
            return self.store.get_detail(diagnosis_id) or {}
        self._advance_locked(diagnosis_id)
        return self.store.get_detail(diagnosis_id) or {}

    def get(self, diagnosis_id: str, advance: bool = True) -> dict[str, Any] | None:
        item = self.store.get_session(diagnosis_id)
        if item is None:
            return None
        if advance and item["status"] not in TERMINAL_DIAGNOSIS_STATUSES:
            self.advance(diagnosis_id)
        return self.store.get_detail(diagnosis_id)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_sessions(limit=limit, offset=offset)

    def advance(self, diagnosis_id: str) -> dict[str, Any] | None:
        with self._operation_lock(diagnosis_id):
            owner = f"{self.owner_prefix}:{threading.get_ident()}:{uuid4().hex}"
            if not self.store.acquire_lease(diagnosis_id, owner):
                return self.store.get_detail(diagnosis_id)
            try:
                self._advance_locked(diagnosis_id)
            finally:
                self.store.release_lease(diagnosis_id, owner)
            return self.store.get_detail(diagnosis_id)

    def advance_active(self, limit: int = 100) -> None:
        """由后台扫描器调用，使恢复不依赖用户 GET 请求。"""
        for item in self.store.list_active_sessions(TERMINAL_DIAGNOSIS_STATUSES, limit=limit):
            try:
                self.advance(item["diagnosis_id"])
            except Exception as exc:
                self.store.record_event(item["diagnosis_id"], "advance_failed", {"error": str(exc)[:1000]})
                self.store.update_pipeline_node(
                    item["diagnosis_id"], "run_probes", "FAILED",
                    error_code="ADVANCE_FAILED", error_message=str(exc),
                )

    def approve(self, diagnosis_id: str, request: ApprovalRequest) -> dict[str, Any]:
        with self._operation_lock(diagnosis_id):
            owner = f"{self.owner_prefix}:{threading.get_ident()}:{uuid4().hex}"
            if not self.store.acquire_lease(diagnosis_id, owner):
                raise ValueError("诊断正在由另一个操作推进，请重试")
            try:
                return self._approve_locked(diagnosis_id, request)
            finally:
                self.store.release_lease(diagnosis_id, owner)

    def _approve_locked(self, diagnosis_id: str, request: ApprovalRequest) -> dict[str, Any]:
        session = self.store.get_session(diagnosis_id)
        if session is None:
            raise ValueError("诊断不存在")
        if session["status"] in TERMINAL_DIAGNOSIS_STATUSES:
            raise ValueError(f"终态诊断不能审批: {session['status']}")
        step = self.store.get_probe(request.step_id)
        if step is None or step["diagnosis_id"] != diagnosis_id:
            raise ValueError("审批步骤不存在或不属于当前诊断")
        if not step["requires_approval"]:
            raise ValueError("该探针不需要审批")
        if step["status"] not in {"WAITING_APPROVAL", "APPROVED"}:
            raise ValueError(f"当前探针状态不可审批: {step['status']}")

        if request.decision == "reject":
            self.store.update_probe(
                request.step_id,
                status="REJECTED",
                approved_by=request.approver_id,
                approved_at=utcnow(),
            )
            self._transition(
                diagnosis_id,
                DiagnosisStatus.NEED_MORE_EVIDENCE,
                "approval_rejected",
                {"step_id": request.step_id, "approver_id": request.approver_id},
            )
            self._advance_locked(diagnosis_id)
            return self.store.get_detail(diagnosis_id) or {}

        approved_r2 = sum(
            1 for probe in self.store.list_probes(diagnosis_id)
            if probe["risk_level"] == "R2" and probe["status"] in {
                "APPROVED", "SCHEDULED", "RUNNING", "COMPLETED",
            }
        )
        limit = int(session["risk_budget"].get("max_medium_risk_probes", 0))
        if approved_r2 >= limit:
            self._transition(
                diagnosis_id,
                DiagnosisStatus.BUDGET_EXHAUSTED,
                "risk_budget_exhausted",
                {"max_medium_risk_probes": limit},
            )
            return self.store.get_detail(diagnosis_id) or {}

        active_count = 0
        for probe in self.store.list_probes(diagnosis_id):
            task_id = probe.get("task_id")
            task = self.repo.tasks.get(task_id) if task_id else None
            if task is not None and status_value(task.status) in ACTIVE_TASK_STATUSES:
                active_count += 1
        parallel_limit = int(session["resource_budget"].get("max_parallel_probes", 1))
        if active_count >= parallel_limit:
            raise ValueError("并发探针预算已用尽，请等待当前探针完成后重试审批")

        duration = int(step["parameters"].get("duration_sec", 0))
        used_duration = int(session["budget_used"].get("probe_duration_seconds", 0))
        duration_limit = min(
            int(session["resource_budget"].get("max_duration_minutes", 10)) * 60,
            int(session["resource_budget"].get("max_total_probe_cpu_seconds", 120)),
        )
        if used_duration + duration > duration_limit:
            self._transition(
                diagnosis_id,
                DiagnosisStatus.BUDGET_EXHAUSTED,
                "resource_budget_exhausted",
                {"probe_duration_limit_seconds": duration_limit},
            )
            return self.store.get_detail(diagnosis_id) or {}

        self.store.update_probe(
            request.step_id,
            status="APPROVED",
            approved_by=request.approver_id,
            approved_at=utcnow(),
        )
        self.store.record_event(
            diagnosis_id,
            "approval_granted",
            {"step_id": request.step_id, "approver_id": request.approver_id, "scope": request.scope},
        )
        self.store.enqueue_probe(request.step_id)
        self._drain_probe_outbox(diagnosis_id)
        approved_step = self.store.get_probe(request.step_id) or {}
        self.store.update_pipeline_node(
            diagnosis_id, "run_probes", "RUNNING",
            input_refs=[request.step_id],
            output_refs=[f"task:{approved_step['task_id']}"] if approved_step.get("task_id") else [],
            metrics={"approved_step_id": request.step_id},
        )
        self._transition(
            diagnosis_id,
            DiagnosisStatus.COLLECTING,
            "probe_started",
            {"step_id": request.step_id},
        )
        return self.store.get_detail(diagnosis_id) or {}

    def _advance_locked(self, diagnosis_id: str) -> None:
        session = self.store.get_session(diagnosis_id)
        if session is None or session["status"] in TERMINAL_DIAGNOSIS_STATUSES:
            return
        deadline = session.get("deadline_at")
        if deadline is not None:
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if utcnow() >= deadline:
                for probe in self.store.list_probes(diagnosis_id):
                    if probe["status"] not in {"COMPLETED", "FAILED", "TIMED_OUT", "REJECTED", "UNAVAILABLE", "SKIPPED"}:
                        self.store.update_probe(probe["step_id"], status="TIMED_OUT", error_code="DIAGNOSIS_DEADLINE")
                self._ensure_insufficient_conclusion(diagnosis_id, [])
                self._transition(diagnosis_id, DiagnosisStatus.INSUFFICIENT_EVIDENCE, "diagnosis_deadline_reached")
                return
        probes = self.store.list_probes(diagnosis_id)
        child_ids = list(session.get("child_task_ids", []))

        for probe in probes:
            task_id = probe.get("task_id")
            if not task_id:
                continue
            task = self.repo.tasks.get(task_id)
            if task is None:
                self.store.update_probe(probe["step_id"], status="FAILED")
                continue
            task_status = status_value(task.status)
            if task_status in ACTIVE_TASK_STATUSES and probe["status"] != "RUNNING":
                self.store.update_probe(probe["step_id"], status="RUNNING")
            elif task_status == "DONE" and probe["status"] != "COMPLETED":
                self.store.update_probe(probe["step_id"], status="COMPLETED")
            elif task_status == "FAILED" and probe["status"] != "FAILED":
                self.store.update_probe(probe["step_id"], status="FAILED")
            if task_id not in child_ids:
                child_ids.append(task_id)

        if child_ids != session.get("child_task_ids", []):
            session = self.store.update_session(diagnosis_id, child_task_ids=child_ids)

        # A completed batch frees slots for READY targets before any analysis.
        self._fill_ready_queue(diagnosis_id)
        probes = self.store.list_probes(diagnosis_id)
        child_ids = list((self.store.get_session(diagnosis_id) or {}).get("child_task_ids", []))

        terminal_tasks = []
        active_tasks = []
        for task_id in child_ids:
            task = self.repo.tasks.get(task_id)
            if task is None:
                continue
            task_status = status_value(task.status)
            if task_status in TERMINAL_TASK_STATUSES:
                terminal_tasks.append(task)
            elif task_status in ACTIVE_TASK_STATUSES:
                active_tasks.append(task)

        waiting = [probe for probe in probes if probe["status"] == "WAITING_APPROVAL"]
        if session["status"] == DiagnosisStatus.WAITING_APPROVAL.value and waiting:
            # Completed R1 tasks remain children while an R2 gate is open.
            # Re-analyzing them on every GET would attempt the illegal
            # WAITING_APPROVAL -> ANALYZING transition and return HTTP 500.
            self.store.update_pipeline_node(
                diagnosis_id, "run_probes", "WAITING",
                input_refs=[probe["step_id"] for probe in waiting],
                metrics={"approval_required_count": len(waiting)},
            )
            return

        # A faster worker may finish while another target is still collecting.
        # Cross-node attribution must use one coherent collection round, so do
        # not conclude from the first terminal child task.
        if active_tasks:
            self.store.update_pipeline_node(
                diagnosis_id, "run_probes", "RUNNING",
                output_refs=[f"task:{task.id}" for task in active_tasks],
                metrics={
                    "active_task_count": len(active_tasks),
                    "terminal_task_count": len(terminal_tasks),
                },
            )
            if session["status"] != DiagnosisStatus.COLLECTING.value:
                self._transition(diagnosis_id, DiagnosisStatus.COLLECTING, "probe_started")
            return

        if terminal_tasks:
            self.store.update_pipeline_node(
                diagnosis_id, "run_probes", "COMPLETED",
                output_refs=[f"task:{task.id}" for task in terminal_tasks],
                metrics={
                    "terminal_task_count": len(terminal_tasks),
                    "failed_task_count": sum(1 for task in terminal_tasks if status_value(task.status) == "FAILED"),
                },
            )
            self._transition(diagnosis_id, DiagnosisStatus.ANALYZING, "evidence_analysis_started")
            informative = self._analyze_tasks(diagnosis_id, terminal_tasks)
            if informative:
                for probe in self.store.list_probes(diagnosis_id):
                    if probe["status"] == "WAITING_APPROVAL":
                        self.store.update_probe(probe["step_id"], status="SKIPPED")
                latest = (
                    (self.store.get_session(diagnosis_id) or {}).get("conclusion_versions", [])
                    or [{}]
                )[-1]
                failure_is_the_diagnosed_signal = (
                    latest.get("cluster_assessment", {}).get("classification")
                    == "single_instance_storage_path_failure"
                )
                final_status = (
                    DiagnosisStatus.PARTIAL_COMPLETED
                    if (
                        any(status_value(task.status) == "FAILED" for task in terminal_tasks)
                        and not failure_is_the_diagnosed_signal
                    )
                    else DiagnosisStatus.COMPLETED
                )
                self._transition(diagnosis_id, DiagnosisStatus.CONCLUDING, "conclusion_generated")
                self._transition(diagnosis_id, final_status, "diagnosis_completed")
                return
            self._plan_adaptive_r2(diagnosis_id, terminal_tasks)

        waiting = [probe for probe in self.store.list_probes(diagnosis_id) if probe["status"] == "WAITING_APPROVAL"]
        if waiting:
            self.store.update_pipeline_node(
                diagnosis_id, "run_probes", "WAITING",
                input_refs=[probe["step_id"] for probe in waiting],
                metrics={"approval_required_count": len(waiting)},
            )
            self._transition(
                diagnosis_id,
                DiagnosisStatus.WAITING_APPROVAL,
                "approval_required",
                {"step_ids": [probe["step_id"] for probe in waiting]},
            )
            return

        if terminal_tasks:
            final = (
                DiagnosisStatus.PARTIAL_COMPLETED
                if any(status_value(task.status) == "FAILED" for task in terminal_tasks)
                else DiagnosisStatus.INSUFFICIENT_EVIDENCE
            )
            self._ensure_insufficient_conclusion(diagnosis_id, terminal_tasks)
            self._transition(diagnosis_id, final, "diagnosis_completed")
            return

        probes = self.store.list_probes(diagnosis_id)
        if probes and all(p["status"] in {
            "UNAVAILABLE", "REJECTED", "REJECTED_POLICY", "INVALID", "FAILED", "SKIPPED", "TIMED_OUT",
        } for p in probes):
            self._ensure_insufficient_conclusion(diagnosis_id, [])
            self._transition(
                diagnosis_id,
                DiagnosisStatus.INSUFFICIENT_EVIDENCE,
                "diagnosis_completed",
            )

    def _plan_adaptive_r2(self, diagnosis_id: str, tasks: list[Any]) -> bool:
        session = self.store.get_session(diagnosis_id) or {}
        strategy = session.get("normalized_intent", {}).get(
            "analysis_strategy", "CONSTRAINED_HYBRID",
        )
        if strategy == "DECISION_TREE":
            return False
        if int(session.get("risk_budget", {}).get("max_medium_risk_probes", 0)) <= 0:
            return False
        probes = self.store.list_probes(diagnosis_id)
        if any(item["risk_level"] == "R2" for item in probes):
            return any(item["status"] == "WAITING_APPROVAL" for item in probes)
        intent = session.get("normalized_intent", {})
        r2_ids = [probe_id for probe_id in choose_probe_ids(intent.get("symptom", "")) if get_probe(probe_id).risk_level == "R2"]
        if not r2_ids:
            return False
        scored: list[tuple[float, Any]] = []
        for task in tasks:
            values = {kind: value for kind, value, _ in self._structured_artifacts(self.repo.artifacts.get(task.id, []))}
            summary = _sys_summary(values.get("sys_metrics"))
            score = sum(1.0 for value in _pressure_flags(summary, values).values() if value)
            scored.append((score, task))
        if not scored:
            return False
        _, task = max(scored, key=lambda item: item[0])
        target = self._target_for_task(diagnosis_id, task)
        definition = get_probe(r2_ids[0])
        key = f"{diagnosis_id}:{definition.probe_id}:{target.get('instance_id')}:adaptive"
        step_id = f"step_{hashlib.sha256(key.encode()).hexdigest()[:14]}"
        self.store.add_probe({
            "step_id": step_id,
            "diagnosis_id": diagnosis_id,
            "probe_id": definition.probe_id,
            "target": target,
            "parameters": {"duration_sec": definition.default_duration_seconds, "sample_rate": definition.default_sample_rate},
            "reason": "R1 全目标指标显示区分性证据缺口，仅在压力最显著节点请求 R2",
            "risk_level": definition.risk_level,
            "requires_approval": True,
            "status": "WAITING_APPROVAL",
        })
        return True

    def _plan_and_schedule(
        self,
        diagnosis_id: str,
        symptom: str,
        target_scope: dict[str, Any],
        budget: DiagnosisBudget,
    ) -> None:
        instances = target_scope["instances"][:budget.max_service_instances]
        session = self.store.get_session(diagnosis_id) or {}
        strategy = session.get("normalized_intent", {}).get(
            "analysis_strategy", "CONSTRAINED_HYBRID",
        )
        probe_ids = (
            [item.probe_id for item in list_probes()]
            if strategy == "EXPLORATORY"
            else choose_probe_ids(symptom)
        )
        planned: list[ProbePlan] = []
        planned_duration = 0
        duration_limit = min(budget.max_duration_minutes * 60, budget.max_total_probe_cpu_seconds)
        for index, instance in enumerate(instances):
            for probe_id in probe_ids:
                definition = get_probe(probe_id)
                if definition.risk_level == "R2" and (
                    strategy != "DECISION_TREE" or index > 0
                ):
                    # R2 is selected adaptively after the all-target R1 round.
                    continue
                duration = min(definition.default_duration_seconds, definition.max_duration_seconds)
                if planned_duration + duration > duration_limit:
                    continue
                planned_duration += duration
                key = f"{diagnosis_id}:{probe_id}:{instance['instance_id']}"
                planned.append(ProbePlan(
                    step_id=f"step_{hashlib.sha256(key.encode()).hexdigest()[:14]}",
                    probe_id=probe_id,
                    target=instance,
                    parameters={"duration_sec": duration, "sample_rate": definition.default_sample_rate},
                    reason=(
                        f"固定决策树路径：用于验证 {', '.join(definition.applicable_hypotheses[:3])}"
                        if strategy == "DECISION_TREE"
                        else f"用于区分 {', '.join(definition.applicable_hypotheses[:3])} 等候选假设"
                    ),
                    risk_level=definition.risk_level,
                    requires_approval=definition.requires_approval,
                ))

        for plan in planned:
            status = "WAITING_APPROVAL" if plan.requires_approval else "READY"
            self.store.add_probe({
                **plan.model_dump(mode="json"),
                "diagnosis_id": diagnosis_id,
                "status": status,
            })
        self._fill_ready_queue(diagnosis_id)

    def _fill_ready_queue(self, diagnosis_id: str) -> None:
        session = self.store.get_session(diagnosis_id) or {}
        limit = int(session.get("resource_budget", {}).get("max_parallel_probes", 1))
        probes = self.store.list_probes(diagnosis_id)
        active = sum(1 for item in probes if item["status"] in {"SCHEDULED", "RUNNING"})
        for item in probes:
            if active >= limit:
                break
            if item["status"] != "READY":
                continue
            self.store.enqueue_probe(item["step_id"])
            active += 1
        self._drain_probe_outbox(diagnosis_id)

    def _drain_probe_outbox(self, diagnosis_id: str) -> None:
        for item in self.store.list_pending_outbox(diagnosis_id):
            try:
                self._schedule_probe(item["step_id"])
                self.store.complete_outbox(item["outbox_id"])
            except Exception as exc:
                step = self.store.get_probe(item["step_id"])
                if step:
                    self.store.update_probe(
                        item["step_id"], status="FAILED", retry_count=int(step.get("retry_count", 0)) + 1,
                        error_code="TASK_CREATION_FAILED", error_message=str(exc),
                    )
                self.store.complete_outbox(item["outbox_id"], str(exc))

    def _schedule_probe(self, step_id: str) -> None:
        step = self.store.get_probe(step_id)
        if step is None or step.get("task_id"):
            return
        definition = get_probe(step["probe_id"])
        target = step["target"]
        session = self.store.get_session(step["diagnosis_id"])
        if session is None:
            self.store.update_probe(step_id, status="INVALID")
            return
        allowed_targets = {
            (item.get("instance_id"), item.get("agent_id"), item.get("pid"))
            for item in session.get("target_scope", {}).get("instances", [])
        }
        target_key = (target.get("instance_id"), target.get("agent_id"), target.get("pid"))
        if target_key not in allowed_targets or step["risk_level"] != definition.risk_level:
            self.store.update_probe(step_id, status="REJECTED_POLICY")
            return
        if definition.requires_approval and step["status"] not in {"APPROVED", "SCHEDULED"}:
            self.store.update_probe(step_id, status="WAITING_APPROVAL")
            return
        self._enforce_service_scope(target.get("service_id"))
        try:
            duration = int(step["parameters"]["duration_sec"])
            sample_rate = int(step["parameters"]["sample_rate"])
        except (KeyError, TypeError, ValueError):
            self.store.update_probe(step_id, status="INVALID")
            return
        if not (1 <= duration <= min(definition.max_duration_seconds, MAX_TASK_DURATION_SEC)):
            self.store.update_probe(step_id, status="REJECTED_POLICY")
            return
        if not (MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE):
            self.store.update_probe(step_id, status="REJECTED_POLICY")
            return
        agent = self.repo.agents.get(target["agent_id"])
        if agent is None or status_value(agent.status) != "ONLINE":
            self.store.update_probe(step_id, status="UNAVAILABLE")
            return
        capabilities = set(getattr(agent, "capabilities", []) or [])
        if definition.runner_task_kind not in capabilities:
            self.store.update_probe(step_id, status="UNAVAILABLE")
            return

        # 恢复时先通过幂等键查找已创建任务，避免重复下发。
        existing_task = self.repo.get_task_by_diagnosis_step_id(step_id)
        if existing_task is not None:
            self.store.update_probe(step_id, status="SCHEDULED", task_id=existing_task.id)
            self._append_child_task(step["diagnosis_id"], existing_task.id, definition)
            return

        task = self.repo.create_task(CreateTaskRequest(
            name=f"AI诊断:{definition.name}:{target['service_id']}",
            agent_id=target["agent_id"],
            target_pid=target["pid"],
            collector_type=definition.runner_task_kind,
            duration_sec=duration,
            sample_rate=sample_rate,
            options={
                "diagnosis_id": step["diagnosis_id"],
                "diagnosis_step_id": step_id,
                "probe_id": definition.probe_id,
                "registered_probe": True,
            },
        ))
        self.store.update_probe(step_id, status="SCHEDULED", task_id=task.id)
        self._append_child_task(step["diagnosis_id"], task.id, definition)

    def _append_child_task(self, diagnosis_id: str, task_id: str, definition) -> None:
        session = self.store.get_session(diagnosis_id)
        if session is None:
            return
        task_ids = list(session.get("child_task_ids", []))
        if task_id not in task_ids:
            task_ids.append(task_id)
        usage = dict(session.get("budget_used", {}))
        usage["hosts"] = len({
            probe["target"].get("host_id")
            for probe in self.store.list_probes(diagnosis_id)
            if probe.get("task_id")
        })
        usage["service_instances"] = len({
            probe["target"].get("instance_id")
            for probe in self.store.list_probes(diagnosis_id)
            if probe.get("task_id")
        })
        usage["probes"] = sum(1 for probe in self.store.list_probes(diagnosis_id) if probe.get("task_id"))
        usage["medium_risk_probes"] = sum(
            1 for probe in self.store.list_probes(diagnosis_id)
            if probe.get("task_id") and probe["risk_level"] == "R2"
        )
        usage["probe_duration_seconds"] = usage.get("probe_duration_seconds", 0) + definition.default_duration_seconds
        self.store.update_session(
            diagnosis_id,
            child_task_ids=task_ids,
            budget_used=usage,
        )

    def _analyze_tasks(self, diagnosis_id: str, tasks: list[Any]) -> bool:
        self.store.update_pipeline_node(
            diagnosis_id, "normalize_evidence", "RUNNING",
            input_refs=[f"task:{task.id}" for task in tasks],
        )
        all_candidates: list[dict[str, Any]] = []
        task_observations: list[dict[str, Any]] = []
        missing: list[str] = []
        failed_targets: list[str] = []
        for task in tasks:
            status = status_value(task.status)
            artifacts = self.repo.artifacts.get(task.id, [])
            evidence_ids = [self._add_task_evidence(diagnosis_id, task)]
            structured = self._structured_artifacts(artifacts)
            for artifact_type, value, artifact in structured:
                evidence_ids.append(self._add_artifact_evidence(
                    diagnosis_id, task, artifact_type, value, artifact,
                ))
            if status == "FAILED":
                failed_targets.append(f"{task.agent_id}:{task.target_pid}")

            values = {kind: value for kind, value, _ in structured}
            task_events = [self.repo.as_dict(event) for event in self.repo.events if event.task_id == task.id]
            evidence = collect_evidence(
                task_id=task.id,
                task_record=task,
                top_functions=values.get("top_json") if isinstance(values.get("top_json"), list) else None,
                ebpf_metrics=values.get("ebpf_metrics") if isinstance(values.get("ebpf_metrics"), dict) else None,
                sys_metrics=values.get("sys_metrics") if isinstance(values.get("sys_metrics"), dict) else None,
                failure_events=[event.get("reason", "") for event in task_events if event.get("reason")],
                agent_stats=self.repo.agent_metrics.get(task.agent_id, {}),
            )
            candidates = generate_candidates(evidence, self.repo.get_feedback_priors())
            calibrated = calibrate(candidates, evidence, self.repo.get_feedback_priors())
            for candidate in calibrated:
                if candidate.candidate_id == "insufficient_data":
                    continue
                all_candidates.append({
                    "candidate_id": candidate.candidate_id,
                    "description": candidate.description,
                    "evidence_refs": evidence_ids,
                    "missing_evidence": candidate.missing_evidence,
                    "score_components": {
                        "rule_match": _quality(candidate.rule_score),
                        "evidence_quality": _quality(candidate.evidence_quality),
                        "baseline_support": _quality(candidate.baseline_support),
                        "source_independence": _quality(candidate.cross_collector_agreement),
                    },
                    "sort_score": candidate.final_confidence,
                })
            if structured:
                task_observations.append(
                    self._build_task_observation(diagnosis_id, task, values, evidence_ids)
                )
            elif status == "FAILED":
                task_observations.append(
                    self._build_failed_task_observation(diagnosis_id, task, evidence_ids)
                )
                missing.append(f"{task.id}:structured_artifact")
            else:
                missing.append(f"{task.id}:structured_artifact")

        evidence_items = self.store.list_evidence(diagnosis_id)
        evidence_ids = [item["evidence_id"] for item in evidence_items]
        self.store.update_pipeline_node(
            diagnosis_id, "normalize_evidence", "COMPLETED",
            input_refs=[f"task:{task.id}" for task in tasks],
            output_refs=evidence_ids,
            metrics={"evidence_count": len(evidence_items), "observation_count": len(task_observations)},
        )

        if not all_candidates and not task_observations:
            self.store.update_pipeline_node(
                diagnosis_id, "analyze_evidence", "SKIPPED",
                metrics={"reason": "no_structured_observation"},
            )
            return False
        self.store.update_pipeline_node(
            diagnosis_id, "analyze_evidence", "RUNNING", input_refs=evidence_ids,
        )
        all_candidates.sort(key=lambda item: item["sort_score"], reverse=True)
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in all_candidates:
            if candidate["candidate_id"] in seen:
                continue
            seen.add(candidate["candidate_id"])
            candidate.pop("sort_score", None)
            candidate["rank"] = len(deduped) + 1
            candidate["confidence_level"] = self._confidence_level(candidate)
            candidate["supporting_claims"] = [{
                "statement": candidate["description"],
                "evidence_refs": candidate["evidence_refs"],
                "strength": "medium" if len(candidate["evidence_refs"]) > 1 else "weak",
            }]
            deduped.append(candidate)
            if len(deduped) >= 3:
                break

        findings = analyze_observations(task_observations)
        self.store.update_pipeline_node(
            diagnosis_id, "analyze_evidence", "COMPLETED",
            input_refs=evidence_ids,
            output_refs=[item["finding_id"] for item in findings],
            metrics={"finding_count": len(findings), "candidate_count": len(deduped)},
        )

        self.store.update_pipeline_node(
            diagnosis_id, "assess_cluster", "RUNNING",
            input_refs=[item["finding_id"] for item in findings] + evidence_ids,
        )
        cluster_assessment = self._build_cluster_assessment(diagnosis_id, task_observations)
        if cluster_assessment["classification"] == "single_instance_storage_path_failure":
            for candidate in deduped:
                if candidate["candidate_id"] != "artifact_storage_unreachable":
                    continue
                candidate["evidence_refs"] = list(cluster_assessment["evidence_refs"])
                candidate["confidence_level"] = "高"
                candidate["score_components"]["baseline_support"] = "high"
                candidate["score_components"]["source_independence"] = "high"
                candidate["supporting_claims"] = [
                    {
                        "statement": candidate["description"],
                        "evidence_refs": list(cluster_assessment["root_location"]["evidence_refs"]),
                        "strength": "strong",
                    },
                    {
                        "statement": "健康 Worker 同期上传成功，排除对象存储整体不可用。",
                        "evidence_refs": [
                            ref for ref in cluster_assessment["evidence_refs"]
                            if ref not in cluster_assessment["root_location"]["evidence_refs"]
                        ],
                        "strength": "strong",
                    },
                ]
        cluster_item = cluster_finding(cluster_assessment)
        findings.append(cluster_item)
        self.store.update_pipeline_node(
            diagnosis_id, "assess_cluster", "COMPLETED",
            output_refs=[cluster_item["finding_id"]],
            metrics={"classification": cluster_assessment["classification"]},
        )

        self.store.update_pipeline_node(
            diagnosis_id, "retrieve_knowledge", "RUNNING",
            input_refs=[item["finding_id"] for item in findings],
        )
        session = self.store.get_session(diagnosis_id) or {}
        knowledge_context = retrieve_knowledge(session.get("raw_query", ""), findings)
        knowledge_refs = [item["knowledge_id"] for item in knowledge_context]
        self.store.update_pipeline_node(
            diagnosis_id, "retrieve_knowledge", "COMPLETED",
            output_refs=knowledge_refs,
            metrics={"knowledge_count": len(knowledge_refs)},
        )

        self.store.update_pipeline_node(
            diagnosis_id, "generate_actions", "RUNNING",
            input_refs=evidence_ids + [item["finding_id"] for item in findings],
        )
        diagnostic_actions = self._build_reviewable_commands(
            diagnosis_id,
            task_observations,
            cluster_assessment,
        )
        self.store.update_pipeline_node(
            diagnosis_id, "generate_actions", "COMPLETED",
            output_refs=[item["action_id"] for item in diagnostic_actions],
            metrics={"action_count": len(diagnostic_actions)},
        )
        conclusion = {
            "version": len((self.store.get_session(diagnosis_id) or {}).get("conclusion_versions", [])) + 1,
            "generated_at": utcnow().isoformat(),
            "summary": cluster_assessment["summary"] or f"形成 {len(deduped)} 个有证据关联的根因候选；结论仍需结合反证和人工确认。",
            "evidence_scope": "reproduction" if session.get("normalized_intent", {}).get("diagnosis_mode") == "REPRODUCTION" else "incident",
            "confidence_level": cluster_assessment["confidence_level"] or (deduped[0]["confidence_level"] if deduped else "不可判断"),
            "cluster_assessment": cluster_assessment,
            "root_location": cluster_assessment["root_location"],
            "domain_cause": cluster_assessment["domain_cause"],
            "findings": findings,
            "root_cause_candidates": deduped,
            "ruled_out": cluster_assessment["ruled_out"],
            "knowledge_refs": knowledge_refs,
            "knowledge_context": knowledge_context,
            "actions": diagnostic_actions,
            "diagnostic_commands": diagnostic_actions,
            "recommendations": self._build_recommendations(cluster_assessment),
            "limitations": sorted(set(missing + (["部分目标采集失败"] if failed_targets else []))),
            "coverage": {
                "task_count": len(tasks),
                "failed_targets": failed_targets,
                "evidence_count": len(self.store.list_evidence(diagnosis_id)),
            },
        }
        self.store.update_pipeline_node(
            diagnosis_id, "verify_report", "RUNNING",
            input_refs=evidence_ids + knowledge_refs + [item["action_id"] for item in diagnostic_actions],
        )
        verification = verify_report(conclusion, evidence_items, session.get("target_scope", {}), session)
        conclusion["verification"] = verification
        if verification["status"] != "passed":
            self.store.update_pipeline_node(
                diagnosis_id, "verify_report", "FAILED",
                error_code="REPORT_VERIFICATION_FAILED",
                error_message="; ".join(verification["issues"]),
                metrics=verification,
            )
            self.store.record_event(
                diagnosis_id, "report_verification_failed", {"issues": verification["issues"]},
            )
            return False
        self.store.update_pipeline_node(
            diagnosis_id, "verify_report", "COMPLETED",
            output_refs=["verified_report"], metrics=verification,
        )
        self._append_conclusion(diagnosis_id, conclusion)
        self._update_hypotheses(diagnosis_id, deduped, cluster_assessment)
        return cluster_assessment["classification"] not in {
            "insufficient_evidence", "scope_unresolved",
        }

    def _build_task_observation(
        self,
        diagnosis_id: str,
        task,
        values: dict[str, Any],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        target = self._target_for_task(diagnosis_id, task)
        summary = _sys_summary(values.get("sys_metrics"))
        top_items = values.get("top_json") if isinstance(values.get("top_json"), list) else []
        top_name = str((top_items[0] or {}).get("name", "")) if top_items else ""
        top_percent = float((top_items[0] or {}).get("percent", 0.0) or 0.0) if top_items else 0.0
        pressure = _pressure_flags(summary, values)
        return {
            "task_id": task.id,
            "collector_type": task.collector_type,
            "target": target,
            "collection_status": status_value(task.status),
            "status_reason": task.status_reason or "",
            "failure_kind": (
                _task_failure_kind(task.status_reason)
                if status_value(task.status) == "FAILED"
                else None
            ),
            "summary": summary,
            "facts": _normalized_facts(values, summary),
            "fact_domains": normalize_sys_metrics(values.get("sys_metrics")) if values.get("sys_metrics") else {},
            "top_function": {"name": top_name, "percent": top_percent},
            "pressure": pressure,
            "evidence_refs": evidence_refs,
        }

    def _build_failed_task_observation(
        self,
        diagnosis_id: str,
        task,
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "collector_type": task.collector_type,
            "target": self._target_for_task(diagnosis_id, task),
            "collection_status": status_value(task.status),
            "status_reason": task.status_reason or "",
            "failure_kind": _task_failure_kind(task.status_reason),
            "summary": {},
            "facts": {},
            "fact_domains": {},
            "top_function": {"name": "", "percent": 0.0},
            "pressure": {},
            "evidence_refs": evidence_refs,
        }

    def _target_for_task(self, diagnosis_id: str, task) -> dict[str, Any]:
        session = self.store.get_session(diagnosis_id) or {}
        probes = self.store.list_probes(diagnosis_id)
        for probe in probes:
            if probe.get("task_id") == task.id:
                return dict(probe.get("target", {}))
        for item in session.get("target_scope", {}).get("instances", []):
            if item.get("agent_id") == task.agent_id and int(item.get("pid", 0) or 0) == int(task.target_pid):
                return dict(item)
        return {
            "service_id": "unknown",
            "instance_id": f"{task.agent_id}:{task.target_pid}",
            "host_id": "unknown",
            "agent_id": task.agent_id,
            "pid": task.target_pid,
        }

    def _build_cluster_assessment(
        self,
        diagnosis_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session = self.store.get_session(diagnosis_id) or {}
        scope = session.get("target_scope", {})
        return assess_cluster(scope, observations)

    def _build_reviewable_commands(
        self,
        diagnosis_id: str,
        observations: list[dict[str, Any]],
        assessment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        commands = [inspect_session_action(diagnosis_id, assessment.get("evidence_refs", []))]
        target_obs = observations[0] if observations else None
        if target_obs:
            target = target_obs["target"]
            commands.append(collect_action(
                action_id="act_low_risk_metrics", title="补充低风险系统指标",
                collector_type="sys_metrics", target=target, duration_sec=15, sample_rate=11,
                comment="低开销采集 CPU、内存、线程、FD、网络与 I/O 等待趋势，适合复核当前判断。",
                risk_level="R1", evidence_refs=target_obs.get("evidence_refs", []),
                confidence_level="高",
            ))
            if assessment.get("classification") in {
                "self_code_or_process_pressure",
                "insufficient_evidence",
            }:
                commands.append(collect_action(
                    action_id="act_cpu_profile", title="申请一次 CPU Profile",
                    collector_type="perf_cpu", target=target, duration_sec=15, sample_rate=49,
                    comment="中风险深度采样，可能带来额外开销；必须由人确认窗口和目标后再执行。",
                    risk_level="R2", evidence_refs=target_obs.get("evidence_refs", []),
                    confidence_level="中",
                ))
            if assessment.get("classification") in {
                "same_host_noisy_neighbor",
                "host_resource_contention",
                "insufficient_evidence",
            }:
                commands.append(collect_action(
                    action_id="act_io_latency", title="申请一次 I/O 延迟探针",
                    collector_type="ebpf_io", target=target, duration_sec=15, sample_rate=11,
                    comment="中风险 eBPF 探针，用于确认块设备延迟和宿主机级 I/O 争抢；需要人工审批。",
                    risk_level="R2", evidence_refs=assessment.get("evidence_refs", []),
                    confidence_level="中",
                ))
        return commands

    @staticmethod
    def _build_recommendations(assessment: dict[str, Any]) -> list[dict[str, Any]]:
        """由已验证领域分类生成可执行、可复核的分层建议。"""

        domain = assessment.get("domain_cause", {}).get("type", "unknown")
        subtype = assessment.get("domain_cause", {}).get("subtype", "unknown")
        location = assessment.get("root_location", {}).get("type", "unknown")
        refs = list(dict.fromkeys(assessment.get("evidence_refs", [])))
        optimization = {
            "cpu": (
                "优化热点函数或线程竞争",
                "依据 CPU Profile 的 TopN/火焰图定位高占比调用栈，优先评估算法复杂度、重复计算、缓存和锁粒度。",
            ),
            "io": (
                "降低共享 I/O 争抢",
                "核对块设备延迟与队列深度，拆分高 I/O 工作负载、合并小 I/O，并评估存储限额或独立卷。",
            ),
            "memory": (
                "控制进程内存增长",
                "检查 RSS/PSS、Swap 和分配热点，修复未释放对象或无界缓存，并设置与工作集匹配的资源限制。",
            ),
            "network": (
                "降低网络与下游调用开销",
                "检查重传、连接池、超时和重试放大，优先修复异常依赖并限制无界重试。",
            ),
            "database": (
                "消除数据库等待链",
                "核对慢查询、锁等待和连接池耗尽，优化索引与事务范围，避免直接执行未经验证的结构变更。",
            ),
            "runtime": (
                "优化运行时暂停或锁竞争",
                "结合 GC、线程和运行时 Profile 调整对象生命周期、堆配置或临界区。",
            ),
        }.get(domain, (
            "补充区分性证据",
            "当前领域尚不可判断；先完成缺失探针并重新校验证据覆盖率，不应直接修改生产配置。",
        ))
        if subtype == "agent_to_object_storage_connectivity":
            optimization = (
                "检查故障 Worker 的对象存储上传路径",
                "对比两个 Worker 到 MinIO/S3 endpoint 的 TCP 连通性，并检查源地址定向防火墙、"
                "路由、TLS、bucket 与访问凭据；健康 Worker 已形成对象存储整体可用的反证。",
            )
        target_hint = assessment.get("root_location", {}).get("target_ref") or "候选实例"
        return [
            {
                "recommendation_id": "rec_mitigation",
                "category": "mitigation",
                "title": "人工确认后的临时缓解",
                "detail": (
                    f"在确认业务容量和回滚方案后，可临时隔离或降低 {target_hint} 的流量；"
                    f"当前归因层级为 {location}，系统不会自动执行摘流、重启或迁移。"
                ),
                "risk_level": "R3",
                "execution": "manual_confirmation_required",
                "evidence_refs": refs,
            },
            {
                "recommendation_id": "rec_optimization",
                "category": "optimization",
                "title": optimization[0],
                "detail": optimization[1],
                "risk_level": "R2",
                "execution": "review_before_change",
                "evidence_refs": refs,
            },
            {
                "recommendation_id": "rec_validation",
                "category": "validation",
                "title": "使用同域证据验证优化效果",
                "detail": (
                    "修复后保持相同目标、负载、采样参数和可比较时间窗重新采集，"
                    "对比 P99、资源指标、TopN 与火焰图；覆盖不完整时不得宣称优化有效。"
                ),
                "risk_level": "R1",
                "execution": "recollect_and_compare",
                "evidence_refs": refs,
            },
        ]

    def _append_scope_help_conclusion(
        self,
        diagnosis_id: str,
        query: str,
        ambiguities: list[str],
    ) -> None:
        """没有可靠拓扑时，只给可审核排查命令，不假装已经诊断。"""
        actions = [
            inspect_command_action(
                action_id="act_list_agents", title="列出可用 Agent",
                argv=["micro-drop", "status", "--agents"],
                comment="确认哪些 Agent 在线，以及它们是否具备 sys_metrics/perf_cpu/ebpf_io 等诊断能力。",
                diagnosis_id=diagnosis_id,
            ),
            inspect_command_action(
                action_id="act_parse_intent", title="解析自然语言意图",
                argv=["micro-drop", "parse", query],
                comment="仅解析意图，不创建采集任务；适合人工核对服务名、采集器和安全参数。",
                diagnosis_id=diagnosis_id, confidence_level="中",
            ),
        ]
        self._complete_node(
            diagnosis_id, "generate_actions",
            output_refs=[item["action_id"] for item in actions],
            metrics={"action_count": len(actions)},
        )
        conclusion = {
            "version": 1,
            "generated_at": utcnow().isoformat(),
            "summary": "当前缺少服务实例到 Agent/PID 的映射，无法安全扩散采集范围。",
            "confidence_level": "不可判断",
            "cluster_assessment": {
                "classification": "scope_unresolved",
                "confidence": 0.0,
                "confidence_level": "不可判断",
                "summary": "请先补充服务实例、宿主机、Agent 和 PID 映射。",
                "evidence_refs": [],
                "compared_targets": [],
                "ruled_out": [],
            },
            "root_location": {"type": "unknown", "target_ref": None, "evidence_refs": []},
            "domain_cause": {"type": "unknown", "subtype": "unknown", "evidence_refs": []},
            "findings": [],
            "root_cause_candidates": [],
            "ruled_out": [],
            "knowledge_refs": [],
            "actions": actions,
            "diagnostic_commands": actions,
            "recommendations": [{
                "action": "补充 context.instances 后重新创建诊断会话；AI 不会猜测 PID 或跨服务扩散采集。",
                "risk_level": "R0",
                "execution": "manual_confirmation_required",
            }],
            "limitations": ambiguities or ["service_instance_mapping"],
            "coverage": {"task_count": 0, "evidence_count": 0},
        }
        session = self.store.get_session(diagnosis_id) or {}
        verification = verify_report(conclusion, [], session.get("target_scope", {}), session)
        conclusion["verification"] = verification
        if verification["status"] == "passed":
            self._complete_node(
                diagnosis_id, "verify_report",
                input_refs=[item["action_id"] for item in actions],
                output_refs=["verified_scope_help_report"],
                metrics=verification,
            )
            self._append_conclusion(diagnosis_id, conclusion)
        else:
            self.store.update_pipeline_node(
                diagnosis_id, "verify_report", "FAILED",
                error_code="REPORT_VERIFICATION_FAILED",
                error_message="; ".join(verification["issues"]), metrics=verification,
            )

    def _ensure_insufficient_conclusion(self, diagnosis_id: str, tasks: list[Any]) -> None:
        session = self.store.get_session(diagnosis_id) or {}
        if session.get("conclusion_versions"):
            return
        probes = self.store.list_probes(diagnosis_id)
        missing = []
        if not tasks:
            missing.append("没有可用的已完成采集任务")
        if any(probe["status"] == "UNAVAILABLE" for probe in probes):
            missing.append("目标 Agent 未注册所需采集能力或当前离线")
        if any(probe["status"] == "REJECTED" for probe in probes):
            missing.append("需要审批的深度探针被拒绝")
        stored_evidence = self.store.list_evidence(diagnosis_id)
        if tasks and not any(item["source_type"] == "derived_artifact" for item in stored_evidence):
            missing.append("任务缺少结构化分析产物")
        scope = session.get("target_scope", {})
        assessment = assess_cluster(scope, [])
        finding = cluster_finding(assessment)
        evidence_refs = [item["evidence_id"] for item in stored_evidence]
        actions = [inspect_session_action(diagnosis_id, evidence_refs)]
        target = next(iter(scope.get("instances", [])), None)
        if target and session.get("normalized_intent", {}).get("diagnosis_mode") != "HISTORICAL":
            actions.append(collect_action(
                action_id="act_low_risk_metrics", title="重新采集低风险系统指标",
                collector_type="sys_metrics", target=target, duration_sec=15, sample_rate=11,
                comment="当前结构化证据缺失，先以低风险指标确认数据链路和资源趋势。",
                risk_level="R1", evidence_refs=evidence_refs, confidence_level="低",
            ))
        pipeline = {item["node_name"]: item["status"] for item in self.store.list_pipeline_nodes(diagnosis_id)}
        self.store.update_pipeline_node(
            diagnosis_id, "run_probes", "COMPLETED" if probes else "SKIPPED",
            output_refs=[f"task:{task.id}" for task in tasks],
            metrics={"probe_statuses": {item["step_id"]: item["status"] for item in probes}},
        )
        if pipeline.get("normalize_evidence") == "PENDING":
            self.store.update_pipeline_node(diagnosis_id, "normalize_evidence", "SKIPPED")
        if pipeline.get("analyze_evidence") == "PENDING":
            self.store.update_pipeline_node(diagnosis_id, "analyze_evidence", "SKIPPED")
        self._complete_node(
            diagnosis_id, "assess_cluster", input_refs=evidence_refs,
            output_refs=[finding["finding_id"]], metrics={"classification": "insufficient_evidence"},
        )
        self._complete_node(
            diagnosis_id, "retrieve_knowledge", input_refs=[finding["finding_id"]],
            output_refs=[], metrics={"knowledge_count": 0},
        )
        self._complete_node(
            diagnosis_id, "generate_actions", input_refs=evidence_refs,
            output_refs=[item["action_id"] for item in actions], metrics={"action_count": len(actions)},
        )
        conclusion = {
            "version": 1,
            "generated_at": utcnow().isoformat(),
            "summary": "当前证据不足，不能可靠给出根因候选。",
            "evidence_scope": "reproduction" if session.get("normalized_intent", {}).get("diagnosis_mode") == "REPRODUCTION" else "incident",
            "confidence_level": "不可判断",
            "cluster_assessment": assessment,
            "root_location": assessment["root_location"],
            "domain_cause": assessment["domain_cause"],
            "findings": [finding],
            "root_cause_candidates": [],
            "ruled_out": [],
            "knowledge_refs": [],
            "knowledge_context": [],
            "actions": actions,
            "diagnostic_commands": actions,
            "recommendations": [],
            "limitations": missing or ["缺少能够区分候选假设的独立证据"],
            "coverage": {"task_count": len(tasks), "evidence_count": len(stored_evidence)},
        }
        verification = verify_report(conclusion, stored_evidence, scope, session)
        conclusion["verification"] = verification
        if verification["status"] == "passed":
            self._complete_node(
                diagnosis_id, "verify_report",
                input_refs=evidence_refs + [item["action_id"] for item in actions],
                output_refs=["verified_insufficient_report"], metrics=verification,
            )
            self._append_conclusion(diagnosis_id, conclusion)
        else:
            self.store.update_pipeline_node(
                diagnosis_id, "verify_report", "FAILED",
                error_code="REPORT_VERIFICATION_FAILED",
                error_message="; ".join(verification["issues"]), metrics=verification,
            )

    def _append_conclusion(self, diagnosis_id: str, conclusion: dict[str, Any]) -> None:
        session = self.store.get_session(diagnosis_id)
        if session is None:
            return
        evaluation = self._evaluate_conclusion(session.get("evaluation_oracle", {}), conclusion)
        if evaluation:
            conclusion["evaluation"] = evaluation
        versions = list(session.get("conclusion_versions", []))
        fingerprint = hashlib.sha256(
            json.dumps(conclusion, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        conclusion["integrity_hash"] = f"sha256:{fingerprint}"
        versions.append(conclusion)
        self.store.update_session(diagnosis_id, conclusion_versions=versions)

    @staticmethod
    def _evaluate_conclusion(
        oracle: dict[str, Any],
        conclusion: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Score a finished report against hidden ground truth without model involvement."""

        if not oracle:
            return None
        actual = {
            "instance_id": conclusion.get("root_location", {}).get("target_ref"),
            "location_type": conclusion.get("root_location", {}).get("type"),
            "domain_type": conclusion.get("domain_cause", {}).get("type"),
            "classification": conclusion.get("cluster_assessment", {}).get("classification"),
        }
        pairs = (
            ("instance_id", "expected_instance_id"),
            ("location_type", "expected_location_type"),
            ("domain_type", "expected_domain_type"),
            ("classification", "expected_classification"),
        )
        checks = []
        for dimension, expected_key in pairs:
            expected = oracle.get(expected_key)
            if expected is None:
                continue
            observed = actual[dimension]
            checks.append({
                "dimension": dimension,
                "expected": expected,
                "actual": observed,
                "matched": observed == expected,
            })
        matched_count = sum(1 for item in checks if item["matched"])
        specified_count = len(checks)
        return {
            "case_id": oracle.get("case_id"),
            "specified_count": specified_count,
            "matched_count": matched_count,
            "score_pct": round(matched_count / specified_count * 100, 1) if specified_count else 0.0,
            "exact_match": bool(specified_count and matched_count == specified_count),
            "checks": checks,
            "oracle_isolated": True,
        }

    def _add_task_evidence(self, diagnosis_id: str, task) -> str:
        payload = {
            "task_id": task.id,
            "status": status_value(task.status),
            "status_reason": task.status_reason,
            "collector_type": task.collector_type,
            "agent_id": task.agent_id,
            "target_pid": task.target_pid,
        }
        identity = hashlib.sha256(f"{diagnosis_id}:{task.id}:task".encode()).hexdigest()
        evidence_id = f"ev_{identity[:20]}"
        session = self.store.get_session(diagnosis_id) or {}
        evidence_role = "reproduction" if session.get("normalized_intent", {}).get("diagnosis_mode") == "REPRODUCTION" else "incident"
        evidence_record = {
            "evidence_id": evidence_id,
            "diagnosis_id": diagnosis_id,
            "source_type": "task_event",
            "source_system": "mini_drop",
            "evidence_role": evidence_role,
            "target": {"agent_id": task.agent_id, "pid": task.target_pid},
            "event_time_range": {
                "start": _iso(task.started_at or task.created_at),
                "end": _iso(task.finished_at or utcnow()),
                "clock_skew_estimate_ms": None,
            },
            "ingestion_time": utcnow(),
            "query_or_probe": task.collector_type,
            "derived_artifact_ref": f"task:{task.id}",
            "derivation_version": PLANNER_VERSION,
            "observed_value": payload,
            "baseline_value": {},
            "anomaly_score": {},
            "claim_links": [],
            "data_quality": {"completeness": "high" if status_value(task.status) == "DONE" else "low",
                             "domains": ["task"]},
        }
        evidence_record["integrity_hash"] = evidence_integrity_hash(evidence_record)
        self.store.add_evidence(evidence_record)
        return evidence_id

    def _add_artifact_evidence(
        self,
        diagnosis_id: str,
        task,
        artifact_type: str,
        value: Any,
        artifact: dict[str, Any],
    ) -> str:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
        digest = hashlib.sha256(serialized).hexdigest()
        identity = hashlib.sha256(
            f"{diagnosis_id}:{task.id}:{artifact_type}:{digest}".encode()
        ).hexdigest()
        evidence_id = f"ev_{identity[:20]}"
        session = self.store.get_session(diagnosis_id) or {}
        evidence_role = "reproduction" if session.get("normalized_intent", {}).get("diagnosis_mode") == "REPRODUCTION" else "incident"
        domains = {
            "sys_metrics": ["host", "process", "container"], "top_json": ["process"],
            "ebpf_metrics": ["host", "process"], "memory_json": ["process"],
            "network_metrics": ["host", "dependency"], "database_metrics": ["dependency"],
            "runtime_metrics": ["process", "runtime"],
        }.get(artifact_type, [])
        evidence_record = {
            "evidence_id": evidence_id,
            "diagnosis_id": diagnosis_id,
            "source_type": "derived_artifact",
            "source_system": "mini_drop_analyzer",
            "evidence_role": evidence_role,
            "target": {"agent_id": task.agent_id, "pid": task.target_pid},
            "event_time_range": {
                "start": _iso(task.started_at or task.created_at),
                "end": _iso(task.finished_at or utcnow()),
                "sampling_period_seconds": task.duration_sec,
                "clock_skew_estimate_ms": None,
            },
            "ingestion_time": utcnow(),
            "query_or_probe": task.collector_type,
            "raw_artifact_ref": f"task:{task.id}:artifact:{artifact_type}",
            "derived_artifact_ref": artifact.get("object_key") or artifact.get("local_path"),
            "derivation_version": PLANNER_VERSION,
            "observed_value": _summarize_value(value),
            "baseline_value": {},
            "anomaly_score": {},
            "claim_links": [],
            "data_quality": {**_artifact_quality(value, len(serialized), task.duration_sec), "domains": domains},
        }
        evidence_record["integrity_hash"] = evidence_integrity_hash(evidence_record)
        self.store.add_evidence(evidence_record)
        session = self.store.get_session(diagnosis_id)
        if session is not None:
            usage = dict(session.get("budget_used", {}))
            usage["artifact_size_mb"] = round(sum(
                int(item.get("data_quality", {}).get("size_bytes", 0))
                for item in self.store.list_evidence(diagnosis_id)
            ) / (1024 * 1024), 3)
            self.store.update_session(diagnosis_id, budget_used=usage)
        return evidence_id

    def _structured_artifacts(self, artifacts: list[dict[str, Any]]) -> list[tuple[str, Any, dict[str, Any]]]:
        results = []
        for artifact in artifacts:
            artifact_type = artifact.get("artifact_type", "")
            if artifact_type not in STRUCTURED_ARTIFACT_TYPES:
                continue
            value = self._read_artifact_json(artifact)
            if value is not None:
                results.append((artifact_type, value, artifact))
        return results

    def _read_artifact_json(self, artifact: dict[str, Any]) -> Any | None:
        metadata = artifact.get("metadata", {})
        if "data" in metadata and isinstance(metadata["data"], (dict, list)):
            return metadata["data"]
        try:
            local_path = artifact.get("local_path")
            if local_path:
                root = Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).resolve()
                path = Path(local_path).expanduser().resolve()
                # Agent 的 local_path 属于远端 Worker；Control 上不存在时必须继续
                # 回退 object_key，而不是因 stat() 抛 FileNotFoundError 提前退出。
                if (path == root or root in path.parents) and path.is_file():
                    if path.stat().st_size > 2 * 1024 * 1024:
                        return None
                    return json.loads(path.read_text(encoding="utf-8", errors="strict"))
            object_key = artifact.get("object_key")
            if object_key:
                raw = storage.read_object_bytes(artifact.get("bucket", "mini-drop"), object_key)
                if len(raw) <= 2 * 1024 * 1024:
                    return json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return None

    def _build_topology_snapshot(self, request, intent) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        service_id = intent.target_service
        if service_id:
            nodes[f"service:{service_id}"] = {
                "id": service_id, "type": "Service", "environment": intent.environment,
            }
        for instance in request.context.instances:
            data = instance.model_dump(mode="json")
            nodes[f"service:{instance.service_id}"] = {
                "id": instance.service_id, "type": "Service", "environment": instance.environment,
            }
            nodes[f"instance:{instance.instance_id}"] = {
                "id": instance.instance_id, "type": "ServiceInstance", **data,
            }
            nodes[f"host:{instance.host_id}"] = {"id": instance.host_id, "type": "Host"}
            nodes[f"process:{instance.agent_id}:{instance.pid}"] = {
                "id": f"{instance.agent_id}:{instance.pid}", "type": "Process",
                "agent_id": instance.agent_id, "pid": instance.pid,
            }
            edges.extend([
                {"source": instance.instance_id, "target": instance.host_id, "type": "DEPLOYED_ON", "confidence": "high"},
                {"source": instance.instance_id, "target": f"{instance.agent_id}:{instance.pid}", "type": "RUNS_AS", "confidence": "high"},
            ])
        for dependency in request.context.dependencies:
            nodes.setdefault(
                f"service:{dependency.source_service}",
                {"id": dependency.source_service, "type": "Service", "environment": intent.environment},
            )
            nodes.setdefault(
                f"service:{dependency.target_service}",
                {"id": dependency.target_service, "type": "Service", "environment": intent.environment},
            )
            edges.append({
                "source": dependency.source_service,
                "target": dependency.target_service,
                "type": dependency.relation,
                "effective_from": _iso(dependency.effective_from),
                "effective_to": _iso(dependency.effective_to),
                "confidence": dependency.confidence,
                "discovery_source": dependency.source,
            })
        now = utcnow()
        return {
            "snapshot_id": f"topo_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}",
            "effective_at": intent.time_range.end,
            "generated_at": now,
            "nodes": list(nodes.values()),
            "edges": edges,
            "source_versions": {"request_context": "v1"},
            "confidence_summary": {
                "level": "high" if request.context.instances else "low",
                "source": "request_context",
                "historical_snapshot": True,
            },
        }

    def _build_target_scope(self, request, intent, budget: DiagnosisBudget) -> dict[str, Any]:
        all_instances = [item.model_dump(mode="json") for item in request.context.instances]
        excluded: list[dict[str, Any]] = []
        identities: dict[str, tuple[Any, ...]] = {}
        for item in all_instances:
            identity = (
                item.get("agent_id"), item.get("pid"), item.get("process_start_time"),
                item.get("boot_id"), item.get("container_id"), item.get("cgroup_id"),
            )
            previous = identities.setdefault(item["instance_id"], identity)
            if previous != identity:
                raise ValueError(f"instance_id {item['instance_id']} 指向多个进程身份")

        eligible: list[dict[str, Any]] = []
        for item in all_instances:
            reason = None
            if intent.environment != "unknown" and item["environment"] != intent.environment:
                reason = "environment_mismatch"
            else:
                agent = self.repo.agents.get(item["agent_id"])
                if agent is None:
                    reason = "agent_not_registered"
                elif str(getattr(agent, "hostname", "")) != item["host_id"]:
                    reason = "agent_host_mismatch"
            if reason:
                excluded.append({"instance_id": item["instance_id"], "reason": reason})
            else:
                eligible.append(item)

        target_instances = [item for item in eligible if item["service_id"] == intent.target_service]
        # 目标锚点未建立时禁止向同宿主或依赖扩散。
        if not target_instances:
            return {
                "target_service": intent.target_service,
                "environment": intent.environment,
                "target_anchor": None,
                "instances": [],
                "eligible_targets": [],
                "excluded_targets": excluded,
                "scope_completeness": "unresolved",
                "same_host_instance_ids": [],
                "downstream_service_ids": [],
                "max_topology_hops": budget.max_topology_hops,
            }

        host_ids = {item["host_id"] for item in target_instances}
        same_host = [item for item in eligible if item["host_id"] in host_ids and item not in target_instances]

        adjacency: dict[str, set[str]] = {}
        for edge in request.context.dependencies:
            if edge.relation in {"CALLS", "READS_FROM", "WRITES_TO", "PUBLISHES_TO", "SHARES_DEPENDENCY"}:
                adjacency.setdefault(edge.source_service, set()).add(edge.target_service)
        downstream_services: set[str] = set()
        frontier = {intent.target_service} if intent.target_service else set()
        for _ in range(budget.max_topology_hops):
            next_frontier = {target for source in frontier for target in adjacency.get(source, set())}
            next_frontier -= downstream_services
            downstream_services.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        downstream = [item for item in eligible if item["service_id"] in downstream_services]
        ordered = target_instances + same_host + downstream
        unique = []
        seen = set()
        for item in ordered:
            key = item["instance_id"]
            if key in seen:
                continue
            if len({entry["host_id"] for entry in unique} | {item["host_id"]}) > budget.max_hosts:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= budget.max_service_instances:
                break
        budget_excluded = [item for item in eligible if item not in unique]
        excluded.extend({"instance_id": item["instance_id"], "reason": "budget_excluded"} for item in budget_excluded)
        return {
            "target_service": intent.target_service,
            "environment": intent.environment,
            "target_anchor": dict(target_instances[0]),
            "instances": unique,
            "eligible_targets": unique,
            "excluded_targets": excluded,
            "scope_completeness": "complete" if not excluded else "partial",
            "same_host_instance_ids": [item["instance_id"] for item in same_host],
            "downstream_service_ids": sorted(downstream_services),
            "max_topology_hops": budget.max_topology_hops,
        }

    def _build_hypotheses(self, symptom: str, target_scope: dict[str, Any]) -> list[dict[str, Any]]:
        base = {
            "cpu_saturation": ["CPU_SATURATION", "SELF_CODE_REGRESSION", "SAME_HOST_NOISY_NEIGHBOR"],
            "latency_increase": ["SELF_CODE_REGRESSION", "DOWNSTREAM_LATENCY", "SAME_HOST_NOISY_NEIGHBOR"],
            "io_degradation": ["HOST_DISK_CONTENTION", "SAME_HOST_NOISY_NEIGHBOR", "DOWNSTREAM_LATENCY"],
            "memory_pressure": ["HOST_MEMORY_PRESSURE", "MEMORY_LEAK", "SAME_HOST_NOISY_NEIGHBOR"],
            "noisy_neighbor": ["SAME_HOST_NOISY_NEIGHBOR", "HOST_DISK_CONTENTION", "TRAFFIC_SURGE"],
        }.get(symptom, ["CPU_SATURATION", "DOWNSTREAM_LATENCY", "INSUFFICIENT_EVIDENCE"])
        targets = [item["instance_id"] for item in target_scope.get("instances", [])]
        created_at = utcnow().isoformat()
        return [{
            "hypothesis_id": f"hyp_{index + 1}_{kind.lower()}",
            "type": kind,
            "description": kind.replace("_", " ").title(),
            "affected_targets": targets,
            "status": "UNTESTED",
            "evidence_score": 0,
            "supporting_evidence_refs": [],
            "contradicting_evidence_refs": [],
            "missing_evidence_requirements": [],
            "score_components": {},
            "next_probe_candidates": choose_probe_ids(symptom),
            "history": [{
                "stage": "build_hypotheses",
                "status": "UNTESTED",
                "evidence_score": 0,
                "reason": "根据症状、目标范围和已注册探针生成初始候选，尚未采集区分性证据。",
                "evidence_refs": [],
                "recorded_at": created_at,
            }],
        } for index, kind in enumerate(base)]

    def _update_hypotheses(
        self,
        diagnosis_id: str,
        candidates: list[dict[str, Any]],
        cluster_assessment: dict[str, Any],
    ) -> None:
        session = self.store.get_session(diagnosis_id)
        if session is None:
            return
        graph = dict(session.get("hypothesis_graph", {}))
        hypotheses = list(graph.get("hypotheses", []))
        edges = list(graph.get("edges", []))
        ruled_out = {
            str(item.get("hypothesis", "")).upper(): item
            for item in cluster_assessment.get("ruled_out", [])
        }
        recorded_at = utcnow().isoformat()
        for hypothesis in hypotheses:
            matched = next((c for c in candidates if _candidate_matches_hypothesis(c["candidate_id"], hypothesis["type"])), None)
            contradicted = ruled_out.get(hypothesis["type"])
            if matched:
                hypothesis["status"] = "SUPPORTED"
                hypothesis["supporting_evidence_refs"] = matched["evidence_refs"]
                hypothesis["missing_evidence_requirements"] = matched["missing_evidence"]
                hypothesis["score_components"] = matched["score_components"]
                base_score = {"高": 85, "中": 65, "低": 40}.get(
                    matched.get("confidence_level"), 30,
                )
                hypothesis["evidence_score"] = min(
                    95, base_score + min(10, len(matched["evidence_refs"]) * 2),
                )
                reason = matched["description"]
                refs = matched["evidence_refs"]
                relation = "SUPPORTS"
            elif contradicted:
                hypothesis["status"] = "RULED_OUT"
                hypothesis["evidence_score"] = 10
                hypothesis["contradicting_evidence_refs"] = contradicted.get(
                    "evidence_refs", [],
                )
                hypothesis["next_probe_candidates"] = []
                reason = contradicted.get("reason", "跨节点对比证据不支持该假设。")
                refs = hypothesis["contradicting_evidence_refs"]
                relation = "CONTRADICTS"
            else:
                hypothesis["status"] = "INCONCLUSIVE"
                hypothesis["evidence_score"] = max(
                    20, int(hypothesis.get("evidence_score", 0)),
                )
                reason = "当前证据尚不足以支持或排除该假设。"
                refs = []
                relation = None
            hypothesis.setdefault("history", []).append({
                "stage": "assess_cluster",
                "status": hypothesis["status"],
                "evidence_score": hypothesis["evidence_score"],
                "reason": reason,
                "evidence_refs": refs,
                "recorded_at": recorded_at,
            })
            if relation:
                for evidence_ref in refs:
                    edge = {
                        "source": evidence_ref,
                        "target": hypothesis["hypothesis_id"],
                        "relation": relation,
                        "recorded_at": recorded_at,
                    }
                    if not any(
                        item.get("source") == evidence_ref
                        and item.get("target") == hypothesis["hypothesis_id"]
                        and item.get("relation") == relation
                        for item in edges
                    ):
                        edges.append(edge)
        graph["hypotheses"] = hypotheses
        graph["edges"] = edges
        graph["updated_at"] = recorded_at
        self.store.update_session(diagnosis_id, hypothesis_graph=graph)

    def _find_reusable_tasks(
        self,
        target_scope: dict[str, Any],
        start: datetime,
        end: datetime,
        *,
        require_fresh: bool = True,
    ) -> list[str]:
        """Return one fresh sys-metrics task for every target, or nothing.

        A task merely overlapping a broad diagnosis window is not necessarily
        representative of the current incident.  Reuse is therefore an
        all-or-nothing fast path: every target must have a recent, successful,
        structured sys-metrics artifact.  Partial or stale coverage falls back
        to new controlled probes instead of mixing observation times.
        """
        targets = {(item["agent_id"], item["pid"]) for item in target_scope.get("instances", [])}
        if not targets:
            return []
        try:
            max_age_seconds = max(
                0,
                int(os.getenv("MINI_DROP_DIAGNOSIS_REUSE_MAX_AGE_SECONDS", "120")),
            )
        except ValueError:
            max_age_seconds = 120
        freshness_cutoff = end - timedelta(seconds=max_age_seconds)
        latest_by_target: dict[tuple[str, int], tuple[datetime, str]] = {}
        for task in self.repo.tasks.values():
            target = (task.agent_id, task.target_pid)
            if target not in targets:
                continue
            if task.collector_type != "sys_metrics" or status_value(task.status) != "DONE":
                continue
            task_start = task.started_at or task.created_at
            task_end = task.finished_at or task_start
            if task_start.tzinfo is None:
                task_start = task_start.replace(tzinfo=timezone.utc)
            if task_end.tzinfo is None:
                task_end = task_end.replace(tzinfo=timezone.utc)
            if task_end < start or task_start > end or (require_fresh and task_end < freshness_cutoff):
                continue
            artifacts = self.repo.artifacts.get(task.id, [])
            if not any(item.get("artifact_type") == "sys_metrics" for item in artifacts):
                continue
            current = latest_by_target.get(target)
            if current is None or task_end > current[0]:
                latest_by_target[target] = (task_end, task.id)
        if set(latest_by_target) != targets:
            return []
        return sorted(item[1] for item in latest_by_target.values())

    @staticmethod
    def _effective_time_range(intent, budget: DiagnosisBudget) -> dict[str, Any]:
        if intent.diagnosis_mode == DiagnosisMode.HISTORICAL:
            return intent.time_range.model_dump(mode="json")
        now = utcnow()
        if intent.diagnosis_mode == DiagnosisMode.LIVE:
            return {
                "start": intent.time_range.start.isoformat(),
                "end": (now + timedelta(minutes=budget.max_duration_minutes)).isoformat(),
                "source": "live_collection_window",
            }
        return {
            "start": now.isoformat(),
            "end": (now + timedelta(minutes=budget.max_duration_minutes)).isoformat(),
            "source": "reproduction_window",
        }

    def _transition(
        self,
        diagnosis_id: str,
        status: DiagnosisStatus,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = self.store.get_session(diagnosis_id)
        if current is None or current["status"] == status.value:
            return
        allowed = ALLOWED_DIAGNOSIS_TRANSITIONS.get(current["status"], set())
        if status.value not in allowed:
            raise ValueError(f"非法诊断状态迁移: {current['status']} -> {status.value}")
        self.store.transition(diagnosis_id, status.value, event_type, payload)
        BUS.publish(event_type, {"diagnosis_id": diagnosis_id, "status": status.value, **(payload or {})})

    @staticmethod
    def _budget_for_profile(profile: str) -> DiagnosisBudget:
        if profile == "development":
            return DiagnosisBudget(max_hosts=10, max_service_instances=20, max_parallel_probes=5, max_medium_risk_probes=2)
        if profile == "staging":
            return DiagnosisBudget(max_hosts=8, max_service_instances=15, max_parallel_probes=4, max_medium_risk_probes=2)
        return DiagnosisBudget()

    @classmethod
    def _effective_budget(cls, profile: str, requested: DiagnosisBudget | None) -> DiagnosisBudget:
        policy_cap = cls._budget_for_profile(profile)
        if requested is None:
            return policy_cap
        requested_values = requested.model_dump()
        cap_values = policy_cap.model_dump()
        return DiagnosisBudget(**{
            key: min(int(requested_values[key]), int(cap_values[key]))
            for key in cap_values
        })

    @staticmethod
    def _empty_budget_usage() -> dict[str, int]:
        return {
            "hosts": 0,
            "service_instances": 0,
            "probes": 0,
            "medium_risk_probes": 0,
            "probe_duration_seconds": 0,
            "model_calls": 0,
            "artifact_size_mb": 0,
        }

    @staticmethod
    def _confidence_level(candidate: dict[str, Any]) -> str:
        refs = candidate.get("evidence_refs", [])
        components = candidate.get("score_components", {})
        if (
            len(refs) >= 3
            and not candidate.get("missing_evidence", [])
            and components.get("baseline_support") == "high"
            and components.get("source_independence") == "high"
        ):
            return "高"
        if len(refs) >= 2:
            return "中"
        return "低"

    @staticmethod
    def _enforce_service_scope(service_id: str | None) -> None:
        allowed = {item.strip() for item in os.getenv("MINI_DROP_ALLOWED_SERVICES", "").split(",") if item.strip()}
        if allowed and service_id not in allowed:
            raise PermissionError(f"当前身份无权诊断服务 {service_id}")


def _task_failure_kind(reason: str | None) -> str | None:
    text = str(reason or "").lower()
    if (
        "artifact upload failed" in text
        or ("minio" in text and ("failed" in text or "refused" in text))
        or ("s3" in text and ("failed" in text or "refused" in text))
        or "证据上传失败" in text
        or "对象存储" in text
    ):
        return "artifact_upload_failed"
    return "task_failed" if text else None


def _quality(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def _confidence_label(value: float) -> str:
    if value >= 0.75:
        return "高"
    if value >= 0.5:
        return "中"
    if value > 0:
        return "低"
    return "不可判断"


def _sys_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("summary"), dict):
        return value["summary"]
    return {}


def _normalized_facts(values: dict[str, Any], sys_summary: dict[str, Any]) -> dict[str, Any]:
    """把不同采集器的标量摘要合并为 Analyzer 的稳定事实输入。"""
    facts = dict(sys_summary)
    for artifact_type, raw in values.items():
        if artifact_type == "top_json" or not isinstance(raw, dict):
            continue
        payload = raw.get("summary") if isinstance(raw.get("summary"), dict) else raw
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                facts[key] = value
    return facts


def _artifact_quality(value: Any, size_bytes: int, duration_sec: int) -> dict[str, Any]:
    sample_count = 0
    if isinstance(value, dict):
        sample_count = int(_num(value.get("sample_count")))
    elif isinstance(value, list):
        sample_count = len(value)
    reasons: list[str] = []
    if size_bytes <= 2:
        reasons.append("empty_or_nearly_empty_artifact")
    if duration_sec < 3:
        reasons.append("sampling_window_too_short")
    if sample_count == 0:
        reasons.append("sample_count_unavailable")
    if size_bytes <= 2 or duration_sec < 3:
        completeness = "low"
    elif sample_count >= 5:
        completeness = "high"
    else:
        completeness = "medium"
    return {
        "completeness": completeness,
        "size_bytes": size_bytes,
        "sample_count": sample_count or None,
        "sampling_window_seconds": duration_sec,
        "quality_reasons": reasons,
    }


def _pressure_flags(summary: dict[str, Any], values: dict[str, Any]) -> dict[str, bool]:
    cpu_user = _num(summary.get("avg_cpu_user_pct"))
    cpu_sys = _num(summary.get("avg_cpu_sys_pct"))
    cpu_iowait = _num(summary.get("avg_cpu_iowait_pct"))
    load1m = _num(summary.get("load1m"))
    rss_mb = _num(summary.get("vmrss_mb"))
    fd_count = _num(summary.get("fd_count"))
    threads = _num(summary.get("thread_count"))
    process_cpu_cores = _num(summary.get("process_cpu_core_usage"))
    memory_trend = str(summary.get("vmrss_trend") or summary.get("memory_trend") or "").lower()
    fd_trend = str(summary.get("fd_trend") or "").lower()
    top_items = values.get("top_json") if isinstance(values.get("top_json"), list) else []
    top_percent = _num((top_items[0] or {}).get("percent")) if top_items else 0.0
    return {
        "cpu": process_cpu_cores >= 0.75 or cpu_user + cpu_sys >= 75 or top_percent >= 45,
        "io_wait": cpu_iowait >= 20 or _has_ebpf_latency(values.get("ebpf_metrics")),
        "host_iowait_high": cpu_iowait >= 10,
        "block_latency_high": _has_ebpf_latency(values.get("ebpf_metrics")),
        "process_io_rate_high": False,
        "memory": rss_mb >= 1024 or (memory_trend in {"increasing", "growing"} and rss_mb >= 256),
        "fd": fd_count >= 1000 or (fd_trend == "increasing" and fd_count >= 200),
        "thread": threads >= 512,
        "load": load1m >= 4,
    }


def _has_ebpf_latency(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    summary = value.get("summary")
    if isinstance(summary, dict) and _num(summary.get("p95_us")) >= 10000:
        return True
    hist = value.get("io_latency_us")
    if not isinstance(hist, dict):
        return False
    for bucket, count in hist.items():
        if _num(count) <= 0:
            continue
        if any(token in str(bucket) for token in ("8192", "16384", "32768", "65536")):
            return True
    return False


def _has_self_hotspot(observation: dict[str, Any]) -> bool:
    top = observation.get("top_function", {})
    return bool(top.get("name")) and _num(top.get("percent")) >= 35


def _has_pressure(observation: dict[str, Any]) -> bool:
    pressure = observation.get("pressure", {})
    return any(bool(value) for value in pressure.values())


def _unique_refs(observations) -> list[str]:
    refs: list[str] = []
    for obs in observations:
        for ref in obs.get("evidence_refs", []):
            if ref not in refs:
                refs.append(ref)
    return refs


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _summarize_value(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"item_count": len(value), "top_items": _minimize(value[:5])}
    if isinstance(value, dict):
        result = {"keys": sorted(value.keys())[:30], "summary": _minimize(value.get("summary", value))}
        for field in ("schema_version", "fact_domains"):
            if field in value:
                result[field] = _minimize(value[field])
        return result
    return {"value": str(value)[:500]}


def _minimize(value: Any, depth: int = 0) -> Any:
    """限制进入证据摘要的数据量，并按字段名做基础脱敏。"""
    if depth >= 4:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:50]:
            key_text = str(key)[:128]
            if any(token in key_text.lower() for token in ("token", "secret", "password", "cookie", "authorization")):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _minimize(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_minimize(item, depth + 1) for item in value[:10]]
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:256]


def _candidate_matches_hypothesis(candidate_id: str, hypothesis_type: str) -> bool:
    tokens = {
        "CPU_SATURATION": ("cpu", "hotspot"),
        "SELF_CODE_REGRESSION": ("hotspot", "recursive", "code"),
        "SAME_HOST_NOISY_NEIGHBOR": ("io_wait", "cross_", "cpu"),
        "HOST_DISK_CONTENTION": ("io_wait", "iowait", "disk"),
        "HOST_MEMORY_PRESSURE": ("memory", "swap", "oom"),
        "MEMORY_LEAK": ("memory", "fd_leak"),
        "DOWNSTREAM_LATENCY": ("network", "latency"),
        "TRAFFIC_SURGE": ("network", "load"),
    }.get(hypothesis_type, ())
    return any(token in candidate_id for token in tokens)
