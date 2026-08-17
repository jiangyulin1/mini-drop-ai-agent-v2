"""Controlled actuation, recovery-plan, and autonomous-loop HTTP endpoints."""

from __future__ import annotations

from server.app.routes.agents_process import scan_agent_processes
from server.app.routes.plans_control import start_case_diagnosis
from server.app.routes.fanout import verify_case_recovery

import os
import secrets
import time
from datetime import timedelta
from pathlib import Path as _Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.app import storage as store
from server.app.artifact_service import extract_artifact_json as _extract_artifact_json
from server.app.artifact_service import read_artifact_bytes
from server.app.case_collaboration import (
    CreateRecoveryPlanRequest,
    RecoveryPlanDecisionRequest,
    RecoveryPlanExecuteRequest,
    StartCaseDiagnosisRequest,
)
from server.app.diagnosis.action_registry import (
    ActionEvaluationRequest,
    DEFAULT_ACTION_REGISTRY,
    evaluate_action,
)
from server.app.diagnosis.actuation import (
    ActuationError,
    ActuationGateway,
    enforce_runtime_execution_policy,
    is_executable,
)
from server.app.diagnosis.authorization import AuthorizationDecision
from server.app.diagnosis.approval_binding import (
    seal_approval_binding,
    verify_approval_binding,
)
from server.app.diagnosis.v6_policy import stable_hash
from server.app.diagnosis.autonomous_agent import AgentCallbacks, AutonomousIncidentAgent
from server.app.diagnosis.case_supervisor import CaseSupervisor
from server.app.diagnosis.distributed_actuation import DistributedActuationGateway
from server.app.diagnosis.plan_driver import PlanDriver
from server.app.diagnosis.recovery_verifier import RecoveryCheckError, run_http_checks
from server.app.diagnosis.verification_contract import (
    build_verification_contract,
    evaluate_verification,
)
from server.app.flamegraph_parser import extract_top_functions_from_svg
from server.app.http.auth import (
    request_principal as _request_principal,
    request_tenant as _request_tenant,
    require_role as _require_role,
)
from server.app.logging_utils import log_event
from server.app.runtime_services import (
    case_supervision_repository,
    diagnosis_orchestrator,
    fanout_service,
    investigation_plan_service,
    repo,
    target_resolver,
)
from server.app.schemas import APIResponse
from server.app.state_machine import now_utc


router = APIRouter()

# ── 受控修复执行（Actuation Gateway 首个实例） ────────────────────


LOCAL_ACTUATION_GATEWAY = ActuationGateway(
    audit_callback=lambda detail: repo.record_audit(
        event_type=detail.pop("event_type", "ACTION_AUDIT"),
        message=detail.pop("message", ""),
        metadata=detail,
    ),
)
ACTUATION_GATEWAY = DistributedActuationGateway(
    repo,
    LOCAL_ACTUATION_GATEWAY,
    audit_callback=lambda detail: repo.record_audit(
        event_type=detail.pop("event_type", "ACTION_AUDIT"),
        message=detail.pop("message", ""),
        metadata=detail,
    ),
)


def _internal_operator_request(path: str) -> Request:
    request = Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 8191),
    })
    request.state.principal_id = "mini-drop-autonomy"
    request.state.principal_roles = {"operator"}
    request.state.request_id = f"autonomy-{int(time.time() * 1000)}"
    request.state.traceparent = ""
    return request


def _autonomy_start_diagnosis(case: dict[str, Any]) -> dict[str, Any]:
    current = repo.get_incident_case(case["case_id"], _request_tenant())
    if current is None:
        raise ValueError("CASE_NOT_FOUND")
    response = start_case_diagnosis(
        case["case_id"],
        StartCaseDiagnosisRequest(
            budget_profile="production_safe",
            analysis_strategy="CONSTRAINED_HYBRID",
            expected_row_version=current["row_version"],
        ),
        _internal_operator_request(f"/api/v1/cases/{case['case_id']}/diagnoses"),
    )
    return response.data


def _autonomy_verify_recovery(case: dict[str, Any], diagnosis_id: str) -> dict[str, Any]:
    current = repo.get_incident_case(case["case_id"], _request_tenant()) or case
    scope = current.get("target_scope") or {}
    swarm_service = (scope.get("orchestration") or {}).get("swarm_service")
    if swarm_service:
        for instance in scope.get("instances") or []:
            try:
                scan = scan_agent_processes(
                    instance["agent_id"],
                    {"query": swarm_service, "timeout_sec": 20, "max_results": 20},
                    _internal_operator_request(f"/api/agents/{instance['agent_id']}/processes/scan"),
                    repo,
                ).data
            except Exception:
                continue
            matches = [
                item for item in scan.get("processes", [])
                if item.get("container_service") == swarm_service
            ]
            if len(matches) != 1:
                continue
            replacement = matches[0]
            if int(replacement["pid"]) == int(instance["pid"]):
                continue
            repo.update_case_instance_pid(
                case["case_id"], _request_tenant(), actor_id="mini-drop-autonomy",
                agent_id=instance["agent_id"], previous_pid=int(instance["pid"]),
                new_pid=int(replacement["pid"]), container_id=replacement.get("container_id"),
            )
    response = verify_case_recovery(
        case["case_id"],
        {"diagnosis_id": diagnosis_id},
        _internal_operator_request(f"/api/v1/cases/{case['case_id']}/verification"),
    )
    metric_result = response.data
    refreshed = repo.get_incident_case(case["case_id"], _request_tenant()) or current
    checks = ((refreshed.get("target_scope") or {}).get("verification") or {}).get("http_checks") or []
    if not checks:
        return metric_result
    allowed_hosts = {
        item.strip()
        for item in os.getenv("MINI_DROP_AUTONOMY_HTTP_HOSTS", "").split(",")
        if item.strip()
    }
    try:
        service_result = run_http_checks(checks, allowed_hosts=allowed_hosts)
    except RecoveryCheckError as exc:
        return {"status": "indeterminate", "reason": str(exc), "metrics": metric_result}
    if service_result["status"] != "recovered":
        result = {**service_result, "metrics": metric_result}
    elif metric_result.get("status") == "degraded":
        result = {"status": "degraded", "reason": "服务检查通过，但资源指标出现退化", "service": service_result, "metrics": metric_result}
    else:
        result = {"status": "recovered", "reason": "服务检查和资源回归检查通过", "service": service_result, "metrics": metric_result}
    # P4：每个 Case 的 VerificationContract 评估（业务目标 + 保护指标）。
    try:
        contract = build_verification_contract(case["case_id"], refreshed.get("target_scope") or {})
        check_metrics: dict[str, Any] = dict(metric_result or {})
        if isinstance(service_result.get("checks"), list):
            for item in service_result["checks"]:
                url = str(item.get("url") or "")
                if url and isinstance(item.get("status"), int):
                    check_metrics[f"http:{url}"] = item["status"]
        evaluation = evaluate_verification(contract, check_metrics)
        result["verification_contract"] = {
            "contract": contract.to_dict(),
            "evaluation": evaluation,
        }
        if evaluation["recovered"]:
            result["status"] = "recovered"
        elif evaluation["status"] == "MITIGATED" and result.get("status") != "recovered":
            result["status"] = "mitigated"
    except Exception:
        pass
    return result


def _autonomy_verify_service_outage(case: dict[str, Any]) -> dict[str, Any]:
    """Run only explicitly configured service checks before a recovery action."""
    checks = ((((case.get("target_scope") or {}).get("verification") or {}).get("http_checks")) or [])
    if not checks:
        return {"status": "indeterminate", "reason": "未配置服务级检查"}
    allowed_hosts = {
        item.strip()
        for item in os.getenv("MINI_DROP_AUTONOMY_HTTP_HOSTS", "").split(",")
        if item.strip()
    }
    try:
        return run_http_checks(checks, allowed_hosts=allowed_hosts)
    except RecoveryCheckError as exc:
        return {"status": "indeterminate", "reason": str(exc)}


AUTONOMOUS_AGENT = AutonomousIncidentAgent(
    repo,
    diagnosis_orchestrator,
    ACTUATION_GATEWAY,
    AgentCallbacks(
        start_diagnosis=_autonomy_start_diagnosis,
        verify_recovery=_autonomy_verify_recovery,
        verify_service_outage=_autonomy_verify_service_outage,
    ),
)
CASE_SUPERVISOR = CaseSupervisor(
    case_supervision_repository,
    AUTONOMOUS_AGENT,
    diagnosis_orchestrator,
    lease_ttl_seconds=max(10, min(int(os.getenv("MINI_DROP_CASE_LEASE_TTL_SECONDS", "120")), 600)),
)
PLAN_DRIVER = PlanDriver(
    repo,
    investigation_plan_service,
    fanout_service,
    target_resolver,
)


@router.post("/api/v1/cases/{case_id}/agent/step")
def advance_autonomous_case(case_id: str, request: Request) -> APIResponse:
    """Manually trigger one idempotent autonomous-loop step for inspection."""
    _require_role(request, "operator")
    result = AUTONOMOUS_AGENT.step(
        case_id,
        _request_tenant(),
        principal_id=_request_principal(request),
    )
    if result.get("outcome") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=result)


@router.post("/api/v1/cases/{case_id}/agent/plan-driver")
def advance_case_plan_driver(case_id: str, request: Request) -> APIResponse:
    """E4：手动触发一次 PlanDriver 调度（自动调度 READ_LOW 步骤 / Task 唤醒）。"""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data=PLAN_DRIVER.dispatch_case_ready_steps(case_id, tenant_id))


def _action_evaluation_allows(action_id: str, request: Request, payload: dict[str, Any]) -> None:
    """执行前必须通过确定性策略评估，不允许 DENIED。

    人工显式调用 execute 本身即满足 USER_APPROVAL / CHANGE_APPROVAL；
    但策略硬拒绝（环境不允许、目标超限、冗余不足、未注册）不可被绕过。
    """
    evaluation = evaluate_action(action_id, ActionEvaluationRequest(
        tenant_id=payload.get("tenant_id", _request_tenant()),
        environment=payload.get("environment", "production"),
        target_count=payload.get("target_count", 1),
        healthy_replicas_after_action=payload.get("healthy_replicas_after_action", 1),
        change_freeze=bool(payload.get("change_freeze", False)),
        rollback_ready=bool(payload.get("rollback_ready", True)),
        dry_run_passed=bool(payload.get("dry_run_passed", True)),
        parameters=payload.get("parameters", {}) or {},
    ))
    if evaluation.decision == AuthorizationDecision.DENIED:
        raise HTTPException(status_code=403, detail=f"ACTION_DENIED: {','.join(evaluation.reason_codes)}")


def _case_recovery_plan_or_404(case_id: str, tenant_id: str, plan_id: str) -> dict[str, Any]:
    plan = repo.get_case_recovery_plan(case_id, tenant_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="恢复方案不存在")
    return plan


@router.get("/api/v1/cases/{case_id}/recovery-plans")
def list_case_recovery_plans(case_id: str, request: Request) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    if repo.get_incident_case(case_id, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    return APIResponse(data={
        "items": repo.list_case_recovery_plans(case_id, tenant_id),
    })


@router.post("/api/v1/cases/{case_id}/recovery-plans")
def create_case_recovery_plan(
    case_id: str,
    payload: CreateRecoveryPlanRequest,
    request: Request,
) -> APIResponse:
    """Create a durable, approval-gated recovery plan for an executable action."""
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    definition = DEFAULT_ACTION_REGISTRY.get(payload.action_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="ACTION_NOT_REGISTERED")
    if definition.implementation_status != "executable" or not is_executable(payload.action_id):
        raise HTTPException(status_code=409, detail="ACTION_POLICY_ONLY_NOT_EXECUTABLE")
    policy = evaluate_action(payload.action_id, ActionEvaluationRequest(
        tenant_id=tenant_id,
        environment=case["environment"],
        target_count=1,
        healthy_replicas_after_action=1,
        rollback_ready=bool(definition.rollback_action_id),
        dry_run_passed=False,
        parameters=payload.parameters,
    ))
    if policy.decision == AuthorizationDecision.DENIED:
        raise HTTPException(status_code=403, detail=f"ACTION_DENIED:{','.join(policy.reason_codes)}")
    try:
        plan = repo.create_case_recovery_plan(
            case_id,
            tenant_id,
            action_id=payload.action_id,
            parameters=payload.parameters,
            value_after_fix=payload.value_after_fix,
            verification_method=payload.verification_method,
            policy=policy.model_dump(mode="json"),
            created_by=_request_principal(request),
            expected_case_version=payload.expected_case_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=plan)


@router.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/dry-run")
def dry_run_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["status"] != "PROPOSED":
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_PROPOSED")
    try:
        dry = ACTUATION_GATEWAY.dry_run(plan["action_id"], plan["parameters"])
        definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
        policy = evaluate_action(plan["action_id"], ActionEvaluationRequest(
            tenant_id=tenant_id,
            environment=case["environment"],
            target_count=1,
            healthy_replicas_after_action=1,
            rollback_ready=bool(definition and definition.rollback_action_id),
            dry_run_passed=True,
            parameters=plan["parameters"],
        ))
        if policy.decision == AuthorizationDecision.DENIED:
            raise ActuationError(f"ACTION_DENIED:{','.join(policy.reason_codes)}")
        next_status = (
            "DRY_RUN_COMPLETED"
            if dry.get("dry_run", {}).get("candidate_count", 0) else "DRY_RUN_EMPTY"
        )
        policy_json = policy.model_dump(mode="json")
        if next_status == "DRY_RUN_COMPLETED":
            policy_json["approval_binding"] = seal_approval_binding(
                operation=plan["action_id"],
                normalized_arguments=plan["parameters"],
                target_resource_incarnation=stable_hash({
                    "case_target_scope": case.get("target_scope") or {},
                    "dry_run_items": (dry.get("dry_run") or {}).get("items") or [],
                }),
                risk=str(policy.impact_level.value),
                scope_revision=int(case.get("scope_revision") or 1),
                control_revision=int(case.get("control_revision") or 1),
                execution_epoch=str(dry["attempt_id"]),
                expires_at=(now_utc() + timedelta(minutes=15)).isoformat(),
                nonce=secrets.token_urlsafe(24),
                approver_identity=_request_principal(request),
            )
        updated = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status=next_status,
            actor_id=_request_principal(request),
            expected_plan_version=payload.expected_plan_version,
            updates={
                "policy_json": policy_json,
                "dry_run_attempt_id": dry["attempt_id"],
                "dry_run_json": dry["dry_run"],
            },
        )
    except (ActuationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


@router.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/decision")
def decide_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanDecisionRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["status"] != "DRY_RUN_COMPLETED":
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_READY_FOR_DECISION")
    now = now_utc()
    if payload.decision == "approve":
        case = repo.get_incident_case(case_id, tenant_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Case 不存在")
        binding = (plan.get("policy") or {}).get("approval_binding")
        approval_error = verify_approval_binding(
            binding,
            supplied_digest=str(payload.approval_digest or ""),
            approver_identity=_request_principal(request),
            scope_revision=int(case.get("scope_revision") or 1),
            control_revision=int(case.get("control_revision") or 1),
            execution_epoch=str(plan.get("dry_run_attempt_id") or ""),
            now=now,
        )
        if approval_error:
            raise HTTPException(status_code=409, detail=approval_error)
    updates = (
        {"approved_by": _request_principal(request), "approved_at": now}
        if payload.decision == "approve"
        else {"rejection_reason": payload.reason}
    )
    try:
        updated = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status="APPROVED" if payload.decision == "approve" else "REJECTED",
            actor_id=_request_principal(request),
            expected_plan_version=payload.expected_plan_version,
            updates=updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


@router.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/execute")
def execute_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case 不存在")
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["status"] not in {"APPROVED", "EXECUTING"} or not plan.get("approved_by"):
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_APPROVED")
    approval_binding = (plan.get("policy") or {}).get("approval_binding")
    if plan["status"] == "APPROVED":
        approval_error = verify_approval_binding(
            approval_binding,
            supplied_digest=str((approval_binding or {}).get("proposal_digest") or ""),
            approver_identity=str(plan.get("approved_by") or ""),
            scope_revision=int(case.get("scope_revision") or 1),
            control_revision=int(case.get("control_revision") or 1),
            execution_epoch=str(plan.get("dry_run_attempt_id") or ""),
        )
        if approval_error:
            raise HTTPException(status_code=409, detail=approval_error)
    definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
    try:
        enforce_runtime_execution_policy(
            payload.runtime_policy,
            action_id=plan["action_id"],
            risk_level="R1" if getattr(getattr(definition, "base_impact_level", None), "value", "I2") == "I1" else "R2",
        )
    except ActuationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    policy = evaluate_action(plan["action_id"], ActionEvaluationRequest(
        tenant_id=tenant_id,
        environment=case["environment"],
        target_count=1,
        healthy_replicas_after_action=1,
        rollback_ready=bool(definition and definition.rollback_action_id),
        dry_run_passed=True,
        parameters=plan["parameters"],
    ))
    if policy.decision == AuthorizationDecision.DENIED or not policy.executable:
        raise HTTPException(status_code=403, detail=f"ACTION_DENIED:{','.join(policy.reason_codes)}")
    try:
        if plan["status"] == "APPROVED":
            consumed_binding = {
                **(approval_binding or {}),
                "consumed_at": now_utc().isoformat(),
            }
            consumed_policy = {
                **(plan.get("policy") or {}),
                "approval_binding": consumed_binding,
            }
            plan = repo.transition_case_recovery_plan(
                case_id, tenant_id, plan_id,
                to_status="EXECUTING",
                actor_id=_request_principal(request),
                expected_plan_version=payload.expected_plan_version,
                updates={"policy_json": consumed_policy},
            )
            if plan is None:
                raise ValueError("RECOVERY_PLAN_NOT_FOUND")
        elif plan["row_version"] != payload.expected_plan_version:
            raise ValueError("RECOVERY_PLAN_VERSION_CONFLICT")

        dry_items = (plan.get("dry_run") or {}).get("items") or []
        inferred = _infer_recovery_execution(plan)
        if dry_items and len(inferred) == len(dry_items):
            execution = {
                "attempt_id": str(plan["dry_run_attempt_id"]),
                "action_id": plan["action_id"],
                "stage": "COMPLETED",
                "executed": inferred,
                "reconciled_from_postconditions": True,
            }
        else:
            if ACTUATION_GATEWAY.get_attempt(str(plan["dry_run_attempt_id"])) is None:
                ACTUATION_GATEWAY.restore_dry_run_attempt(
                    attempt_id=str(plan["dry_run_attempt_id"]),
                    action_id=plan["action_id"],
                    items=dry_items,
                    parameters=plan["parameters"],
                )
            execution = ACTUATION_GATEWAY.execute(
                plan["action_id"],
                str(plan["dry_run_attempt_id"]),
                environment=case["environment"],
            )
            combined = {
                str(item.get("task_id") or item.get("source")): item
                for item in [*inferred, *(execution.get("executed") or [])]
            }
            execution["executed"] = list(combined.values())
        updated = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status="EXECUTED",
            actor_id=_request_principal(request),
            expected_plan_version=plan["row_version"],
            updates={"execution_json": execution, "policy_json": policy.model_dump(mode="json")},
        )
    except ActuationError as exc:
        current = repo.get_case_recovery_plan(case_id, tenant_id, plan_id)
        if current and current["status"] == "EXECUTING":
            attempt = ACTUATION_GATEWAY.get_attempt(str(current.get("dry_run_attempt_id")))
            failure = {
                "attempt_id": current.get("dry_run_attempt_id"),
                "stage": "FAILED",
                "executed": list(attempt.executed_items) if attempt else [],
                "error": str(exc),
            }
            try:
                repo.transition_case_recovery_plan(
                    case_id, tenant_id, plan_id,
                    to_status="FAILED",
                    actor_id=_request_principal(request),
                    expected_plan_version=current["row_version"],
                    updates={"execution_json": failure},
                )
            except ValueError:
                pass
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


def _infer_recovery_execution(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover an execution journal from deterministic filesystem postconditions."""
    dry_items = (plan.get("dry_run") or {}).get("items") or []
    if plan["action_id"] == "mini-drop.cleanup-expired-cache":
        destination_root = _Path(
            os.getenv("MINI_DROP_QUARANTINE_ROOT", "/tmp/mini-drop-quarantine"),
        ).expanduser().resolve()
        destination_field = "quarantine_path"
    elif plan["action_id"] == "mini-drop.restore-cache-quarantine":
        destination_root = _Path(
            os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop"),
        ).expanduser().resolve()
        destination_field = "restored_path"
    else:
        return []
    if not destination_root.is_dir():
        return []
    destinations = [item for item in destination_root.iterdir() if item.is_dir()]
    inferred: list[dict[str, Any]] = []
    for item in dry_items:
        source_value = str(item.get("path") or "").strip()
        if not source_value:
            continue
        source = _Path(source_value)
        task_id = str(item.get("task_id") or source.name)
        if source.exists():
            continue
        matches = [
            candidate for candidate in destinations
            if candidate.name == task_id or candidate.name.startswith(f"{task_id}-")
        ]
        if not matches:
            continue
        inferred.append({
            "task_id": task_id,
            "source": str(source),
            destination_field: str(sorted(matches)[-1]),
            "size_bytes": item.get("size_bytes", 0),
            "reconciled": True,
        })
    return inferred


def _rollback_case_recovery_plan(
    case_id: str,
    tenant_id: str,
    plan: dict[str, Any],
    *,
    actor_id: str,
) -> dict[str, Any]:
    definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
    rollback_id = definition.rollback_action_id if definition else None
    if not rollback_id or not is_executable(rollback_id):
        raise ActuationError(f"动作 {plan['action_id']} 没有可执行的回滚动作")
    case = repo.get_incident_case(case_id, tenant_id)
    if case is None:
        raise ActuationError("Case 不存在")
    dry = ACTUATION_GATEWAY.dry_run(rollback_id, plan.get("parameters") or {})
    if dry.get("dry_run", {}).get("candidate_count", 0):
        result = ACTUATION_GATEWAY.execute(
            rollback_id, dry["attempt_id"], environment=case["environment"],
        )
    else:
        if (plan.get("execution") or {}).get("executed"):
            raise ActuationError("ROLLBACK_TARGET_MISSING：已执行动作的回滚目标不存在")
        result = {"attempt_id": dry["attempt_id"], "stage": "NOTHING_TO_ROLLBACK", "executed": []}
    updated = repo.transition_case_recovery_plan(
        case_id, tenant_id, plan["recovery_plan_id"],
        to_status="ROLLED_BACK",
        actor_id=actor_id,
        expected_plan_version=plan["row_version"],
        updates={"rollback_json": result},
    )
    if updated is None:
        raise ActuationError("恢复方案不存在")
    return updated


@router.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/rollback")
def rollback_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["row_version"] != payload.expected_plan_version:
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_VERSION_CONFLICT")
    if plan["status"] not in {"EXECUTED", "VERIFICATION_FAILED", "FAILED"}:
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_ROLLBACKABLE")
    try:
        updated = _rollback_case_recovery_plan(
            case_id, tenant_id, plan, actor_id=_request_principal(request),
        )
    except (ActuationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=updated)


def _verify_local_recovery_postconditions(plan: dict[str, Any]) -> dict[str, Any]:
    """Verify executable maintenance actions without trusting client assertions."""
    executed = (plan.get("execution") or {}).get("executed") or []
    if not executed:
        return {"status": "indeterminate", "reason": "执行结果为空", "checks": []}
    checks: list[dict[str, Any]] = []
    if plan["action_id"] == "mini-drop.cleanup-expired-cache":
        for item in executed:
            source_value = str(item.get("source") or "").strip()
            quarantine_value = str(item.get("quarantine_path") or "").strip()
            source_absent = bool(source_value) and not _Path(source_value).exists()
            quarantine_present = bool(quarantine_value) and _Path(quarantine_value).is_dir()
            checks.append({
                "task_id": item.get("task_id"),
                "source_absent": source_absent,
                "quarantine_present": quarantine_present,
                "passed": source_absent and quarantine_present,
            })
    elif plan["action_id"] == "mini-drop.restore-cache-quarantine":
        for item in executed:
            source_value = str(item.get("source") or "").strip()
            restored_value = str(item.get("restored_path") or "").strip()
            source_absent = bool(source_value) and not _Path(source_value).exists()
            restored_present = bool(restored_value) and _Path(restored_value).is_dir()
            checks.append({
                "task_id": item.get("task_id"),
                "source_absent": source_absent,
                "restored_present": restored_present,
                "passed": source_absent and restored_present,
            })
    else:
        return {
            "status": "indeterminate",
            "reason": "该动作尚无注册的服务端验证器",
            "checks": [],
        }
    passed = bool(checks) and all(item["passed"] for item in checks)
    return {
        "status": "recovered" if passed else "not_recovered",
        "reason": f"服务端校验 {len(checks)} 项动作后置条件",
        "checks": checks,
    }


@router.post("/api/v1/cases/{case_id}/recovery-plans/{plan_id}/verify")
def verify_case_recovery_plan(
    case_id: str,
    plan_id: str,
    payload: RecoveryPlanExecuteRequest,
    request: Request,
) -> APIResponse:
    _require_role(request, "operator")
    tenant_id = _request_tenant()
    plan = _case_recovery_plan_or_404(case_id, tenant_id, plan_id)
    if plan["row_version"] != payload.expected_plan_version:
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_VERSION_CONFLICT")
    if plan["status"] != "EXECUTED":
        raise HTTPException(status_code=409, detail="RECOVERY_PLAN_NOT_EXECUTED")
    judgment = _verify_local_recovery_postconditions(plan)
    try:
        transitioned = repo.transition_case_recovery_plan(
            case_id, tenant_id, plan_id,
            to_status="VERIFIED" if judgment["status"] == "recovered" else "VERIFICATION_FAILED",
            actor_id=_request_principal(request),
            expected_plan_version=plan["row_version"],
            updates={"verification_json": judgment},
        )
        final_plan = transitioned
        if judgment["status"] != "recovered":
            definition = DEFAULT_ACTION_REGISTRY.get(plan["action_id"])
            if definition and definition.rollback_action_id and is_executable(definition.rollback_action_id):
                final_plan = _rollback_case_recovery_plan(
                    case_id, tenant_id, transitioned,
                    actor_id=_request_principal(request),
                )
    except (ActuationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={"judgment": judgment, "recovery_plan": final_plan})


@router.post("/api/v1/actions/{action_id}/dry-run")
def dry_run_registered_action(
    action_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """对注册动作执行只读预演，返回将影响的清单（不执行任何变更）。"""
    _require_role(request, "operator")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    if tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    try:
        result = ACTUATION_GATEWAY.dry_run(
            action_id,
            payload.get("parameters") or {},
        )
    except ActuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={
        **result,
        "tenant_id": tenant_id,
        "principal_id": _request_principal(request),
    })


@router.post("/api/v1/actions/{action_id}/execute")
def execute_registered_action(
    action_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """执行已通过 dry-run 与策略评估的修复动作（人工显式触发 = 批准）。"""
    _require_role(request, "operator")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    if tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    if not payload.get("dry_run_attempt_id"):
        raise HTTPException(status_code=400, detail="dry_run_attempt_id 必填：必须先 dry-run 再执行")
    definition = DEFAULT_ACTION_REGISTRY.get(action_id)
    try:
        enforce_runtime_execution_policy(
            payload.get("runtime_policy"),
            action_id=action_id,
            risk_level="R1" if getattr(getattr(definition, "base_impact_level", None), "value", "I2") == "I1" else "R2",
        )
    except ActuationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    _action_evaluation_allows(action_id, request, payload)
    try:
        result = ACTUATION_GATEWAY.execute(
            action_id,
            str(payload["dry_run_attempt_id"]),
            environment=str(payload.get("environment") or "production"),
        )
    except ActuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={**result, "tenant_id": tenant_id})


@router.post("/api/v1/actions/{action_id}/rollback")
def rollback_registered_action(
    action_id: str,
    payload: dict[str, Any],
    request: Request,
) -> APIResponse:
    """回滚已执行的可逆动作（当前支持从隔离区恢复 Mini-Drop 缓存）。"""
    _require_role(request, "operator")
    tenant_id = str(payload.get("tenant_id") or _request_tenant())
    if tenant_id != _request_tenant():
        raise HTTPException(status_code=403, detail="ACTION_TENANT_MISMATCH")
    definition = DEFAULT_ACTION_REGISTRY.get(action_id)
    rollback_id = definition.rollback_action_id if definition else None
    if not rollback_id or not is_executable(rollback_id):
        raise HTTPException(status_code=409, detail=f"动作 {action_id} 没有可执行的回滚动作")
    try:
        dry = ACTUATION_GATEWAY.dry_run(rollback_id, payload.get("parameters") or {})
        if not dry.get("dry_run", {}).get("candidate_count", 0):
            return APIResponse(data={"attempt_id": dry["attempt_id"], "stage": "NOTHING_TO_ROLLBACK", "executed": []})
        result = ACTUATION_GATEWAY.execute(rollback_id, dry["attempt_id"], str(payload.get("environment") or "production"))
    except ActuationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data={**result, "tenant_id": tenant_id})




def _extract_top_functions(artifacts: list[dict]) -> list[dict]:
    """Read TopN JSON, or derive it from an available flamegraph SVG."""

    top_functions = _extract_artifact_json(artifacts, "top_json")
    if isinstance(top_functions, list) and top_functions:
        return top_functions

    for artifact in artifacts:
        if artifact.get("artifact_type") != "flamegraph_svg":
            continue
        try:
            svg_text = read_artifact_bytes(artifact).decode("utf-8", errors="replace")
            derived = extract_top_functions_from_svg(svg_text)
            if derived:
                return derived
        except Exception as exc:
            log_event(
                "warning",
                "flamegraph_svg_top_parse_failed",
                artifact_type="flamegraph_svg",
                error=type(exc).__name__,
            )
    return []


def _artifact_root() -> _Path:
    return _Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def _resolve_artifact_path(local_path: str | None) -> _Path:
    if not local_path:
        raise HTTPException(status_code=404, detail="本地产物不存在")

    root = _artifact_root()
    candidate = _Path(local_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="产物路径不在允许目录内")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="本地产物不存在")
    return resolved


def _resolve_artifact_path_or_none(local_path: str | None) -> _Path | None:
    try:
        return _resolve_artifact_path(local_path)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _read_artifact_object_text(artifact: dict) -> str:
    bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
    key = _validate_presign_request(bucket, artifact.get("object_key", ""))
    try:
        return store.read_object_bytes(bucket, key).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log_event("warning", "artifact_object_read_failed", bucket=bucket, object_key=key, error=type(exc).__name__)
        raise HTTPException(status_code=404, detail="对象存储产物不存在") from exc


def _validate_presign_request(bucket: str, key: str) -> str:
    allowed_bucket = os.getenv("MINIO_BUCKET", "mini-drop")
    if bucket != allowed_bucket:
        raise HTTPException(status_code=403, detail="bucket 不在允许范围内")
    if not key:
        raise HTTPException(status_code=400, detail="key 参数不能为空")
    normalized = key.replace("\\", "/")
    if normalized.startswith("/") or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="key 路径不合法")
    if not normalized.startswith("tasks/"):
        raise HTTPException(status_code=403, detail="key 不在任务产物目录内")
    return normalized


def _safe_download_filename(value: str) -> str:
    filename = _Path(value.replace("\\", "/")).name
    filename = "".join(ch for ch in filename if ch >= " " and ch not in {'"', ';'})
    return filename[:255] or "artifact.bin"



__all__ = [
    "ACTUATION_GATEWAY",
    "AUTONOMOUS_AGENT",
    "CASE_SUPERVISOR",
    "PLAN_DRIVER",
    "router",
]
