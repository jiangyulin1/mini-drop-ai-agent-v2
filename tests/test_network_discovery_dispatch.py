"""network_discovery authority, parameter transport and replay regression tests."""

from __future__ import annotations

import pytest

from agent.mini_drop_agent.config import AgentConfig
from agent.mini_drop_agent.main import _heartbeat
from mini_drop_contracts import get_collector_spec
from server.app.database import init_db, reset_engine
from server.app.diagnosis.case_evidence import stable_projection_hash
from server.app.diagnosis.collection_supervisor import CollectionSupervisor
from server.app.diagnosis.collection_reuse import result_fingerprint
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


@pytest.mark.parametrize(("collector_id", "requested", "expected_index"), [
    ("runtime_snapshot", "识别运行时类型、线程状态、锁等待/futex/park，判断是否阻塞", 1),
    ("process_scan", "确认目标 PID 身份、命令、CPU 和内存，并检查同机竞争", 0),
    ("sys_metrics", "获取 CPU、负载、线程、FD、网络和 I/O 基线", 1),
    ("log_scan", "读取近期日志，提取错误、警告、超时和时间线", 1),
])
def test_collector_goal_accepts_agent_paraphrase(collector_id, requested, expected_index):
    spec = get_collector_spec(collector_id)
    assert spec is not None
    canonical = CollectionSupervisor._canonical_information_goal(spec, requested)
    assert canonical == spec.information_goals[expected_index]


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


def test_collection_supervisor_rejects_malformed_target_without_raising():
    case, supervisor = _case_and_supervisor()
    result = supervisor.propose_and_dispatch(
        **_proposal_kwargs(
            case,
            target_selector={"agent_id": AGENT_ID, "target_pid": "not-a-pid"},
        ),
    )
    assert result["proposal"]["status"] == "REJECTED"
    assert "INVALID_PARAMETER_TYPE:target_pid" in result["proposal"][
        "validation_result"
    ]["errors"]
    assert result["task"] is None


def test_collection_supervisor_rejects_conflicting_pid_selector_aliases():
    case, supervisor = _case_and_supervisor()
    result = supervisor.propose_and_dispatch(
        **_proposal_kwargs(
            case,
            target_selector={
                "agent_id": AGENT_ID,
                "target_pid": SEED_PID,
                "pid": SEED_PID + 1,
            },
        ),
    )
    assert result["proposal"]["status"] == "REJECTED"
    assert "TARGET_PID_ALIAS_CONFLICT" in result["proposal"][
        "validation_result"
    ]["errors"]
    assert result["task"] is None


def test_discovery_incarnation_context_conflict_is_explicitly_rejected():
    case, supervisor = _case_and_supervisor()
    kwargs = _proposal_kwargs(
        case,
        dispatch_context={"expected_boot_id": "boot-new"},
        target_selector={
            "agent_id": AGENT_ID,
            "target_pid": SEED_PID,
            "boot_id": "boot-old",
        },
    )
    result = supervisor.propose_and_dispatch(
        **kwargs,
    )
    assert result["proposal"]["status"] == "REJECTED"
    assert "TARGET_INCARNATION_MISMATCH" in result["proposal"][
        "validation_result"
    ]["errors"]
    assert result["task"] is None


def test_discovery_target_entity_alias_conflict_is_explicitly_rejected():
    case, supervisor = _case_and_supervisor()
    kwargs = _proposal_kwargs(
        case,
        dispatch_context={"expected_entity_id": "entity-new"},
        target_selector={
            "agent_id": AGENT_ID,
            "target_pid": SEED_PID,
            "target_entity_id": "entity-old",
        },
    )
    result = supervisor.propose_and_dispatch(**kwargs)
    assert result["proposal"]["status"] == "REJECTED"
    assert "TARGET_INCARNATION_MISMATCH" in result["proposal"][
        "validation_result"
    ]["errors"]


def test_topology_frontier_treats_same_agent_new_pid_as_run_scoped_target():
    case, _supervisor = _case_and_supervisor()

    assert _target_is_in_case_scope(case, AGENT_ID, SEED_PID) is True
    assert _target_is_in_case_scope(case, AGENT_ID, SEED_PID + 1) is False


def _completed_reuse_candidate(
    case: dict, supervisor: CollectionSupervisor,
) -> tuple[dict, dict]:
    first = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        idempotency_key="reuse-seed-request",
    )
    assert first["proposal"]["status"] == "ACCEPTED"
    task = first["task"]
    request = first["collection_request"]
    supervisor.mark_task_terminal(case["case_id"], TENANT_ID, task.id, "DONE")
    probe = task.request_params["options"]["probe_fingerprint"]
    projection_content = {"summary": "reuse candidate"}
    projection_hash = stable_projection_hash(projection_content)
    result = result_fingerprint(
        probe_fingerprint_value=probe,
        projection_hash=projection_hash,
        content_hash="content-reuse-network",
        artifact_schema="network_discovery",
        parser_version="deterministic.v1",
        completeness="COMPLETE",
    )
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id=TENANT_ID,
        evidence_id="ev-reuse-network", attachment_id=None,
        task_id=task.id, artifact_id=1,
        artifact_type="network_discovery", collector_id="network_discovery",
        source_type="task_artifact", target_ref=f"task:{task.id}",
        content_hash="content-reuse-network", projection_hash=projection_hash,
        lineage={
            "task_id": task.id,
            "probe_fingerprint": probe,
            "result_fingerprint": result,
        },
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-reuse-network", case_id=case["case_id"], tenant_id=TENANT_ID,
        projection_kind="SUMMARY", projection_version=1, content=projection_content,
        parser_version="deterministic.v1",
    )
    return request, first


def test_exact_collection_reuse_returns_existing_request_without_new_task():
    case, supervisor = _case_and_supervisor()
    request, first = _completed_reuse_candidate(case, supervisor)
    task_count = len(repo.tasks)
    request_count = len(repo.list_collection_requests(case["case_id"], TENANT_ID))

    reused = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        reuse_existing_request_id=request["collection_request_id"],
    )

    assert reused["proposal"]["status"] == "ACCEPTED"
    assert reused["proposal"]["validation_result"]["reused"] is True
    assert reused["collection_request"]["collection_request_id"] == request["collection_request_id"]
    assert reused["task"] is None
    assert len(repo.tasks) == task_count
    assert len(repo.list_collection_requests(case["case_id"], TENANT_ID)) == request_count
    assert reused["reuse"]["items"][0]["evidence_id"] == "ev-reuse-network"
    assert first["task"].id == reused["collection_request"]["task_id"]


def test_multiple_evidence_results_require_explicit_selection():
    case, supervisor = _case_and_supervisor()
    request, first = _completed_reuse_candidate(case, supervisor)
    task = first["task"]
    projection_content = {"summary": "second projection"}
    projection_hash = stable_projection_hash(projection_content)
    probe = task.request_params["options"]["probe_fingerprint"]
    result = result_fingerprint(
        probe_fingerprint_value=probe,
        projection_hash=projection_hash,
        content_hash="content-reuse-network-2",
        artifact_schema="network_discovery",
        parser_version="deterministic.v1",
        completeness="COMPLETE",
    )
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id=TENANT_ID,
        evidence_id="ev-reuse-network-2", attachment_id=None,
        task_id=task.id, artifact_id=2, artifact_type="network_discovery",
        collector_id="network_discovery", source_type="task_artifact",
        target_ref=f"task:{task.id}", content_hash="content-reuse-network-2",
        projection_hash=projection_hash,
        lineage={
            "task_id": task.id,
            "probe_fingerprint": probe,
            "probe_key": task.request_params["options"].get("probe_key"),
            "result_fingerprint": result,
        },
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-reuse-network-2", case_id=case["case_id"],
        tenant_id=TENANT_ID, projection_kind="SUMMARY", projection_version=1,
        content=projection_content, parser_version="deterministic.v1",
    )

    ambiguous = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        reuse_existing_request_id=request["collection_request_id"],
    )
    assert ambiguous["proposal"]["status"] == "REJECTED"
    assert ambiguous["proposal"]["validation_result"]["errors"] == [
        "REUSE_EVIDENCE_SELECTION_REQUIRED"
    ]

    selected = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        reuse_existing_request_id=request["collection_request_id"],
        reuse_existing_evidence_id="ev-reuse-network-2",
    )
    assert selected["proposal"]["status"] == "ACCEPTED"
    assert selected["reuse"]["items"][0]["evidence_id"] == "ev-reuse-network-2"
    assert selected["task"] is None


def test_explicit_reuse_preserves_discovery_target_authority_context():
    """A discovered Agent remains resolvable during the reuse query.

    The Case scope contains only the seed Agent.  The remote target is legal
    solely because the caller supplies the discovery authority grant.  The
    reuse lookup must receive that same grant; otherwise it would silently
    report ``PROBE_NOT_RESOLVABLE`` and trigger an unnecessary recollection.
    """
    case, supervisor = _case_and_supervisor()
    remote_id = "discovered-agent"
    remote_pid = 7777
    repo.register_agent(
        remote_id,
        "discovered-host",
        "10.0.0.77",
        version="0.4.0",
        capabilities=["network_discovery"],
    )
    kwargs = _proposal_kwargs(
        case,
        target_selector={"agent_id": remote_id, "target_pid": remote_pid},
        authorized_agent_ids={remote_id},
        dispatch_context={
            "expected_boot_id": "boot-discovered",
            "expected_process_start_time": "1777000",
            "expected_entity_id": "entity-discovered",
        },
        idempotency_key="reuse-discovered-target",
    )
    first = supervisor.propose_and_dispatch(**kwargs)
    assert first["proposal"]["status"] == "ACCEPTED"
    task = first["task"]
    request = first["collection_request"]
    supervisor.mark_task_terminal(case["case_id"], TENANT_ID, task.id, "DONE")

    # A completed Evidence with a verifiable projection is the explicit reuse
    # material, matching the helper used by the ordinary seed-path test.
    probe = task.request_params["options"]["probe_fingerprint"]
    projection_content = {"summary": "discovered reuse candidate"}
    projection_hash = stable_projection_hash(projection_content)
    result = result_fingerprint(
        probe_fingerprint_value=probe,
        projection_hash=projection_hash,
        content_hash="content-reuse-discovered",
        artifact_schema="network_discovery",
        parser_version="deterministic.v1",
        completeness="COMPLETE",
    )
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id=TENANT_ID,
        evidence_id="ev-reuse-discovered", attachment_id=None,
        task_id=task.id, artifact_id=2,
        artifact_type="network_discovery", collector_id="network_discovery",
        source_type="task_artifact", target_ref=f"task:{task.id}",
        content_hash="content-reuse-discovered", projection_hash=projection_hash,
        lineage={"task_id": task.id, "probe_fingerprint": probe,
                 "result_fingerprint": result},
    )
    repo.upsert_evidence_projection(
        evidence_id="ev-reuse-discovered", case_id=case["case_id"],
        tenant_id=TENANT_ID, projection_kind="SUMMARY", projection_version=1,
        content=projection_content, parser_version="deterministic.v1",
    )

    reused = supervisor.propose_and_dispatch(
        **{**kwargs, "reuse_existing_request_id": request["collection_request_id"]},
    )
    assert reused["proposal"]["status"] == "ACCEPTED"
    assert reused["task"] is None
    assert reused["reuse"]["items"][0]["evidence_id"] == "ev-reuse-discovered"


def test_collection_reuse_rejects_probe_fingerprint_mismatch_without_dispatch():
    case, supervisor = _case_and_supervisor()
    request, _first = _completed_reuse_candidate(case, supervisor)
    task_count = len(repo.tasks)

    changed = _parameters()
    changed["max_sockets"] += 1
    rejected = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case, parameters=changed),
        reuse_existing_request_id=request["collection_request_id"],
    )

    assert rejected["proposal"]["status"] == "REJECTED"
    assert rejected["proposal"]["validation_result"]["errors"] == [
        "REUSE_CANDIDATE_NOT_ELIGIBLE"
    ]
    assert any(
        "PROBE_FINGERPRINT_MISMATCH" in item["reason_codes"]
        for item in rejected["reuse"]["rejected"]
    )
    assert len(repo.tasks) == task_count
    assert len(repo.list_collection_requests(case["case_id"], TENANT_ID)) == 1


def test_low_trust_collection_reuse_requires_explicit_opt_in():
    case, supervisor = _case_and_supervisor()
    request, _first = _completed_reuse_candidate(case, supervisor)
    repo.add_evidence_review_revision(
        evidence_id="ev-reuse-network", case_id=case["case_id"],
        tenant_id=TENANT_ID, decision="LOW_TRUST", reviewed_by="operator",
        reason="short observation window",
    )
    task_count = len(repo.tasks)

    rejected = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        reuse_existing_request_id=request["collection_request_id"],
    )

    assert rejected["proposal"]["status"] == "REJECTED"
    assert any(
        "EVIDENCE_LOW_TRUST_REQUIRES_EXPLICIT_REVIEW" in item["reason_codes"]
        for item in rejected["reuse"]["rejected"]
    )
    assert len(repo.tasks) == task_count

    explicitly_allowed = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        reuse_existing_request_id=request["collection_request_id"],
        allow_low_trust_reuse=True,
    )
    assert explicitly_allowed["proposal"]["status"] == "ACCEPTED"
    assert explicitly_allowed["proposal"]["validation_result"]["allow_low_trust_reuse"] is True
    assert len(repo.tasks) == task_count


def test_pending_collection_is_fenced_when_input_evidence_review_changes():
    case, supervisor = _case_and_supervisor()
    repo.upsert_case_evidence(
        case_id=case["case_id"], tenant_id=TENANT_ID,
        evidence_id="ev-input-fence", attachment_id=None, task_id="task-input",
        artifact_id=1, artifact_type="summary", collector_id="sys_metrics",
        source_type="task_artifact", target_ref="task:task-input",
        content_hash="content-input", projection_hash="projection-input",
    )
    proposed = supervisor.propose_and_dispatch(
        **_proposal_kwargs(case),
        input_evidence_refs=["ev-input-fence"],
        auto_dispatch=False,
    )
    proposal = proposed["proposal"]
    pinned = proposal["validation_result"]["input_evidence_review_revisions"]
    assert pinned["ev-input-fence"]["review_revision"] == 0
    assert proposal["validation_result"]["approval_context"][
        "input_evidence_review_revisions"
    ] == pinned

    repo.add_evidence_review_revision(
        evidence_id="ev-input-fence", case_id=case["case_id"],
        tenant_id=TENANT_ID, decision="LOW_TRUST", reviewed_by="operator",
        reason="new review arrived while approval was pending",
    )
    with pytest.raises(ValueError, match="COLLECTION_PROPOSAL_FENCED:INPUT_EVIDENCE_REVIEW_CHANGED:ev-input-fence"):
        supervisor.decide_pending_proposal(
            proposal_id=proposal["proposal_id"], case_id=case["case_id"],
            tenant_id=TENANT_ID, decision="APPROVE", decided_by="operator",
            expected_control_revision=case["control_revision"],
            expected_scope_revision=case["scope_revision"],
        )
    assert not repo.list_collection_requests(case["case_id"], TENANT_ID)
    assert not repo.tasks
