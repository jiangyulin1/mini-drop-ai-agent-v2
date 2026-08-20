"""Deterministic authority for AI-proposed native collection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mini_drop_contracts import get_collector_spec
from server.app.schemas import CreateTaskRequest


class CollectionSupervisor:
    """Validate a proposal and compile one accepted request into one Task."""

    MAX_COLLECTION_REQUESTS = 8
    MAX_COLLECTION_DURATION_SEC = 240

    def __init__(self, repository: Any):
        self._repo = repository

    def propose_and_dispatch(
        self,
        *,
        case_id: str,
        tenant_id: str,
        collector_id: str,
        target_selector: dict[str, Any],
        parameters: dict[str, Any],
        information_goal: str,
        reason_summary: str = "",
        time_window: dict[str, Any] | None = None,
        input_evidence_refs: list[str] | None = None,
        runtime_generation: int = 1,
        expected_control_revision: int | None = None,
        expected_scope_revision: int | None = None,
        idempotency_key: str | None = None,
        allowed_risk_levels: set[str] | frozenset[str] | None = None,
        max_collection_requests: int = MAX_COLLECTION_REQUESTS,
        max_collection_duration_sec: int = MAX_COLLECTION_DURATION_SEC,
        agent_run_id: str | None = None,
        cycle_id: str | None = None,
        auto_dispatch: bool = True,
        existing_proposal_id: str | None = None,
    ) -> dict[str, Any]:
        case = self._repo.get_incident_case(case_id, tenant_id)
        spec = get_collector_spec(collector_id)
        if existing_proposal_id:
            proposal = self._repo.get_collection_proposal(
                existing_proposal_id, case_id, tenant_id,
            )
            if proposal is None:
                raise ValueError("COLLECTION_PROPOSAL_NOT_FOUND")
            if proposal.get("status") != "PROPOSED":
                raise ValueError("COLLECTION_PROPOSAL_NOT_PENDING")
        else:
            proposal = self._repo.create_collection_proposal(
                case_id=case_id, tenant_id=tenant_id, agent_run_id=agent_run_id,
                cycle_id=cycle_id, collector_id=collector_id,
                collector_spec_version=spec.spec_version if spec else "unknown",
                target_selector=target_selector or {}, parameters=parameters or {},
                time_window=time_window or {}, information_goal=information_goal,
                reason_summary=reason_summary, expected_cost=(spec.estimated_overhead if spec else {}),
                expected_risk=(spec.risk_level if spec else "UNKNOWN"),
                input_evidence_refs=input_evidence_refs or [],
            )

        errors: list[str] = []
        if case is None:
            errors.append("CASE_NOT_FOUND")
        if spec is None or not spec.enabled:
            errors.append("COLLECTOR_NOT_REGISTERED")
        if (
            existing_proposal_id
            and spec is not None
            and proposal.get("collector_spec_version") != spec.spec_version
        ):
            errors.append("COLLECTOR_SPEC_VERSION_CHANGED")
        if spec is not None and information_goal not in spec.information_goals:
            errors.append("INFORMATION_GOAL_NOT_DECLARED_BY_COLLECTOR")
        if len(reason_summary) > 1000:
            errors.append("REASON_SUMMARY_TOO_LONG")
        if case is not None:
            if expected_control_revision is not None and int(expected_control_revision) != int(case.get("control_revision") or 1):
                errors.append("STALE_CONTROL_REVISION")
            if expected_scope_revision is not None and int(expected_scope_revision) != int(case.get("scope_revision") or 1):
                errors.append("STALE_SCOPE_REVISION")
        if spec is not None:
            validation_parameters = {
                **(parameters or {}),
                **({"target_pid": target_selector.get("target_pid") or target_selector.get("pid")}
                   if (target_selector.get("target_pid") or target_selector.get("pid")) else {}),
            }
            errors.extend(self._validate_parameters(spec.parameter_schema, validation_parameters))
            allowed = set(allowed_risk_levels or {"R0", "R1"})
            if spec.risk_level not in allowed:
                errors.append("COLLECTOR_RISK_NOT_ALLOWED")

        target = self._resolve_target(case or {}, target_selector or {})
        if target is None:
            errors.append("TARGET_UNAVAILABLE_OR_OUT_OF_SCOPE")
        elif spec is not None and spec.collector_id not in target["capabilities"]:
            errors.append("AGENT_CAPABILITY_MISSING")

        for evidence_id in input_evidence_refs or []:
            evidence = self._repo.get_case_evidence(case_id, tenant_id, evidence_id)
            if evidence is None:
                errors.append(f"INPUT_EVIDENCE_NOT_FOUND:{evidence_id}")
            elif evidence.get("status") == "EXCLUDED":
                errors.append(f"INPUT_EVIDENCE_EXCLUDED:{evidence_id}")

        validation = {
            "accepted": not errors,
            "errors": errors,
            "collector_catalog_rechecked": True,
            "scope_rechecked": True,
            "capability_rechecked": True,
        }
        if errors:
            rejected = self._repo.decide_collection_proposal(
                proposal["proposal_id"], "REJECTED", validation,
            )
            return {"proposal": rejected, "collection_request": None, "task": None}

        if not auto_dispatch:
            pending = self._repo.decide_collection_proposal(
                proposal["proposal_id"], "PROPOSED",
                {
                    **validation,
                    "awaiting_execution_authority": True,
                    "approval_context": {
                        "runtime_generation": max(1, int(runtime_generation or 1)),
                        "control_revision": int(case.get("control_revision") or 1),
                        "scope_revision": int(case.get("scope_revision") or 1),
                        "idempotency_key": idempotency_key,
                        "allowed_risk_levels": sorted(allowed_risk_levels or {"R0", "R1"}),
                        "max_collection_requests": max_collection_requests,
                        "max_collection_duration_sec": max_collection_duration_sec,
                    },
                },
            )
            return {"proposal": pending, "collection_request": None, "task": None}

        effective = self._effective_parameters(spec, parameters or {}, target)
        request_key = (
            self._scoped_idempotency_key(case_id, tenant_id, idempotency_key)
            if idempotency_key
            else self._idempotency_key(
                case_id, spec.collector_id, target, effective,
                int(case.get("scope_revision") or 1),
            )
        )
        existing_requests = self._repo.list_collection_requests(case_id, tenant_id)
        duplicate = next(
            (item for item in existing_requests if item.get("idempotency_key") == request_key),
            None,
        )
        if duplicate is not None:
            duplicate_validation = {
                **validation,
                "duplicate": True,
                "duplicate_of_request": duplicate["collection_request_id"],
                "duplicate_of_proposal": duplicate["proposal_id"],
                "budget_consumed": False,
            }
            task_id = duplicate.get("task_id")
            if task_id:
                accepted = self._repo.decide_collection_proposal(
                    proposal["proposal_id"], "ACCEPTED", duplicate_validation,
                )
                return {
                    "proposal": accepted,
                    "collection_request": duplicate,
                    "task": self._repo.tasks.get(task_id),
                }
            try:
                task = self._create_task(
                    proposal=proposal, collection_request=duplicate, spec=spec,
                    target=target, effective=effective, request_key=request_key,
                    case=case, case_id=case_id, tenant_id=tenant_id,
                    information_goal=information_goal,
                )
            except Exception as exc:
                self._mark_dispatch_failed(proposal, duplicate, validation)
                raise ValueError("COLLECTION_TASK_DISPATCH_FAILED") from exc
            updated = self._repo.update_collection_request(
                duplicate["collection_request_id"], status="DISPATCHED", task_id=task.id,
            )
            accepted = self._repo.decide_collection_proposal(
                proposal["proposal_id"], "ACCEPTED", duplicate_validation,
            )
            return {"proposal": accepted, "collection_request": updated, "task": task}

        request_limit = min(
            max(1, int(max_collection_requests or self.MAX_COLLECTION_REQUESTS)),
            self.MAX_COLLECTION_REQUESTS,
        )
        duration_limit = min(
            max(1, int(max_collection_duration_sec or self.MAX_COLLECTION_DURATION_SEC)),
            self.MAX_COLLECTION_DURATION_SEC,
        )
        reserved_duration = int(effective["duration_sec"])
        consumed_duration = sum(self._reserved_duration(item) for item in existing_requests)
        budget = {
            "request_limit": request_limit,
            "request_count": len(existing_requests),
            "duration_limit_sec": duration_limit,
            "reserved_duration_sec": consumed_duration,
            "requested_duration_sec": reserved_duration,
        }
        budget_errors: list[str] = []
        if len(existing_requests) >= request_limit:
            budget_errors.append("COLLECTION_REQUEST_COUNT_BUDGET_EXHAUSTED")
        if consumed_duration + reserved_duration > duration_limit:
            budget_errors.append("COLLECTION_REQUEST_DURATION_BUDGET_EXHAUSTED")
        if budget_errors:
            rejected = self._repo.decide_collection_proposal(
                proposal["proposal_id"], "REJECTED",
                {
                    **validation,
                    "accepted": False,
                    "errors": budget_errors,
                    "budget_rechecked": True,
                    "budget": budget,
                },
            )
            return {"proposal": rejected, "collection_request": None, "task": None}

        acceptance_validation = {
            **validation,
            "budget_rechecked": True,
            "budget_consumed": True,
            "budget": budget,
        }
        try:
            collection_request = self._repo.create_collection_request(
                proposal_id=proposal["proposal_id"], case_id=case_id, tenant_id=tenant_id,
                collector_id=spec.collector_id, collector_spec_version=spec.spec_version,
                resolved_target_identity={
                    "agent_id": target["agent_id"], "target_pid": target["target_pid"],
                    "hostname": target.get("hostname"), "resource_incarnation": target.get("resource_incarnation"),
                },
                effective_parameters=effective, runtime_generation=max(1, int(runtime_generation or 1)),
                control_revision=int(case.get("control_revision") or 1),
                scope_revision=int(case.get("scope_revision") or 1),
                idempotency_key=request_key,
                request_limit=request_limit,
                duration_limit_sec=duration_limit,
                budget_reservation={
                    "max_result_bytes": spec.max_result_bytes,
                    "max_duration_sec": spec.max_duration,
                    "reserved_duration_sec": reserved_duration,
                    "estimated_overhead": spec.estimated_overhead,
                },
            )
        except ValueError as exc:
            code = str(exc)
            if code not in {
                "COLLECTION_REQUEST_COUNT_BUDGET_EXHAUSTED",
                "COLLECTION_REQUEST_DURATION_BUDGET_EXHAUSTED",
            }:
                raise
            rejected = self._repo.decide_collection_proposal(
                proposal["proposal_id"], "REJECTED",
                {
                    **validation,
                    "accepted": False,
                    "errors": [code],
                    "budget_rechecked": True,
                    "budget": budget,
                },
            )
            return {"proposal": rejected, "collection_request": None, "task": None}
        if collection_request.get("task_id"):
            accepted = self._repo.decide_collection_proposal(
                proposal["proposal_id"], "ACCEPTED", acceptance_validation,
            )
            return {
                "proposal": accepted, "collection_request": collection_request,
                "task": self._repo.tasks.get(collection_request["task_id"]),
            }
        try:
            task = self._create_task(
                proposal=proposal, collection_request=collection_request, spec=spec,
                target=target, effective=effective, request_key=request_key,
                case=case, case_id=case_id, tenant_id=tenant_id,
                information_goal=information_goal,
            )
        except Exception as exc:
            self._mark_dispatch_failed(proposal, collection_request, validation)
            raise ValueError("COLLECTION_TASK_DISPATCH_FAILED") from exc
        updated = self._repo.update_collection_request(
            collection_request["collection_request_id"], status="DISPATCHED", task_id=task.id,
        )
        accepted = self._repo.decide_collection_proposal(
            proposal["proposal_id"], "ACCEPTED", acceptance_validation,
        )
        return {"proposal": accepted, "collection_request": updated, "task": task}

    def decide_pending_proposal(
        self, *, proposal_id: str, case_id: str, tenant_id: str,
        decision: str, decided_by: str, reason: str = "",
        expected_control_revision: int | None = None,
        expected_scope_revision: int | None = None,
    ) -> dict[str, Any]:
        proposal = self._repo.get_collection_proposal(proposal_id, case_id, tenant_id)
        if proposal is None:
            raise ValueError("COLLECTION_PROPOSAL_NOT_FOUND")
        if proposal.get("status") != "PROPOSED":
            raise ValueError("COLLECTION_PROPOSAL_NOT_PENDING")
        validation = proposal.get("validation_result") or {}
        if not validation.get("awaiting_execution_authority"):
            raise ValueError("COLLECTION_PROPOSAL_NOT_AWAITING_APPROVAL")
        normalized = str(decision or "").upper()
        if normalized == "REJECT":
            rejected = self._repo.decide_collection_proposal(
                proposal_id, "REJECTED", {
                    **validation, "accepted": False, "approval_decision": "REJECT",
                    "decided_by": decided_by, "decision_reason": reason,
                },
            )
            return {"proposal": rejected, "collection_request": None, "task": None}
        if normalized != "APPROVE":
            raise ValueError("INVALID_COLLECTION_PROPOSAL_DECISION")
        context = validation.get("approval_context") or {}
        pinned_control = int(context.get("control_revision") or 1)
        pinned_scope = int(context.get("scope_revision") or 1)
        if expected_control_revision is not None and int(expected_control_revision) != pinned_control:
            raise ValueError("APPROVAL_CONTROL_REVISION_MISMATCH")
        if expected_scope_revision is not None and int(expected_scope_revision) != pinned_scope:
            raise ValueError("APPROVAL_SCOPE_REVISION_MISMATCH")
        result = self.propose_and_dispatch(
            case_id=case_id, tenant_id=tenant_id,
            collector_id=str(proposal.get("collector_id") or ""),
            target_selector=proposal.get("target_selector") or {},
            parameters=proposal.get("parameters") or {},
            information_goal=str(proposal.get("information_goal") or ""),
            reason_summary=str(proposal.get("reason_summary") or ""),
            time_window=proposal.get("time_window") or {},
            input_evidence_refs=proposal.get("input_evidence_refs") or [],
            runtime_generation=int(context.get("runtime_generation") or 1),
            expected_control_revision=pinned_control,
            expected_scope_revision=pinned_scope,
            idempotency_key=context.get("idempotency_key"),
            allowed_risk_levels=set(context.get("allowed_risk_levels") or {"R0", "R1"}),
            max_collection_requests=int(context.get("max_collection_requests") or self.MAX_COLLECTION_REQUESTS),
            max_collection_duration_sec=int(context.get("max_collection_duration_sec") or self.MAX_COLLECTION_DURATION_SEC),
            agent_run_id=proposal.get("agent_run_id"), cycle_id=proposal.get("cycle_id"),
            auto_dispatch=True, existing_proposal_id=proposal_id,
        )
        approved = result.get("proposal") or {}
        if approved.get("status") == "REJECTED":
            errors = (approved.get("validation_result") or {}).get("errors") or []
            raise ValueError("COLLECTION_PROPOSAL_FENCED:" + ",".join(errors))
        approved_validation = {
            **(approved.get("validation_result") or {}),
            "approval_decision": "APPROVE", "decided_by": decided_by,
            "decision_reason": reason,
        }
        result["proposal"] = self._repo.decide_collection_proposal(
            proposal_id, str(approved.get("status") or "ACCEPTED"), approved_validation,
        )
        return result

    def _create_task(
        self, *, proposal: dict[str, Any], collection_request: dict[str, Any],
        spec: Any, target: dict[str, Any], effective: dict[str, Any], request_key: str,
        case: dict[str, Any], case_id: str, tenant_id: str, information_goal: str,
    ) -> Any:
        return self._repo.create_task(
            CreateTaskRequest(
                name=f"agent-collection:{spec.collector_id}:{target['agent_id']}"[:120],
                agent_id=target["agent_id"], target_pid=effective["target_pid"],
                collector_type=spec.collector_id,
                sample_rate=effective["sample_rate"], duration_sec=effective["duration_sec"],
                options={
                    "source": "collection_supervisor", "case_id": case_id,
                    "tenant_id": tenant_id, "collection_proposal_id": proposal["proposal_id"],
                    "collection_request_id": collection_request["collection_request_id"],
                    "collector_spec_version": spec.spec_version,
                    "information_goal": information_goal,
                    "scope_revision": int(case.get("scope_revision") or 1),
                    "control_revision": int(case.get("control_revision") or 1),
                },
            ),
            idempotency_key=f"collection-request:{request_key}",
        )

    def _mark_dispatch_failed(
        self, proposal: dict[str, Any], collection_request: dict[str, Any],
        validation: dict[str, Any],
    ) -> None:
        self._repo.update_collection_request(
            collection_request["collection_request_id"], status="DISPATCH_FAILED",
        )
        self._repo.decide_collection_proposal(
            proposal["proposal_id"], "FAILED", {
                **validation, "accepted": False,
                "errors": ["COLLECTION_TASK_DISPATCH_FAILED"],
            },
        )

    @staticmethod
    def _reserved_duration(collection_request: dict[str, Any]) -> int:
        reservation = collection_request.get("budget_reservation") or {}
        effective = collection_request.get("effective_parameters") or {}
        value = (
            reservation.get("reserved_duration_sec")
            or effective.get("duration_sec")
            or reservation.get("max_duration_sec")
            or 0
        )
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _validate_parameters(schema: dict[str, Any], values: dict[str, Any]) -> list[str]:
        if not isinstance(values, dict):
            return ["INVALID_PARAMETERS"]
        properties = schema.get("properties") or {}
        errors: list[str] = []
        for required in schema.get("required") or []:
            if required not in values:
                errors.append(f"MISSING_PARAMETER:{required}")
        for key, value in values.items():
            rule = properties.get(key)
            if rule is None:
                errors.append(f"UNSUPPORTED_PARAMETER:{key}")
                continue
            expected = rule.get("type")
            if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                errors.append(f"INVALID_PARAMETER_TYPE:{key}")
                continue
            if expected == "string" and not isinstance(value, str):
                errors.append(f"INVALID_PARAMETER_TYPE:{key}")
                continue
            if isinstance(value, int):
                if "minimum" in rule and value < int(rule["minimum"]):
                    errors.append(f"PARAMETER_BELOW_MINIMUM:{key}")
                if "maximum" in rule and value > int(rule["maximum"]):
                    errors.append(f"PARAMETER_ABOVE_MAXIMUM:{key}")
            if isinstance(value, str) and rule.get("maxLength") and len(value) > int(rule["maxLength"]):
                errors.append(f"PARAMETER_TOO_LONG:{key}")
        return errors

    def _resolve_target(self, case: dict[str, Any], selector: dict[str, Any]) -> dict[str, Any] | None:
        requested_agent = str(selector.get("agent_id") or "")
        requested_pid = int(selector.get("target_pid") or selector.get("pid") or 0)
        instances = list((case.get("target_scope") or {}).get("instances") or [])
        allowed_agents = {str(item.get("agent_id") or "") for item in instances if item.get("agent_id")}
        if requested_agent and allowed_agents and requested_agent not in allowed_agents:
            return None
        agents = getattr(self._repo, "agents", {})
        candidates = [agents.get(requested_agent)] if requested_agent else list(agents.values())
        for agent in candidates:
            if agent is None or str(getattr(agent, "status", "ONLINE")) != "ONLINE":
                continue
            agent_id = str(getattr(agent, "id", "") or "")
            if allowed_agents and agent_id not in allowed_agents:
                continue
            instance = next((item for item in instances if str(item.get("agent_id") or "") == agent_id), {})
            pid = requested_pid or int(instance.get("pid") or 1)
            return {
                "agent_id": agent_id, "target_pid": pid,
                "hostname": getattr(agent, "hostname", None),
                "capabilities": set(getattr(agent, "capabilities", None) or []),
                "resource_incarnation": instance.get("resource_incarnation"),
            }
        return None

    @staticmethod
    def _effective_parameters(spec: Any, values: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        return {
            **values,
            "target_pid": int(values.get("target_pid") or target["target_pid"]),
            "duration_sec": min(int(values.get("duration_sec") or spec.default_duration), spec.max_duration),
            "sample_rate": int(values.get("sample_rate") or spec.default_sample_rate),
        }

    @staticmethod
    def _idempotency_key(
        case_id: str, collector_id: str, target: dict[str, Any], parameters: dict[str, Any], scope_revision: int,
    ) -> str:
        canonical = json.dumps({
            "case_id": case_id, "collector_id": collector_id,
            "agent_id": target["agent_id"], "parameters": parameters,
            "scope_revision": scope_revision,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _scoped_idempotency_key(case_id: str, tenant_id: str, supplied_key: str) -> str:
        canonical = json.dumps({
            "case_id": case_id,
            "tenant_id": tenant_id,
            "supplied_key": supplied_key,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
