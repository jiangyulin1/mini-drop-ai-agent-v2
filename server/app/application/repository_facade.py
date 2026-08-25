"""Frozen compatibility application facade for the legacy aggregate repository.

The facade is intentionally restrictive.  Existing adapters may call the
frozen surface while domain-specific application services are extracted, but a
new direct repository call fails immediately and must be modelled on a domain
port/application service instead.
"""

from __future__ import annotations

from typing import Any


FROZEN_REPOSITORY_SURFACE = frozenset({
    "add_artifacts", "add_assistant_message", "agent_metrics", "agents", "analysis_health",
    "add_runtime_wakeup_source", "claim_domain_outbox",
    "append_case_message", "artifacts", "as_dict", "attach_case_diagnosis",
    "acquire_case_lease_token",
    "audit_logs", "cancel_task", "consume_runtime_wakeup",
    "correct_incident_case", "count_incident_cases", "create_agent_cycle",
    "create_analysis_job", "create_authorization_grant",
    "create_case_context_snapshot", "create_case_for_target_signal",
    "create_case_recovery_plan", "create_change_record", "create_context_packet",
    "create_incident_case", "create_investigation_iteration",
    "create_investigation_run", "create_membership_snapshot",
    "create_investigation_tree_node", "add_investigation_tree_dependency",
    "create_model_request", "create_runtime_wakeup", "create_target_session",
    "create_task", "delete_task", "enqueue_domain_outbox", "events",
    "fail_domain_outbox", "find_agent_by_ip", "finish_attempt", "finalize_investigation_result",
    "get_agent_runtime_binding", "get_agent_runtime_branch_binding", "get_agent_runtime_turn", "get_case_event_high_water",
    "get_agent_cycle", "get_attempt", "get_case_evidence", "get_case_hypothesis_graph",
    "get_case_recovery_plan", "get_causal_graph", "get_conclusion",
    "get_collection_proposal", "get_diagnosis", "get_fanout_run", "get_incident_case",
    "get_investigation_plan", "get_investigation_run", "get_investigation_tree_node", "get_membership_snapshot",
    "get_runtime_wakeup_by_outbox", "get_target_session", "get_task_by_diagnosis_step_id", "heartbeat",
    "heartbeat_only", "index_profile_task", "invalidate_cache",
    "list_agent_runtime_events", "list_agent_runtime_turns",
    "list_assistant_messages", "list_authorization_grants", "list_case_events",
    "list_case_evidence", "list_case_recovery_plans", "list_change_records",
    "list_context_packets", "list_diagnoses_for_task", "list_diagnosis_history",
    "list_collection_proposals", "list_collection_requests",
    "list_evidence_analysis_runs", "list_evidence_gaps", "list_evidence_projections", "list_evidence_review_revisions", "list_execution_units",
    "list_evidence_dependency_edges", "list_conclusion_revisions", "promote_case_evidence", "propose_evidence_dependency",
    "build_confidence_chain_impact", "save_confidence_snapshot", "list_confidence_snapshots",
    "list_confidence_adjustments", "record_confidence_adjustment",
    "submit_causal_graph_revision",
    "list_fanout_runs", "list_incident_cases", "list_investigation_iterations",
    "list_investigation_runs", "list_investigation_tree", "list_investigation_tree_events",
    "list_model_attempts", "list_operation_specs",
    "list_profile_windows", "list_repair_recommendations", "list_runtime_wakeups",
    "list_domain_outbox", "list_system_controls", "list_target_sessions", "list_target_signals",
    "list_task_analysis_jobs", "list_task_attempts", "mark_incident_case_investigating", "mark_offline_agents",
    "persist_agent_metric_snapshots", "persist_case_conclusion",
    "record_agent_metrics", "record_agent_runtime_event", "record_outbox_consumer_effect",
    "record_agent_runtime_turn", "record_audit", "record_case_event",
    "record_model_attempt", "record_rca_feedback", "record_target_signal",
    "record_task_retry", "reclaim_expired_outbox", "recover_dead_outbox", "requeue_runtime_wakeup",
    "recover_stale_tasks", "register_agent", "set_agent_collection_enabled",
    "revoke_authorization_grant", "seal_runtime_wakeup", "set_system_control",
    "mark_outbox_delivered",
    "should_cancel_attempt", "submit_conclusion_revision", "complete_agent_runtime_turn",
    "sync_case_hypothesis_graph", "tasks", "transition_agent_cycle",
    "transition_investigation_tree_node", "invalidate_investigation_tree_for_evidence",
    "transition_case_recovery_plan", "transition_incident_case",
    "transition_model_request", "transition_target_session", "transition_task",
    "attach_evidence_analysis_turn", "update_case_instance_pid", "update_plan_step", "upsert_agent_runtime_binding", "upsert_agent_runtime_branch_binding",
})


class RepositoryApplicationFacade:
    """One-call application boundary over the frozen repository contract."""

    __slots__ = ("_repository",)

    def __init__(self, repository: Any) -> None:
        object.__setattr__(self, "_repository", repository)

    def __getattr__(self, name: str) -> Any:
        if name not in FROZEN_REPOSITORY_SURFACE:
            raise AttributeError(
                f"repository compatibility surface is frozen; model {name!r} "
                "on a domain application service",
            )
        return getattr(self._repository, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("repository compatibility facade is immutable")
