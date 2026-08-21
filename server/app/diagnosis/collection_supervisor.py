"""Deterministic authority for AI-proposed native collection."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from mini_drop_contracts import get_collector_spec
from server.app.diagnosis.network_discovery import (
    aggregate_dependency_graph,
    case_dependency_graph_snapshot,
)
from server.app.schemas import CreateTaskRequest


class CollectionSupervisor:
    """Validate a proposal and compile one accepted request into one Task."""

    MAX_COLLECTION_REQUESTS = 8
    MAX_COLLECTION_DURATION_SEC = 240
    _DISPATCH_CONTEXT_KEYS = frozenset({
        "discovery_run_id", "discovery_hop", "discovery_parent_task_id",
        "discovery_seed_ref", "discovery_authority_evidence_ref",
        "discovery_authority_evidence_refs",
        "discovery_followup_authority",
        "discovery_phase", "membership_snapshot_id",
        "expected_boot_id", "expected_process_start_time", "expected_entity_id",
    })

    def __init__(self, repository: Any):
        self._repo = repository

    @staticmethod
    def _projection_discovery_run_id(projection: dict[str, Any]) -> str:
        content = projection.get("content") or {}
        topology = content.get("topology") or {}
        return str(
            content.get("discovery_run_id")
            or (topology.get("discovery_run_id") if isinstance(topology, dict) else "")
            or ""
        )

    @staticmethod
    def _projection_membership_snapshot_id(projection: dict[str, Any]) -> str:
        content = projection.get("content") or {}
        topology = content.get("topology") or {}
        return str(
            content.get("membership_snapshot_id")
            or (
                topology.get("membership_snapshot_id")
                if isinstance(topology, dict) else ""
            )
            or ""
        )

    @staticmethod
    def case_scoped_process_targets(case: dict[str, Any]) -> set[tuple[str, int]]:
        """Return explicit ``agent_id + pid`` process targets from Case scope."""
        target_scope = case.get("target_scope") or {}
        result: set[tuple[str, int]] = set()
        for item in target_scope.get("instances") or []:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or "").strip()
            try:
                pid = int(item.get("pid") or item.get("target_pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if agent_id and pid > 0:
                result.add((agent_id, pid))
        top_level_agent_id = str(target_scope.get("agent_id") or "").strip()
        try:
            top_level_pid = int(
                target_scope.get("pid") or target_scope.get("target_pid") or 0
            )
        except (TypeError, ValueError):
            top_level_pid = 0
        if top_level_agent_id and top_level_pid > 0:
            result.add((top_level_agent_id, top_level_pid))
        return result

    @classmethod
    def case_scoped_agent_ids(cls, case: dict[str, Any]) -> set[str]:
        """Compatibility view of explicit process targets, without PID widening."""
        return {agent_id for agent_id, _pid in cls.case_scoped_process_targets(case)}

    @staticmethod
    def case_scoped_agent_level_ids(case: dict[str, Any]) -> set[str]:
        """Return explicit Agent-level scope entries that intentionally omit PID."""
        target_scope = case.get("target_scope") or {}
        result: set[str] = set()
        top_level_agent_id = str(target_scope.get("agent_id") or "").strip()
        try:
            top_level_pid = int(
                target_scope.get("pid") or target_scope.get("target_pid") or 0
            )
        except (TypeError, ValueError):
            top_level_pid = 0
        if top_level_agent_id and top_level_pid <= 0:
            result.add(top_level_agent_id)
        for item in target_scope.get("instances") or []:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or "").strip()
            try:
                pid = int(item.get("pid") or item.get("target_pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if agent_id and pid <= 0:
                result.add(agent_id)
        return result

    def authorize_discovered_target(
        self,
        *,
        case_id: str,
        tenant_id: str,
        target_selector: dict[str, Any],
        discovery_run_id: str,
        evidence_refs: list[str],
        expected_control_revision: int | None,
        expected_scope_revision: int | None,
    ) -> dict[str, Any]:
        """Resolve one proposal-scoped authority from canonical dependency Evidence.

        This does not mutate the Case scope or create a reusable grant.  It only
        proves that the exact ``agent_id + pid`` target is an observed process in
        the named discovery run, using active canonical Evidence at the current
        control/scope revisions.  The caller may then pass the returned Agent ID
        to one ``propose_and_dispatch`` invocation as ``authorized_agent_ids``.
        """
        case = self._repo.get_incident_case(case_id, tenant_id)
        if case is None:
            raise ValueError("CASE_NOT_FOUND")
        if expected_control_revision is None or expected_scope_revision is None:
            raise ValueError("DISCOVERY_AUTHORITY_REVISION_REQUIRED")
        if int(expected_control_revision) != int(case.get("control_revision") or 1):
            raise ValueError("STALE_CONTROL_REVISION")
        if int(expected_scope_revision) != int(case.get("scope_revision") or 1):
            raise ValueError("STALE_SCOPE_REVISION")

        run_id = str(discovery_run_id or "").strip()
        if re.fullmatch(r"discovery-[0-9a-f]{20}", run_id) is None:
            raise ValueError("DISCOVERY_AUTHORITY_RUN_REQUIRED")
        agent_id = str(target_selector.get("agent_id") or "").strip()
        try:
            target_pid = int(
                target_selector.get("target_pid") or target_selector.get("pid") or 0
            )
        except (TypeError, ValueError):
            target_pid = 0
        if not agent_id or target_pid <= 0:
            raise ValueError("DISCOVERY_AUTHORITY_TARGET_REQUIRED")

        refs = list(dict.fromkeys(
            str(item).strip() for item in (evidence_refs or []) if str(item).strip()
        ))
        if not refs:
            raise ValueError("DISCOVERY_AUTHORITY_EVIDENCE_REQUIRED")
        if len(refs) > 32:
            raise ValueError("DISCOVERY_AUTHORITY_EVIDENCE_LIMIT_EXCEEDED")

        evidence_items: list[dict[str, Any]] = []
        projections: list[dict[str, Any]] = []
        membership_snapshot_ids: set[str] = set()
        for evidence_id in refs:
            evidence = self._repo.get_case_evidence(
                case_id, tenant_id, evidence_id,
            )
            if (
                evidence is None
                or str(evidence.get("status") or "") != "ACTIVE"
                or bool(evidence.get("stale_for_current_revision"))
            ):
                raise ValueError(
                    f"DISCOVERY_AUTHORITY_EVIDENCE_NOT_ACTIVE:{evidence_id}"
                )
            membership_snapshot_id = str(
                evidence.get("membership_snapshot_id") or ""
            ).strip()
            if not membership_snapshot_id:
                raise ValueError(
                    f"DISCOVERY_AUTHORITY_MEMBERSHIP_SNAPSHOT_REQUIRED:{evidence_id}"
                )
            membership_snapshot = self._repo.get_membership_snapshot(
                case_id, tenant_id, membership_snapshot_id,
            )
            if membership_snapshot is None:
                raise ValueError(
                    "DISCOVERY_AUTHORITY_MEMBERSHIP_SNAPSHOT_NOT_FOUND:"
                    f"{membership_snapshot_id}"
                )
            if int(membership_snapshot.get("scope_revision") or 1) != int(
                case.get("scope_revision") or 1
            ):
                raise ValueError(
                    "DISCOVERY_AUTHORITY_MEMBERSHIP_SCOPE_MISMATCH:"
                    f"{membership_snapshot_id}"
                )
            member_agent_ids = {
                str(member.get("agent_id") or "").strip()
                for member in membership_snapshot.get("members") or []
                if isinstance(member, dict)
            }
            if agent_id not in member_agent_ids:
                raise ValueError(
                    f"DISCOVERY_AUTHORITY_TARGET_NOT_IN_MEMBERSHIP:{agent_id}"
                )
            matching = []
            for projection in self._repo.list_evidence_projections(
                case_id, tenant_id, evidence_id=evidence_id,
            ):
                if str(projection.get("projection_kind") or "") not in {
                    "DEPENDENCY_GRAPH", "TOPOLOGY_GRAPH",
                }:
                    continue
                if self._projection_discovery_run_id(projection) != run_id:
                    continue
                if (
                    self._projection_membership_snapshot_id(projection)
                    != membership_snapshot_id
                ):
                    raise ValueError(
                        "DISCOVERY_AUTHORITY_PROJECTION_MEMBERSHIP_MISMATCH:"
                        f"{evidence_id}"
                    )
                matching.append({"evidence_id": evidence_id, **projection})
            if not matching:
                raise ValueError(
                    f"DISCOVERY_AUTHORITY_RUN_EVIDENCE_MISMATCH:{evidence_id}"
                )
            evidence_items.append(evidence)
            projections.extend(matching)
            membership_snapshot_ids.add(membership_snapshot_id)

        if len(membership_snapshot_ids) != 1:
            raise ValueError("DISCOVERY_AUTHORITY_MULTIPLE_MEMBERSHIP_SNAPSHOTS")

        scoped = aggregate_dependency_graph(evidence_items, projections)
        if run_id not in set(scoped.get("discovery_run_ids") or []):
            raise ValueError("DISCOVERY_AUTHORITY_RUN_EVIDENCE_MISMATCH")

        requested_entity_id = str(
            target_selector.get("target_entity_id")
            or target_selector.get("entity_id")
            or target_selector.get("target_ref")
            or ""
        ).strip()
        declared_entity_type = str(
            target_selector.get("entity_type") or ""
        ).strip()
        forbidden_endpoint_types = {
            "external_unmanaged_endpoint", "virtual_endpoint",
        }
        if declared_entity_type in forbidden_endpoint_types:
            raise ValueError(
                "DISCOVERY_AUTHORITY_ENDPOINT_NOT_COLLECTABLE:"
                f"{declared_entity_type}"
            )
        if requested_entity_id:
            requested_nodes = [
                node for node in (scoped.get("graph") or {}).get("nodes") or []
                if str(node.get("entity_id") or "") == requested_entity_id
            ]
            if len(requested_nodes) != 1:
                raise ValueError("DISCOVERY_AUTHORITY_TARGET_ENTITY_NOT_FOUND")
            requested_type = str(requested_nodes[0].get("entity_type") or "")
            if requested_type in forbidden_endpoint_types:
                raise ValueError(
                    "DISCOVERY_AUTHORITY_ENDPOINT_NOT_COLLECTABLE:"
                    f"{requested_type}"
                )
            if requested_type != "process":
                raise ValueError(
                    f"DISCOVERY_AUTHORITY_ENTITY_NOT_COLLECTABLE:{requested_type}"
                )

        def target_nodes(graph_payload: dict[str, Any]) -> list[dict[str, Any]]:
            matches: list[dict[str, Any]] = []
            for node in (graph_payload.get("graph") or {}).get("nodes") or []:
                process = node.get("process") or {}
                node_agent_id = str(process.get("agent_id") or node.get("agent_id") or "")
                try:
                    node_pid = int(process.get("pid") or 0)
                except (TypeError, ValueError):
                    node_pid = 0
                if (
                    str(node.get("entity_type") or "") == "process"
                    and node_agent_id == agent_id
                    and node_pid == target_pid
                ):
                    matches.append(node)
            return matches

        scoped_targets = target_nodes(scoped)
        if len(scoped_targets) != 1:
            code = (
                "DISCOVERY_AUTHORITY_TARGET_NOT_FOUND"
                if not scoped_targets else "DISCOVERY_AUTHORITY_TARGET_AMBIGUOUS"
            )
            raise ValueError(code)
        target_entity_id = str(scoped_targets[0].get("entity_id") or "")
        if requested_entity_id and target_entity_id != requested_entity_id:
            raise ValueError("DISCOVERY_AUTHORITY_TARGET_ENTITY_MISMATCH")
        target_process = scoped_targets[0].get("process") or {}
        expected_boot_id = str(target_process.get("boot_id") or "").strip()
        try:
            expected_process_start_time = int(
                target_process.get("process_start_time") or 0
            )
        except (TypeError, ValueError):
            expected_process_start_time = 0
        expected_entity_id = (
            f"process:{agent_id}:{expected_boot_id}:{target_pid}:"
            f"{expected_process_start_time}"
        )
        if (
            not expected_boot_id
            or expected_process_start_time <= 0
            or target_entity_id != expected_entity_id
        ):
            raise ValueError("DISCOVERY_AUTHORITY_INCARNATION_MISMATCH")
        incident_edges = [
            edge for edge in (scoped.get("graph") or {}).get("edges") or []
            if target_entity_id in {
                str(edge.get("source_entity") or ""),
                str(edge.get("target_entity") or ""),
            }
        ]
        if not incident_edges:
            raise ValueError("DISCOVERY_AUTHORITY_TARGET_NOT_CONNECTED")
        supporting_refs = {
            str(item)
            for edge in incident_edges
            for item in edge.get("evidence_refs") or []
            if item
        }
        if not supporting_refs.intersection(refs):
            raise ValueError("DISCOVERY_AUTHORITY_TARGET_EVIDENCE_MISMATCH")

        # Recheck the current full Case aggregate as well as the run-scoped
        # subset above.  An excluded/stale projection must not remain usable as
        # collection authority merely because it existed earlier in the run.
        current = case_dependency_graph_snapshot(
            self._repo, case_id, tenant_id,
        )
        if run_id not in set(current.get("discovery_run_ids") or []):
            raise ValueError("DISCOVERY_AUTHORITY_RUN_NOT_CURRENT")
        current_targets = {
            str(item.get("entity_id") or "") for item in target_nodes(current)
        }
        if target_entity_id not in current_targets:
            raise ValueError("DISCOVERY_AUTHORITY_TARGET_NOT_CURRENT")
        current_incident = [
            edge for edge in (current.get("graph") or {}).get("edges") or []
            if target_entity_id in {
                str(edge.get("source_entity") or ""),
                str(edge.get("target_entity") or ""),
            }
        ]
        if not current_incident:
            raise ValueError("DISCOVERY_AUTHORITY_TARGET_NOT_CURRENT")

        return {
            "authorized_agent_ids": {agent_id},
            "discovery_run_id": run_id,
            "evidence_refs": refs,
            "membership_snapshot_id": next(iter(membership_snapshot_ids)),
            "target_entity_id": target_entity_id,
            "expected_boot_id": expected_boot_id,
            "expected_process_start_time": expected_process_start_time,
            "expected_entity_id": expected_entity_id,
        }

    def mark_task_terminal(
        self, case_id: str, tenant_id: str, task_id: str, task_status: str,
    ) -> list[dict[str, Any]]:
        """Project one native Task terminal state onto its CollectionRequest."""
        request_status = {
            "DONE": "COMPLETED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
        }.get(str(task_status).upper())
        if request_status is None:
            return []
        updated: list[dict[str, Any]] = []
        for request in self._repo.list_collection_requests(case_id, tenant_id):
            if str(request.get("task_id") or "") != str(task_id):
                continue
            if request.get("status") == request_status:
                updated.append(request)
                continue
            item = self._repo.update_collection_request(
                request["collection_request_id"], status=request_status, task_id=task_id,
            )
            if item is not None:
                updated.append(item)
        return updated

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
        plan_step_id: str | None = None,
        plan_revision: int | None = None,
        auto_dispatch: bool = True,
        existing_proposal_id: str | None = None,
        authorized_agent_ids: set[str] | frozenset[str] | None = None,
        dispatch_context: dict[str, Any] | None = None,
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
            plan_step_id = plan_step_id or proposal.get("plan_step_id")
            plan_revision = plan_revision if plan_revision is not None else proposal.get("plan_revision")
        else:
            proposal = self._repo.create_collection_proposal(
                case_id=case_id, tenant_id=tenant_id, agent_run_id=agent_run_id,
                cycle_id=cycle_id, collector_id=collector_id,
                plan_step_id=plan_step_id, plan_revision=plan_revision,
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

        target = self._resolve_target(
            case or {}, target_selector or {},
            authorized_agent_ids=authorized_agent_ids,
        )
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
            "discovery_scope_expansion": bool(
                target is not None and target.get("scope_source") == "discovery_frontier"
            ),
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
                        # Approval is a delayed dispatch replay. Pin the safe
                        # topology lineage now so it cannot disappear or be
                        # replaced between proposal and human approval.
                        "dispatch_context": self._safe_dispatch_context(dispatch_context),
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
                    dispatch_context=dispatch_context,
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
                plan_step_id=plan_step_id, plan_revision=plan_revision,
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
                dispatch_context=dispatch_context,
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
        dispatch_context = (
            context.get("dispatch_context")
            if isinstance(context.get("dispatch_context"), dict) else {}
        )
        authorized_agent_ids: set[str] | None = None
        if dispatch_context.get("discovery_followup_authority") is True:
            authority = self.authorize_discovered_target(
                case_id=case_id,
                tenant_id=tenant_id,
                target_selector=proposal.get("target_selector") or {},
                discovery_run_id=str(
                    dispatch_context.get("discovery_run_id") or ""
                ),
                evidence_refs=[
                    str(item) for item in (
                        dispatch_context.get("discovery_authority_evidence_refs") or []
                    )
                ],
                expected_control_revision=pinned_control,
                expected_scope_revision=pinned_scope,
            )
            pinned_membership_snapshot_id = str(
                dispatch_context.get("membership_snapshot_id") or ""
            )
            if (
                pinned_membership_snapshot_id
                and pinned_membership_snapshot_id
                != str(authority.get("membership_snapshot_id") or "")
            ):
                raise ValueError(
                    "DISCOVERY_AUTHORITY_MEMBERSHIP_SNAPSHOT_CHANGED"
                )
            dispatch_context = {
                **dispatch_context,
                "expected_boot_id": authority["expected_boot_id"],
                "expected_process_start_time": authority[
                    "expected_process_start_time"
                ],
                "expected_entity_id": authority["expected_entity_id"],
            }
            authorized_agent_ids = set(
                authority.get("authorized_agent_ids") or set()
            )
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
            authorized_agent_ids=authorized_agent_ids,
            dispatch_context=dispatch_context or None,
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
        dispatch_context: dict[str, Any] | None = None,
    ) -> Any:
        collector_options = {
            key: value for key, value in effective.items()
            if key not in {"target_pid", "duration_sec", "sample_rate"}
        }
        safe_context = self._safe_dispatch_context(dispatch_context)
        return self._repo.create_task(
            CreateTaskRequest(
                name=f"agent-collection:{spec.collector_id}:{target['agent_id']}"[:120],
                agent_id=target["agent_id"], target_pid=effective["target_pid"],
                collector_type=spec.collector_id,
                sample_rate=effective["sample_rate"], duration_sec=effective["duration_sec"],
                options={
                    **collector_options,
                    "agent_id": target["agent_id"],
                    "source": "collection_supervisor", "case_id": case_id,
                    "tenant_id": tenant_id, "collection_proposal_id": proposal["proposal_id"],
                    "collection_request_id": collection_request["collection_request_id"],
                    "collector_spec_version": spec.spec_version,
                    "information_goal": information_goal,
                    "scope_revision": int(case.get("scope_revision") or 1),
                    "control_revision": int(case.get("control_revision") or 1),
                    "diagnosis_step_id": proposal.get("plan_step_id"),
                    "plan_step_id": proposal.get("plan_step_id"),
                    "plan_revision": proposal.get("plan_revision"),
                    **safe_context,
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
            if expected == "string" and rule.get("enum") and value not in set(rule["enum"]):
                errors.append(f"INVALID_PARAMETER_VALUE:{key}")
                continue
            if expected == "boolean" and not isinstance(value, bool):
                errors.append(f"INVALID_PARAMETER_TYPE:{key}")
                continue
            if expected == "array":
                if not isinstance(value, list):
                    errors.append(f"INVALID_PARAMETER_TYPE:{key}")
                    continue
                max_items = rule.get("maxItems")
                if max_items is not None and len(value) > int(max_items):
                    errors.append(f"PARAMETER_TOO_MANY_ITEMS:{key}")
                    continue
                item_rule = rule.get("items") or {}
                item_type = item_rule.get("type")
                if item_type == "integer" and any(
                    not isinstance(item, int) or isinstance(item, bool) for item in value
                ):
                    errors.append(f"INVALID_PARAMETER_ITEM_TYPE:{key}")
                    continue
                if item_type == "string" and any(not isinstance(item, str) for item in value):
                    errors.append(f"INVALID_PARAMETER_ITEM_TYPE:{key}")
                    continue
                if item_type == "integer":
                    minimum = item_rule.get("minimum")
                    maximum = item_rule.get("maximum")
                    if minimum is not None and any(item < int(minimum) for item in value):
                        errors.append(f"PARAMETER_ITEM_BELOW_MINIMUM:{key}")
                    if maximum is not None and any(item > int(maximum) for item in value):
                        errors.append(f"PARAMETER_ITEM_ABOVE_MAXIMUM:{key}")
            if isinstance(value, int):
                if "minimum" in rule and value < int(rule["minimum"]):
                    errors.append(f"PARAMETER_BELOW_MINIMUM:{key}")
                if "maximum" in rule and value > int(rule["maximum"]):
                    errors.append(f"PARAMETER_ABOVE_MAXIMUM:{key}")
            if isinstance(value, str) and rule.get("maxLength") and len(value) > int(rule["maxLength"]):
                errors.append(f"PARAMETER_TOO_LONG:{key}")
        return errors

    def _resolve_target(
        self,
        case: dict[str, Any],
        selector: dict[str, Any],
        *,
        authorized_agent_ids: set[str] | frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        requested_agent = str(selector.get("agent_id") or "")
        requested_pid = int(selector.get("target_pid") or selector.get("pid") or 0)
        instances = list((case.get("target_scope") or {}).get("instances") or [])
        allowed_targets = self.case_scoped_process_targets(case)
        allowed_agent_level = self.case_scoped_agent_level_ids(case)
        discovery_agents = {str(item) for item in (authorized_agent_ids or set()) if item}
        if requested_agent and requested_pid > 0:
            requested_pair = (requested_agent, requested_pid)
            if (
                allowed_targets
                and requested_pair not in allowed_targets
                and requested_agent not in discovery_agents
            ):
                return None
            if (
                not allowed_targets
                and allowed_agent_level
                and requested_agent not in allowed_agent_level
                and requested_agent not in discovery_agents
            ):
                return None
        elif requested_agent:
            matching_scope_targets = {
                pair for pair in allowed_targets if pair[0] == requested_agent
            }
            if (
                allowed_targets
                and len(matching_scope_targets) != 1
                and requested_agent not in discovery_agents
            ):
                return None
            if (
                not allowed_targets
                and allowed_agent_level
                and requested_agent not in allowed_agent_level
                and requested_agent not in discovery_agents
            ):
                return None
        elif allowed_targets and len(allowed_targets) != 1:
            return None
        agents = getattr(self._repo, "agents", {})
        candidates = [agents.get(requested_agent)] if requested_agent else list(agents.values())
        for agent in candidates:
            if agent is None or str(getattr(agent, "status", "ONLINE")) != "ONLINE":
                continue
            agent_id = str(getattr(agent, "id", "") or "")
            scoped_pids = sorted(pid for item_agent, pid in allowed_targets if item_agent == agent_id)
            if (
                allowed_targets
                and agent_id not in discovery_agents
                and not scoped_pids
            ):
                continue
            if (
                not allowed_targets
                and allowed_agent_level
                and agent_id not in allowed_agent_level
                and agent_id not in discovery_agents
            ):
                continue
            instance = next((item for item in instances if str(item.get("agent_id") or "") == agent_id), {})
            if requested_pid > 0:
                pid = requested_pid
            elif len(scoped_pids) == 1:
                pid = scoped_pids[0]
            else:
                continue
            in_case_scope = (
                (agent_id, pid) in allowed_targets
                or (not allowed_targets and agent_id in allowed_agent_level)
                or (not allowed_targets and not allowed_agent_level)
            )
            return {
                "agent_id": agent_id, "target_pid": pid,
                "hostname": getattr(agent, "hostname", None),
                "capabilities": set(getattr(agent, "capabilities", None) or []),
                "resource_incarnation": instance.get("resource_incarnation"),
                "scope_source": (
                    "case_scope" if in_case_scope else "discovery_frontier"
                ),
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

    @classmethod
    def _safe_dispatch_context(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        """Keep only bounded scalar lineage fields that are safe to persist/replay."""
        safe: dict[str, Any] = {}
        for key, item in (value or {}).items():
            if key not in cls._DISPATCH_CONTEXT_KEYS:
                continue
            if key == "discovery_authority_evidence_refs":
                if not isinstance(item, (list, tuple)):
                    continue
                refs = list(dict.fromkeys(
                    str(ref).strip() for ref in item if str(ref).strip()
                ))[:32]
                safe[key] = refs
                continue
            if item is None or isinstance(item, (str, int, bool)):
                safe[key] = item
        return safe

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
