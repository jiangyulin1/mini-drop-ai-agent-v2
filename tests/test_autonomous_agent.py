import pytest

from server.app.diagnosis.autonomous_agent import (
    AgentCallbacks,
    AutonomousIncidentAgent,
    _loop_state,
    _select_recovery_action,
)


def _case(phase="OBSERVING"):
    return {
        "case_id": "case-1",
        "run_mode": "AUTHORIZED_AUTONOMY",
        "state": "INVESTIGATING",
        "environment": "production",
        "row_version": 1,
        "diagnosis_session_id": "diag-1",
        "target_scope": {
            "service_id": "paymentservice",
            "orchestration": {"swarm_service": "shop_paymentservice", "replicas": 1},
            "autonomy_policy": {
                "allowed_action_ids": ["swarm.restart-stateless-service"],
                "max_auto_impact": "I2",
                "stable_verification_count": 2,
                "max_iterations": 8,
                "max_actions": 3,
            },
        },
        "recovery": {
            "agent_loop": {
                "phase": phase,
                "iteration": 1,
                "actions_executed": 0,
                "stable_verifications": 0,
                "diagnosis_id": "diag-1",
                "active_action": {},
            },
        },
    }


class Repo:
    def __init__(self, case):
        self.case = case
        self.events = []

    def get_incident_case(self, *_):
        return self.case

    def update_case_agent_loop(self, *_args, loop, event_type, detail, **_kwargs):
        self.case["recovery"] = {"agent_loop": loop.copy()}
        self.events.append((event_type, detail))
        return self.case

    def transition_incident_case(self, *_args, **_kwargs):
        self.case["state"] = "RESOLVED"
        return self.case

    def correct_incident_case(self, *_args, **_kwargs):
        self.case["diagnosis_session_id"] = None
        return self.case


class Orchestrator:
    def __init__(self, diagnosis):
        self.diagnosis = diagnosis

    def get(self, *_args, **_kwargs):
        return self.diagnosis


class Gateway:
    def __init__(self):
        self.calls = []

    def dry_run(self, action_id, parameters):
        self.calls.append(("dry", action_id, parameters))
        return {"attempt_id": f"dry-{len(self.calls)}", "dry_run": {"candidate_count": 1}}

    def execute(self, action_id, attempt_id, environment):
        self.calls.append(("execute", action_id, attempt_id, environment))
        return {"attempt_id": attempt_id, "stage": "COMPLETED", "executed": [{}]}


def _diagnosis():
    return {
        "diagnosis_id": "diag-1",
        "status": "COMPLETED",
        "latest_conclusion": {
            "cluster_assessment": {"classification": "runtime_lock_contention"},
            "evidence_review": {"quality_gate_passed": True, "conflicts": []},
        },
    }


def test_authorized_case_executes_only_registered_preauthorized_action():
    repo = Repo(_case())
    gateway = Gateway()
    agent = AutonomousIncidentAgent(
        repo, Orchestrator(_diagnosis()), gateway,
        AgentCallbacks(start_diagnosis=lambda _case: {}, verify_recovery=lambda _case, _diag: {}),
    )
    result = agent.step("case-1", "tenant")
    assert result["outcome"] == "ACTION_EXECUTED"
    assert gateway.calls[0][1] == "swarm.restart-stateless-service"
    assert repo.case["recovery"]["agent_loop"]["phase"] == "ACTION_EXECUTED"


def test_recovery_requires_two_stable_verifications_before_resolve():
    case = _case("ACTION_EXECUTED")
    case["recovery"]["agent_loop"]["active_action"] = {
        "action_id": "swarm.restart-stateless-service",
        "rollback_action_id": "swarm.rollback-service",
        "parameters": {"service_name": "shop_paymentservice"},
    }
    repo = Repo(case)
    agent = AutonomousIncidentAgent(
        repo, Orchestrator(_diagnosis()), Gateway(),
        AgentCallbacks(
            start_diagnosis=lambda _case: {},
            verify_recovery=lambda _case, _diag: {"status": "recovered"},
            verify_service_outage=lambda _case: (_ for _ in ()).throw(
                AssertionError("pre-action outage check must not run after execution")
            ),
        ),
    )
    first = agent.step("case-1", "tenant")
    assert first["outcome"] == "MONITORING"
    assert repo.case["state"] != "RESOLVED"
    second = agent.step("case-1", "tenant")
    assert second["outcome"] == "RESOLVED"
    assert repo.case["state"] == "RESOLVED"


def test_failed_recovery_verification_triggers_registered_rollback_and_reinvestigation():
    case = _case("ACTION_EXECUTED")
    case["recovery"]["agent_loop"]["actions_executed"] = 1
    case["recovery"]["agent_loop"]["active_action"] = {
        "action_id": "swarm.restart-stateless-service",
        "rollback_action_id": "swarm.rollback-service",
        "parameters": {"service_name": "shop_paymentservice"},
    }
    repo = Repo(case)
    gateway = Gateway()
    agent = AutonomousIncidentAgent(
        repo, Orchestrator(_diagnosis()), gateway,
        AgentCallbacks(
            start_diagnosis=lambda _case: {},
            verify_recovery=lambda _case, _diag: {
                "status": "not_recovered",
                "reason": "synthetic transaction still fails",
            },
        ),
    )

    result = agent.step("case-1", "tenant")

    assert result["outcome"] == "ROLLED_BACK"
    assert gateway.calls[0][1] == "swarm.rollback-service"
    assert gateway.calls[0][2]["operation_key"].endswith(":swarm.rollback-service")
    assert repo.case["diagnosis_session_id"] is None
    assert repo.case["recovery"]["agent_loop"]["phase"] == "OBSERVING"
    assert any(event[0] == "agent_reinvestigation_scheduled" for event in repo.events)


def test_confirmed_service_outage_can_use_explicit_default_action():
    case = _case()
    case["target_scope"]["autonomy_policy"]["allow_service_outage_override"] = True
    case["target_scope"]["recovery_actions"] = {
        "default": {
            "action_id": "swarm.restart-stateless-service",
            "parameters": {"service_name": "shop_paymentservice"},
            "healthy_replicas_after_action": 1,
            "rollback_action_id": "swarm.rollback-service",
        },
    }
    case["target_scope"]["verification"] = {
        "http_checks": [{"url": "http://shop/checkout"}],
    }
    diagnosis = _diagnosis()
    diagnosis["status"] = "INSUFFICIENT_EVIDENCE"
    gateway = Gateway()
    repo = Repo(case)
    agent = AutonomousIncidentAgent(
        repo, Orchestrator(diagnosis), gateway,
        AgentCallbacks(
            start_diagnosis=lambda _case: {},
            verify_recovery=lambda _case, _diag: {"status": "recovered"},
            verify_service_outage=lambda _case: {"status": "not_recovered"},
        ),
    )

    result = agent.step("case-1", "tenant")

    assert result["outcome"] == "ACTION_EXECUTED"
    assert any(event[0] == "agent_service_outage_confirmed" for event in repo.events)


def test_loop_state_reads_persisted_case_summary_envelope():
    case = {"summary": {"recovery": {"agent_loop": {
        "phase": "MONITORING",
        "iteration": 3,
        "pending_action": {"action_id": "swarm.restart-stateless-service"},
    }}}}

    loop = _loop_state(case)

    assert loop["phase"] == "MONITORING"
    assert loop["iteration"] == 3
    assert loop["pending_action"]["action_id"] == "swarm.restart-stateless-service"


def test_action_dispatch_reuses_operation_key_after_control_crash():
    class CrashGateway(Gateway):
        def dry_run(self, action_id, parameters):
            self.calls.append(("dry", action_id, parameters))
            raise SystemExit("simulated control stop")

    repo = Repo(_case())
    crashed = CrashGateway()
    agent = AutonomousIncidentAgent(
        repo, Orchestrator(_diagnosis()), crashed,
        AgentCallbacks(start_diagnosis=lambda _case: {}, verify_recovery=lambda _case, _diag: {}),
    )

    with pytest.raises(SystemExit, match="simulated control stop"):
        agent.step("case-1", "tenant")

    saved = repo.case["recovery"]["agent_loop"]
    assert saved["phase"] == "ACTION_DISPATCHING"
    first_key = saved["pending_action"]["operation_key"]

    resumed = Gateway()
    result = AutonomousIncidentAgent(
        repo, Orchestrator(_diagnosis()), resumed,
        AgentCallbacks(start_diagnosis=lambda _case: {}, verify_recovery=lambda _case, _diag: {}),
    ).step("case-1", "tenant")

    assert result["outcome"] == "ACTION_EXECUTED"
    assert resumed.calls[0][2]["operation_key"] == first_key
    assert repo.case["recovery"]["agent_loop"]["active_action"]["operation_key"] == first_key


def test_default_recovery_is_not_used_for_unrelated_diagnosis():
    case = _case()
    case["target_scope"]["recovery_actions"] = {
        "default": {
            "action_id": "swarm.restart-stateless-service",
            "parameters": {"service_name": "shop_paymentservice"},
        },
    }
    conclusion = {"cluster_assessment": {"classification": "filesystem_exhaustion"}}

    assert _select_recovery_action(case, conclusion) is None
    assert _select_recovery_action(case, conclusion, allow_default=True)["action_id"] == (
        "swarm.restart-stateless-service"
    )


def test_network_restart_requires_explicit_classification_opt_in():
    case = _case()
    conclusion = {"cluster_assessment": {"classification": "network_degradation"}}

    assert _select_recovery_action(case, conclusion) is None
    case["target_scope"]["orchestration"]["restartable_classifications"] = ["network_degradation"]
    assert _select_recovery_action(case, conclusion)["action_id"] == "swarm.restart-stateless-service"


@pytest.mark.parametrize("classification", [
    "process_oom",
    "runtime_lock_contention",
    "runtime_stall",
])
def test_stateless_runtime_failures_use_bounded_rolling_restart(classification):
    action = _select_recovery_action(
        _case(), {"cluster_assessment": {"classification": classification}},
    )

    assert action["action_id"] == "swarm.restart-stateless-service"
    assert action["rollback_action_id"] == "swarm.rollback-service"


def test_filesystem_recovery_requires_exact_registered_action():
    case = _case()
    case["target_scope"]["recovery_actions"] = {
        "filesystem_exhaustion": {
            "action_id": "mini-drop.cleanup-expired-cache",
            "parameters": {"retention_days": 7},
            "rollback_action_id": "mini-drop.restore-cache-quarantine",
        },
    }

    action = _select_recovery_action(
        case, {"cluster_assessment": {"classification": "filesystem_exhaustion"}},
    )

    assert action["action_id"] == "mini-drop.cleanup-expired-cache"


def test_compound_incident_does_not_guess_one_repair():
    action = _select_recovery_action(
        _case(), {"cluster_assessment": {"classification": "compound_incident"}},
    )

    assert action is None
