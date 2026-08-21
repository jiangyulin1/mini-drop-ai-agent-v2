"""network_discovery authority, parameter transport and replay regression tests."""

from __future__ import annotations

import pytest

from agent.mini_drop_agent.config import AgentConfig
from agent.mini_drop_agent.main import _heartbeat
from mini_drop_contracts import get_collector_spec
from server.app.database import init_db, reset_engine
from server.app.diagnosis.collection_supervisor import CollectionSupervisor
from server.app.grpc_services.healthcheck_service import HealthCheckService
from server.app.main import repo
from server.app.models import Base
from server.app.routes.topology_discovery import _target_is_in_case_scope


TENANT_ID = "tenant-a"
AGENT_ID = "network-agent"
AGENT_IP = "10.0.0.10"
SEED_PID = 4321


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_API_TENANT_ID", TENANT_ID)
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _case_and_supervisor() -> tuple[dict, CollectionSupervisor]:
    repo.register_agent(
        AGENT_ID,
        "network-host",
        AGENT_IP,
        version="0.4.0",
        capabilities=["network_discovery", "sys_metrics"],
    )
    case = repo.create_incident_case({
        "tenant_id": TENANT_ID,
        "created_by": "test-user",
        "title": "unknown topology dispatch",
        "problem_description": "seed process has an unknown downstream",
        "recovery_goal": "resolve the managed one-hop TCP peer",
        "run_mode": "COLLABORATE",
        "environment": "test",
        "target_scope": {
            "service_id": "seed-service",
            "instances": [{
                "service_id": "seed-service",
                "instance_id": "seed-1",
                "host_id": "network-host",
                "agent_id": AGENT_ID,
                "pid": SEED_PID,
                "environment": "test",
            }],
        },
    })
    return case, CollectionSupervisor(repo)


def _parameters() -> dict:
    return {
        "scope": "target",
        "listener_ports": [8080, 50051],
        "include_listeners": True,
        "include_connections": True,
        "include_loopback": False,
        "max_processes": 120,
        "max_sockets": 240,
        "max_events": 300,
        "duration_sec": 2,
        "sample_rate": 1,
    }


def _proposal_kwargs(case: dict, **overrides) -> dict:
    spec = get_collector_spec("network_discovery")
    assert spec is not None
    values = {
        "case_id": case["case_id"],
        "tenant_id": TENANT_ID,
        "collector_id": "network_discovery",
        "target_selector": {"agent_id": AGENT_ID, "target_pid": SEED_PID},
        "parameters": _parameters(),
        "information_goal": spec.information_goals[0],
        "reason_summary": "resolve one-hop dependency from an authorized seed PID",
        "expected_control_revision": case["control_revision"],
        "expected_scope_revision": case["scope_revision"],
        "allowed_risk_levels": {"R0", "R1"},
    }
    values.update(overrides)
    return values


class _DirectHealthStub:
    def __init__(self):
        self._service = HealthCheckService(repo)

    def Do(self, request, timeout):
        assert timeout == 5
        return self._service.Do(request, None)


def test_collection_supervisor_to_agent_preserves_network_discovery_parameters():
    case, supervisor = _case_and_supervisor()
    result = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        idempotency_key="network-parameter-transport",
        dispatch_context={
            "discovery_run_id": "dfr-1",
            "discovery_hop": 0,
            "discovery_phase": "seed",
        },
    )

    assert result["proposal"]["status"] == "ACCEPTED"
    assert result["collection_request"]["status"] == "DISPATCHED"
    task = result["task"]
    assert task.collector_type == "network_discovery"
    assert task.target_pid == SEED_PID
    assert task.duration_sec == 2
    assert task.sample_rate == 1
    task_options = task.request_params["options"]
    for key, value in _parameters().items():
        if key not in {"duration_sec", "sample_rate"}:
            assert task_options[key] == value
    assert task_options["discovery_run_id"] == "dfr-1"
    assert task_options["discovery_hop"] == 0
    assert task_options["discovery_phase"] == "seed"

    # Exercise the actual gRPC task encoding and Agent heartbeat decoder. The
    # protobuf enum does not know this new collector, so _collector_type must
    # restore the exact implementation while every bounded option survives.
    decoded = _heartbeat(
        _DirectHealthStub(),
        AgentConfig(
            agent_id=AGENT_ID,
            server_grpc_addr="127.0.0.1:50051",
            agent_ip_addr=AGENT_IP,
        ),
    )
    assert decoded is not None
    assert decoded["collector_type"] == "network_discovery"
    assert decoded["target_pid"] == SEED_PID
    decoded_options = decoded["request_params"]["options"]
    for key, value in task_options.items():
        assert decoded_options[key] == value
    assert "_collector_type" not in decoded_options


def test_approved_network_discovery_replays_pinned_context_and_deduplicates():
    case, supervisor = _case_and_supervisor()
    supplied_key = "network-human-approved"
    proposed = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        idempotency_key=supplied_key,
        auto_dispatch=False,
        dispatch_context={
            "discovery_run_id": "dfr-approved",
            "discovery_hop": 1,
            "discovery_phase": "remote_listener_resolution",
            "discovery_parent_task_id": "task-seed",
            "discovery_authority_evidence_ref": "ev-authority",
            "membership_snapshot_id": "membership-1",
            "untrusted_context": "must-not-persist",
        },
    )
    proposal = proposed["proposal"]
    assert proposal["status"] == "PROPOSED"
    assert proposed["task"] is None
    pinned = proposal["validation_result"]["approval_context"]["dispatch_context"]
    assert pinned == {
        "discovery_run_id": "dfr-approved",
        "discovery_hop": 1,
        "discovery_phase": "remote_listener_resolution",
        "discovery_parent_task_id": "task-seed",
        "discovery_authority_evidence_ref": "ev-authority",
        "membership_snapshot_id": "membership-1",
    }

    approved = supervisor.decide_pending_proposal(
        proposal_id=proposal["proposal_id"],
        case_id=case["case_id"],
        tenant_id=TENANT_ID,
        decision="APPROVE",
        decided_by="human-reviewer",
        reason="bounded read-only discovery approved",
        expected_control_revision=case["control_revision"],
        expected_scope_revision=case["scope_revision"],
    )
    first_task = approved["task"]
    options = first_task.request_params["options"]
    for key, value in pinned.items():
        assert options[key] == value
    assert "untrusted_context" not in options
    assert approved["proposal"]["validation_result"]["approval_decision"] == "APPROVE"

    replay = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        idempotency_key=supplied_key,
        # A replay cannot replace the authority context of the already-created
        # request/task even if a caller supplies different lineage.
        dispatch_context={"discovery_run_id": "must-not-replace"},
    )
    assert replay["task"].id == first_task.id
    assert replay["collection_request"]["collection_request_id"] == approved["collection_request"]["collection_request_id"]
    assert replay["proposal"]["validation_result"]["duplicate"] is True
    assert replay["proposal"]["validation_result"]["budget_consumed"] is False
    assert replay["task"].request_params["options"]["discovery_run_id"] == "dfr-approved"
    assert len(repo.list_collection_requests(case["case_id"], TENANT_ID)) == 1


def test_collection_supervisor_rejects_unknown_network_discovery_scope():
    case, supervisor = _case_and_supervisor()
    invalid = _parameters()
    invalid["scope"] = "cluster-wide-unbounded"
    result = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case, parameters=invalid),
    )

    assert result["proposal"]["status"] == "REJECTED"
    assert "INVALID_PARAMETER_VALUE:scope" in result["proposal"]["validation_result"]["errors"]
    assert result["collection_request"] is None
    assert result["task"] is None


def test_topology_frontier_treats_same_agent_new_pid_as_run_scoped_target():
    case, _supervisor = _case_and_supervisor()

    assert _target_is_in_case_scope(case, AGENT_ID, SEED_PID) is True
    assert _target_is_in_case_scope(case, AGENT_ID, SEED_PID + 1) is False
