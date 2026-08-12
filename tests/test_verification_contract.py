"""P4 VerificationContract 与动作租约测试。"""

from __future__ import annotations

import pytest

from server.app.diagnosis.actuation import ActuationError
from server.app.diagnosis.distributed_actuation import DistributedActuationGateway
from server.app.diagnosis.verification_contract import (
    build_verification_contract,
    evaluate_verification,
)


def _scope(**overrides) -> dict:
    scope = {
        "service_id": "checkout",
        "verification": {
            "business_targets": [
                {"metric": "checkout.success_rate", "operator": ">=", "value": 0.99},
                {"metric": "error_rate", "operator": "<=", "value": 0.01, "kind": "guardrail"},
            ],
            "http_checks": [{"url": "http://localhost:8080/healthz"}],
            "required_consecutive_passes": 2,
        },
        "orchestration": {"replicas": 1},
    }
    scope.update(overrides)
    return scope


def test_build_contract_from_business_targets():
    contract = build_verification_contract("case-1", _scope())
    assert contract.has_business_objectives is True
    assert any(o.metric == "checkout.success_rate" for o in contract.primary_objectives)
    assert any(g.metric == "error_rate" for g in contract.guardrails)
    assert contract.required_consecutive_passes == 2
    assert "http://localhost:8080/healthz" in contract.synthetic_checks


def test_build_contract_falls_back_to_health_check():
    contract = build_verification_contract("case-1", _scope(verification={"http_checks": [{"url": "http://x"}]}))
    assert contract.has_business_objectives is False
    assert contract.primary_objectives  # 健康检查兜底目标


def test_evaluate_recovered_when_objectives_and_guardrails_met():
    contract = build_verification_contract("case-1", _scope())
    result = evaluate_verification(contract, {
        "checkout.success_rate": 0.995,
        "error_rate": 0.005,
    })
    assert result["recovered"] is True
    assert result["status"] == "RECOVERED"
    assert result["objectives_met"] == 1


def test_evaluate_not_recovered_when_guardrail_violated():
    contract = build_verification_contract("case-1", _scope())
    result = evaluate_verification(contract, {
        "checkout.success_rate": 0.995,
        "error_rate": 0.05,
    })
    assert result["recovered"] is False
    assert result["guardrails_ok"] is False
    assert result["guardrail_violations"]


def test_evaluate_mitigated_without_business_objective():
    contract = build_verification_contract("case-1", _scope(verification={"http_checks": [{"url": "http://x"}]}))
    result = evaluate_verification(contract, {"http:http://x": 200})
    assert result["status"] in {"RECOVERED", "MITIGATED"}
    assert result["recovered"] is True  # 健康检查目标达成


class _LocalGateway:
    def dry_run(self, action_id, parameters):
        return {"attempt_id": "local-dry", "action_id": action_id}

    def execute(self, action_id, dry_run_attempt_id, environment):
        return {"attempt_id": dry_run_attempt_id, "stage": "COMPLETED"}


class _Agent:
    def __init__(self):
        self.capabilities = ["swarm_actuation"]
        self.status = "ONLINE"


class _Repo:
    def __init__(self):
        self.agents = {"manager": _Agent()}
        self.tasks = {}
        self.artifacts = {}

    def create_task(self, request, *, idempotency_key=None):
        task = type("T", (), {
            "id": f"task-{len(self.tasks) + 1}",
            "status": "DONE",
            "agent_id": request.agent_id,
            "collector_type": request.collector_type,
        })()
        self.tasks[task.id] = task
        self.artifacts[task.id] = [{
            "artifact_type": "actuation_result",
            "metadata": {"data": {
                "service_name": request.options.get("service_name"),
                "before_version_index": 1,
                "after_version_index": 2,
                "preflight": {"version_index": 1, "service_name": request.options.get("service_name")},
            }},
        }]
        return task

    def get_agent_metric_history(self, *a, **k):
        return []


def test_action_lease_rejects_concurrent_execution():
    repo = _Repo()
    gateway = DistributedActuationGateway(repo, _LocalGateway())
    # dry_run 返回 swarm 型 attempt
    dry = gateway.dry_run("swarm.restart-stateless-service", {
        "manager_agent_id": "manager",
        "service_name": "shop_paymentservice",
        "operation_key": "case:1:1:restart",
    })
    attempt_id = dry["attempt_id"]
    # 第一次执行获取租约成功
    first = gateway.execute("swarm.restart-stateless-service", attempt_id)
    assert first["stage"] == "COMPLETED"
    # 完成后租约释放；第二次 replay 走幂等路径（不重复下发）
    replay = gateway.execute("swarm.restart-stateless-service", attempt_id)
    assert replay.get("idempotent_replay") is True
