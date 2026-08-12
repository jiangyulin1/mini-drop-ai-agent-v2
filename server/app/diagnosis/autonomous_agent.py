"""Persistent, policy-gated incident investigation and recovery loop.

The model may propose a diagnosis, but it cannot invent an executable action.
Only a registered action explicitly pre-authorized on an AUTHORIZED_AUTONOMY
Case can cross this controller into the Actuation Gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable

from server.app.diagnosis.action_registry import ActionEvaluationRequest, evaluate_action
from server.app.diagnosis.actuation import ActuationError, ActuationGateway, is_executable
from server.app.diagnosis.authorization import AuthorizationDecision, ImpactLevel
from server.app.diagnosis.governance import is_shadow_mode
from server.app.diagnosis.schemas import ApprovalRequest, TERMINAL_DIAGNOSIS_STATUSES


IMPACT_ORDER = {"I0": 0, "I1": 1, "I2": 2, "I3": 3, "I4": 4}
RESTART_CLASSIFICATIONS = {
    "process_oom",
    "runtime_lock_contention",
    "runtime_stall",
    "self_code_or_process_pressure",
}


@dataclass
class AgentCallbacks:
    start_diagnosis: Callable[[dict[str, Any]], dict[str, Any]]
    verify_recovery: Callable[[dict[str, Any], str], dict[str, Any]]
    verify_service_outage: Callable[[dict[str, Any]], dict[str, Any]] | None = None


class AutonomousIncidentAgent:
    """Advance one Case by one durable step on every scheduler tick."""

    def __init__(self, repo, orchestrator, gateway: ActuationGateway, callbacks: AgentCallbacks):
        self.repo = repo
        self.orchestrator = orchestrator
        self.gateway = gateway
        self.callbacks = callbacks
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def step(self, case_id: str, tenant_id: str, principal_id: str = "mini-drop-autonomy") -> dict[str, Any]:
        with self._locks_guard:
            lock = self._locks.setdefault(case_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return {"outcome": "BUSY"}
        try:
            return self._step_locked(case_id, tenant_id, principal_id)
        finally:
            lock.release()

    def _step_locked(self, case_id: str, tenant_id: str, principal_id: str) -> dict[str, Any]:
        case = self.repo.get_incident_case(case_id, tenant_id)
        if case is None:
            return {"outcome": "NOT_FOUND"}
        if case.get("run_mode") != "AUTHORIZED_AUTONOMY":
            return {"outcome": "NOT_AUTONOMOUS"}
        if case.get("state") in {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}:
            return {"outcome": "TERMINAL_OR_PAUSED", "state": case.get("state")}

        policy = _policy(case)
        loop = _loop_state(case)
        if loop.get("phase") == "ESCALATED":
            return {"outcome": "ESCALATED", "reason": loop.get("last_error"), "loop": loop}
        if loop["iteration"] >= policy["max_iterations"]:
            return self._escalate(case, tenant_id, loop, "MAX_ITERATIONS_REACHED", principal_id)
        if loop["actions_executed"] >= policy["max_actions"]:
            return self._escalate(case, tenant_id, loop, "MAX_ACTIONS_REACHED", principal_id)

        diagnosis_id = case.get("diagnosis_session_id")
        if not diagnosis_id:
            loop.update({"phase": "STARTING_DIAGNOSIS", "iteration": loop["iteration"] + 1})
            self._save(case, tenant_id, loop, "agent_diagnosis_requested", principal_id)
            try:
                result = self.callbacks.start_diagnosis(case)
            except Exception as exc:
                return self._escalate(case, tenant_id, loop, f"DIAGNOSIS_START_FAILED:{exc}", principal_id)
            loop.update({
                "phase": "DIAGNOSING",
                "diagnosis_id": (result.get("diagnosis") or {}).get("diagnosis_id"),
            })
            refreshed = self.repo.get_incident_case(case_id, tenant_id) or case
            self._save(refreshed, tenant_id, loop, "agent_diagnosis_started", principal_id)
            return {"outcome": "DIAGNOSIS_STARTED", "loop": loop}

        diagnosis = self.orchestrator.get(diagnosis_id, advance=True)
        if diagnosis is None:
            return self._escalate(case, tenant_id, loop, "DIAGNOSIS_NOT_FOUND", principal_id)
        status = str(diagnosis.get("status") or "")
        loop["diagnosis_id"] = diagnosis_id

        if status == "WAITING_APPROVAL":
            approved = self._approve_registered_probes(diagnosis, policy, principal_id)
            if approved:
                loop["phase"] = "DIAGNOSING"
                self._save(case, tenant_id, loop, "agent_probe_auto_approved", principal_id,
                           {"probe_steps": approved})
                return {"outcome": "PROBES_APPROVED", "steps": approved, "loop": loop}
            loop["phase"] = "WAITING_APPROVAL"
            self._save(case, tenant_id, loop, "agent_waiting_probe_approval", principal_id)
            return {"outcome": "WAITING_APPROVAL", "loop": loop}

        if status not in TERMINAL_DIAGNOSIS_STATUSES:
            loop["phase"] = "DIAGNOSING"
            self._save(case, tenant_id, loop, "agent_diagnosis_progress", principal_id, {"status": status})
            return {"outcome": "DIAGNOSING", "status": status, "loop": loop}

        conclusion = diagnosis.get("latest_conclusion") or {}
        review = conclusion.get("evidence_review") or {}
        if not review.get("quality_gate_passed", True):
            return self._escalate(case, tenant_id, loop, "EVIDENCE_QUALITY_GATE_FAILED", principal_id)
        if review.get("conflicts"):
            return self._escalate(case, tenant_id, loop, "HIGH_QUALITY_EVIDENCE_CONFLICT", principal_id)

        # An executed action must go directly to recovery verification. Do not
        # re-run the pre-action outage override on every scheduler tick.
        if loop.get("phase") in {"ACTION_EXECUTED", "VERIFYING", "MONITORING", "ROLLBACK_DISPATCHING"}:
            return self._verify_or_rollback(case, tenant_id, loop, diagnosis_id, policy, principal_id)

        service_outage_override = False
        if status == "INSUFFICIENT_EVIDENCE" and policy["allow_service_outage_override"]:
            configured = ((case.get("target_scope") or {}).get("recovery_actions") or {}).get("default")
            checks = (((case.get("target_scope") or {}).get("verification") or {}).get("http_checks") or [])
            if isinstance(configured, dict) and configured.get("action_id") and checks:
                try:
                    checker = self.callbacks.verify_service_outage
                    outage = checker(case) if checker else self.callbacks.verify_recovery(case, diagnosis_id)
                except Exception as exc:
                    outage = {"status": "indeterminate", "reason": str(exc)}
                if outage.get("status") == "not_recovered":
                    service_outage_override = True
                    loop["pre_action_verification"] = outage
                    self._save(
                        case, tenant_id, loop, "agent_service_outage_confirmed", principal_id,
                        {"verification": outage},
                    )
        if status not in {"COMPLETED", "PARTIAL_COMPLETED"} and not service_outage_override:
            return self._escalate(case, tenant_id, loop, f"DIAGNOSIS_TERMINATED:{status}", principal_id)

        # Persist the exact candidate and a deterministic execution key before
        # crossing the remote actuation boundary.  If Control stops after the
        # Agent has accepted the task but before ACTION_EXECUTED is saved, the
        # next tick reuses the same key and therefore the same remote task.
        candidate = loop.get("pending_action") or _select_recovery_action(
            case, conclusion, allow_default=service_outage_override,
        )
        if candidate is None:
            return self._escalate(case, tenant_id, loop, "NO_REGISTERED_RECOVERY_ACTION", principal_id)
        candidate = dict(candidate)
        action_id = candidate["action_id"]
        if action_id not in policy["allowed_action_ids"]:
            return self._escalate(case, tenant_id, loop, "ACTION_NOT_PREAUTHORIZED", principal_id)
        if not is_executable(action_id):
            return self._escalate(case, tenant_id, loop, "ACTION_NOT_EXECUTABLE", principal_id)

        evaluation = evaluate_action(action_id, ActionEvaluationRequest(
            tenant_id=tenant_id,
            environment=str(case.get("environment") or "production"),
            target_count=1,
            healthy_replicas_after_action=int(candidate.get("healthy_replicas_after_action", 1)),
            change_freeze=bool(policy.get("change_freeze", False)),
            rollback_ready=True,
            dry_run_passed=True,
            parameters=candidate.get("parameters") or {},
        ))
        if evaluation.decision == AuthorizationDecision.DENIED:
            return self._escalate(
                case, tenant_id, loop,
                "ACTION_POLICY_DENIED:" + ",".join(evaluation.reason_codes), principal_id,
            )
        if IMPACT_ORDER[evaluation.impact_level.value] > IMPACT_ORDER[policy["max_auto_impact"]]:
            return self._escalate(case, tenant_id, loop, "ACTION_IMPACT_EXCEEDS_GRANT", principal_id)

        operation_key = str(candidate.get("operation_key") or _operation_key(
            case["case_id"], loop["iteration"], loop["actions_executed"] + 1, action_id,
        ))
        parameters = dict(candidate.get("parameters") or {})
        parameters["operation_key"] = operation_key
        candidate["parameters"] = parameters
        candidate["operation_key"] = operation_key
        # P7：影子模式只诊断不执行——记录候选动作并进入观察，不跨执行边界。
        if is_shadow_mode(case):
            loop.update({
                "phase": "MONITORING",
                "pending_action": candidate,
                "last_verification": {"status": "shadow_mode_skipped_execution"},
            })
            self._save(case, tenant_id, loop, "agent_shadow_skipped_execution", principal_id, {
                "action_id": action_id,
                "operation_key": operation_key,
            })
            return {"outcome": "SHADOW_SKIPPED_EXECUTION", "loop": loop}
        loop.update({"phase": "ACTION_DISPATCHING", "pending_action": candidate})
        self._save(case, tenant_id, loop, "agent_action_dispatching", principal_id, {
            "action_id": action_id,
            "operation_key": operation_key,
        })
        try:
            dry = self.gateway.dry_run(action_id, parameters)
            self._record_attempt(
                case, tenant_id, dry.get("attempt_id"), action_id, operation_key,
                "dry_run", parameters, dry,
            )
            execution = self.gateway.execute(
                action_id, dry["attempt_id"], environment=str(case.get("environment") or "production"),
            )
            self._record_attempt(
                case, tenant_id, execution.get("attempt_id"), action_id, operation_key,
                "execute", parameters, execution,
            )
        except ActuationError as exc:
            return self._escalate(case, tenant_id, loop, f"ACTION_FAILED:{exc}", principal_id)

        loop.update({
            "phase": "ACTION_EXECUTED",
            "actions_executed": loop["actions_executed"] + 1,
            "active_action": {
                "action_id": action_id,
                "attempt_id": execution["attempt_id"],
                "operation_key": operation_key,
                "parameters": parameters,
                "rollback_action_id": candidate.get("rollback_action_id"),
            },
            "pending_action": {},
            "stable_verifications": 0,
        })
        self._save(case, tenant_id, loop, "agent_action_executed", principal_id, {
            "action_id": action_id,
            "attempt_id": execution["attempt_id"],
        })
        return {"outcome": "ACTION_EXECUTED", "execution": execution, "loop": loop}

    def _approve_registered_probes(self, diagnosis: dict[str, Any], policy: dict[str, Any], principal: str) -> list[str]:
        approved: list[str] = []
        allowed = set(policy["auto_approve_probe_ids"])
        for probe in diagnosis.get("probes") or []:
            if probe.get("status") != "WAITING_APPROVAL" or probe.get("probe_id") not in allowed:
                continue
            self.orchestrator.approve(diagnosis["diagnosis_id"], ApprovalRequest(
                step_id=probe["step_id"],
                decision="approve",
                scope="single_execution",
                approver_id=principal,
            ))
            approved.append(probe["step_id"])
        return approved

    def _verify_or_rollback(self, case, tenant_id, loop, diagnosis_id, policy, principal):
        loop["phase"] = "VERIFYING"
        self._save(case, tenant_id, loop, "agent_verification_started", principal)
        try:
            result = self.callbacks.verify_recovery(case, diagnosis_id)
        except Exception as exc:
            result = {"status": "indeterminate", "reason": str(exc)}
        active = loop.get("active_action") or {}
        if active.get("action_id") and active.get("operation_key"):
            self._record_attempt(
                case, tenant_id, None, active["action_id"], active["operation_key"],
                "verify", active.get("parameters") or {}, result,
            )
        if result.get("status") == "recovered":
            loop["stable_verifications"] += 1
            if loop["stable_verifications"] >= policy["stable_verification_count"]:
                loop["phase"] = "RESOLVED"
                self._save(case, tenant_id, loop, "agent_recovery_verified", principal, result)
                self.repo.transition_incident_case(
                    case["case_id"], tenant_id, actor_id=principal, action="resolve",
                    reason="自主处置通过连续恢复验证",
                )
                return {"outcome": "RESOLVED", "verification": result, "loop": loop}
            loop["phase"] = "MONITORING"
            self._save(case, tenant_id, loop, "agent_recovery_check_passed", principal, result)
            return {"outcome": "MONITORING", "verification": result, "loop": loop}

        active = loop.get("active_action") or {}
        rollback_id = active.get("rollback_action_id")
        if rollback_id and is_executable(rollback_id):
            rollback_key = str(active.get("rollback_operation_key") or _operation_key(
                case["case_id"], loop["iteration"], loop["actions_executed"], rollback_id,
            ))
            active["rollback_operation_key"] = rollback_key
            rollback_parameters = dict(active.get("parameters") or {})
            rollback_parameters["operation_key"] = rollback_key
            loop.update({"phase": "ROLLBACK_DISPATCHING", "active_action": active})
            self._save(case, tenant_id, loop, "agent_rollback_dispatching", principal, {
                "rollback_action_id": rollback_id,
                "operation_key": rollback_key,
            })
            try:
                dry = self.gateway.dry_run(rollback_id, rollback_parameters)
                self._record_attempt(
                    case, tenant_id, dry.get("attempt_id"), rollback_id, rollback_key,
                    "rollback_dry_run", rollback_parameters, dry,
                )
                rollback = self.gateway.execute(
                    rollback_id, dry["attempt_id"], environment=str(case.get("environment") or "production"),
                )
                self._record_attempt(
                    case, tenant_id, rollback.get("attempt_id"), rollback_id, rollback_key,
                    "rollback", rollback_parameters, rollback,
                )
                loop.update({"phase": "ROLLED_BACK", "last_verification": result})
                self._save(case, tenant_id, loop, "agent_action_rolled_back", principal, {
                    "rollback_action_id": rollback_id,
                    "verification": result,
                    "rollback_attempt_id": rollback["attempt_id"],
                })
                current = self.repo.get_incident_case(case["case_id"], tenant_id) or case
                corrected = self.repo.correct_incident_case(
                    case["case_id"], tenant_id, actor_id=principal,
                    changes={"target_scope": current.get("target_scope") or {}},
                    reason="自主修复验证失败并回滚，重新收集证据",
                    expected_row_version=current.get("row_version"),
                )
                loop.update({
                    "phase": "OBSERVING",
                    "diagnosis_id": None,
                    "active_action": {},
                    "stable_verifications": 0,
                })
                if corrected is not None:
                    self._save(corrected, tenant_id, loop, "agent_reinvestigation_scheduled", principal)
                return {"outcome": "ROLLED_BACK", "rollback": rollback, "loop": loop}
            except ActuationError as exc:
                return self._escalate(case, tenant_id, loop, f"ROLLBACK_FAILED:{exc}", principal)
        return self._escalate(case, tenant_id, loop, "RECOVERY_NOT_VERIFIED", principal)

    def _save(self, case, tenant_id, loop, event_type, actor_id, detail=None):
        return self.repo.update_case_agent_loop(
            case["case_id"], tenant_id, actor_id=actor_id, loop=loop,
            event_type=event_type, detail=detail or {},
        )

    def _record_attempt(
        self,
        case,
        tenant_id: str,
        attempt_id: str | None,
        action_id: str,
        operation_key: str,
        phase: str,
        parameters: dict,
        result: dict,
    ) -> None:
        """把动作尝试阶段持久化到 action_attempts；失败不阻断自主循环。"""
        try:
            self.repo.record_action_attempt(
                case["case_id"], tenant_id,
                attempt_id=str(attempt_id or ""),
                action_id=action_id,
                operation_key=operation_key,
                phase=phase,
                parameters=parameters or {},
                result=result or {},
            )
        except Exception:
            # 落库失败不影响已持久化的 agent_loop；审计记录尽力而为。
            pass

    def _escalate(self, case, tenant_id, loop, reason, principal):
        loop.update({"phase": "ESCALATED", "last_error": reason})
        self._save(case, tenant_id, loop, "agent_escalated", principal, {"reason": reason})
        return {"outcome": "ESCALATED", "reason": reason, "loop": loop}


def _policy(case: dict[str, Any]) -> dict[str, Any]:
    raw = ((case.get("target_scope") or {}).get("autonomy_policy") or {})
    max_impact = str(raw.get("max_auto_impact") or "I1")
    if max_impact not in IMPACT_ORDER:
        max_impact = "I1"
    return {
        "max_iterations": max(1, min(int(raw.get("max_iterations", 8)), 20)),
        "max_actions": max(1, min(int(raw.get("max_actions", 3)), 10)),
        "stable_verification_count": max(1, min(int(raw.get("stable_verification_count", 2)), 5)),
        "max_auto_impact": max_impact,
        "allowed_action_ids": list(dict.fromkeys(raw.get("allowed_action_ids") or [])),
        "auto_approve_probe_ids": list(dict.fromkeys(raw.get("auto_approve_probe_ids") or [])),
        "change_freeze": bool(raw.get("change_freeze", False)),
        "allow_service_outage_override": bool(raw.get("allow_service_outage_override", False)),
    }


def _loop_state(case: dict[str, Any]) -> dict[str, Any]:
    recovery = case.get("recovery") or ((case.get("summary") or {}).get("recovery") or {})
    saved = recovery.get("agent_loop") or {}
    return {
        "schema_version": "autonomous-agent-loop.v1",
        "phase": str(saved.get("phase") or "OBSERVING"),
        "iteration": int(saved.get("iteration") or 0),
        "actions_executed": int(saved.get("actions_executed") or 0),
        "stable_verifications": int(saved.get("stable_verifications") or 0),
        "diagnosis_id": saved.get("diagnosis_id"),
        "active_action": saved.get("active_action") or {},
        "pending_action": saved.get("pending_action") or {},
        "pre_action_verification": saved.get("pre_action_verification"),
        "last_verification": saved.get("last_verification"),
        "last_error": saved.get("last_error"),
    }


def _operation_key(case_id: str, iteration: int, action_no: int, action_id: str) -> str:
    """Stable key for one logical action across Control process restarts."""
    return f"{case_id}:{iteration}:{action_no}:{action_id}"


def _select_recovery_action(
    case: dict[str, Any],
    conclusion: dict[str, Any],
    *,
    allow_default: bool = False,
) -> dict[str, Any] | None:
    scope = case.get("target_scope") or {}
    classification = str((conclusion.get("cluster_assessment") or {}).get("classification") or "")
    configured = scope.get("recovery_actions") or {}
    candidate = configured.get(classification)
    if candidate is None:
        default = configured.get("default")
        applicable = set(default.get("applicable_classifications") or []) if isinstance(default, dict) else set()
        if allow_default or classification in applicable:
            candidate = default
    if isinstance(candidate, dict) and candidate.get("action_id"):
        return dict(candidate)
    orchestration = scope.get("orchestration") or {}
    swarm_service = orchestration.get("swarm_service")
    restartable = set(orchestration.get("restartable_classifications") or RESTART_CLASSIFICATIONS)
    if classification in restartable and swarm_service:
        return {
            "action_id": "swarm.restart-stateless-service",
            "parameters": {
                "service_name": swarm_service,
                **({"manager_agent_id": orchestration.get("manager_agent_id")}
                   if orchestration.get("manager_agent_id") else {}),
            },
            "healthy_replicas_after_action": int(orchestration.get("replicas", 1) or 1),
            "rollback_action_id": "swarm.rollback-service",
        }
    return None
