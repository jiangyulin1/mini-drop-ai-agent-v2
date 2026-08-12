import pytest

from server.app.diagnosis import actuation
from server.app.diagnosis.actuation import ActuationAttempt, ActuationError


def _service(version=7):
    return {
        "ID": "svc-id",
        "Version": {"Index": version},
        "Spec": {
            "Labels": {"mini-drop.autonomy": "true", "mini-drop.stateless": "true"},
            "Mode": {"Replicated": {"Replicas": 1}},
        },
    }


def test_swarm_restart_requires_allowlist_and_labels(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AUTONOMY_SWARM_SERVICES", "shop_paymentservice")
    monkeypatch.setattr(actuation, "_inspect_swarm_service", lambda _name: _service())
    result = actuation.swarm_restart_dry_run({"service_name": "shop_paymentservice"})
    assert result["candidate_count"] == 1
    assert result["items"][0]["version_index"] == 7
    with pytest.raises(ActuationError, match="允许列表"):
        actuation.swarm_restart_dry_run({"service_name": "other_service"})


def test_swarm_restart_rechecks_version_and_uses_exact_update(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AUTONOMY_SWARM_SERVICES", "shop_paymentservice")
    monkeypatch.setattr(actuation, "_inspect_swarm_service", lambda _name: _service())
    calls = []
    monkeypatch.setattr(actuation, "_docker_run", lambda args, timeout=90: calls.append(args) or "ok")
    attempt = ActuationAttempt(
        attempt_id="act-1",
        action_id="swarm.restart-stateless-service",
        dry_run_items=[actuation._swarm_preflight_item("shop_paymentservice")],
    )
    result = actuation.swarm_restart_execute(attempt)
    assert result[0]["rollback_action_id"] == "swarm.rollback-service"
    assert calls == [[
        "service", "update", "--force", "--update-order", "start-first",
        "--label-add", "mini-drop.last-actuation=act-1", "shop_paymentservice",
    ]]
