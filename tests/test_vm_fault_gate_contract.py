"""C7 fault injection must always be scoped, leased, and cleaned up."""

from __future__ import annotations

import pytest

from scripts.vm_fault_injection_gate import (
    SIDECAR_SERVICE,
    ScopedProviderFault,
    ScopedRemoteServiceFault,
    VmGate,
    deterministic_fallback_ok,
    host_systemctl,
)


class FakeRemote:
    def __init__(self):
        self.commands: list[str] = []

    def __call__(self, command: str) -> str:
        self.commands.append(command)
        if "echo $!" in command:
            return "4242\n"
        if "is-active" in command:
            return "active\n"
        return ""


def test_fault_scope_rejects_unapproved_service_and_unbounded_ttl():
    with pytest.raises(ValueError, match="allowlist"):
        host_systemctl("stop", "postgresql")
    with pytest.raises(ValueError, match="TTL"):
        ScopedRemoteServiceFault(FakeRemote(), SIDECAR_SERVICE, ttl_seconds=601)


def test_service_fault_has_remote_ttl_and_finally_cleanup_on_body_error():
    remote = FakeRemote()
    fault = ScopedRemoteServiceFault(
        remote,
        SIDECAR_SERVICE,
        ttl_seconds=30,
        token="deadbeef",
    )
    with pytest.raises(RuntimeError, match="body failed"):
        with fault:
            raise RuntimeError("body failed")

    assert fault.cleanup_ok is True
    assert "sleep 30" in remote.commands[0]
    assert any("systemctl stop mini-drop-pi-sidecar" in item for item in remote.commands)
    assert any("systemctl start mini-drop-pi-sidecar" in item for item in remote.commands)
    assert any("kill 4242" in item for item in remote.commands)
    assert "is-active mini-drop-pi-sidecar" in remote.commands[-1]


def test_provider_fault_restores_protected_env_and_service_on_body_error():
    remote = FakeRemote()
    fault = ScopedProviderFault(remote, ttl_seconds=45, token="cafebabe")
    with pytest.raises(RuntimeError, match="provider body failed"):
        with fault:
            raise RuntimeError("provider body failed")

    assert fault.cleanup_ok is True
    joined = "\n".join(remote.commands)
    assert "MINI_DROP_FAULT_SCOPE=cafebabe" in joined
    assert "__fault_invalid_provider__" in joined
    assert "sleep 45" in joined
    assert "cp /tmp/mini-drop-sidecar-env-cafebabe" in joined
    assert "rm -f /tmp/mini-drop-sidecar-env-cafebabe" in joined
    assert "kill 4242" in joined
    assert "is-active mini-drop-pi-sidecar" in remote.commands[-1]


def test_api_key_expands_only_after_the_protected_env_is_sourced(tmp_path):
    gate = VmGate(tmp_path / "ssh-config", 30)
    commands: list[str] = []

    def fake_control(command: str, timeout: int = 180) -> str:
        del timeout
        commands.append(command)
        return '{"data":{"healthy":true}}'

    gate.control = fake_control
    assert gate.api("/api/readyz")["data"]["healthy"] is True
    assert commands[0].startswith("bash -c 'source ")
    assert '$MINI_DROP_API_KEY' in commands[0]
    assert 'X-API-Key: $MINI_DROP_API_KEY' in commands[0]


def test_ordinary_drop_uses_current_task_creation_response(tmp_path):
    gate = VmGate(tmp_path / "ssh-config", 30)

    def fake_api(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
        if path == "/api/agents":
            return {"data": {"items": [{
                "id": "agent-1",
                "status": "ONLINE",
                "capabilities": ["process_scan"],
            }]}}
        assert path == "/api/tasks"
        assert method == "POST"
        assert payload is not None and payload["collector_type"] == "process_scan"
        return {"data": {"task_id": "task-1", "status": "PENDING"}}

    gate.api = fake_api
    gate.wait_task = lambda task_id: {"id": task_id, "status": "DONE"}
    assert gate.ordinary_drop("contract") == {"id": "task-1", "status": "DONE"}


def test_fallback_gate_accepts_explicit_fail_closed_result_only():
    explicit = {
        "status": "insufficient_data",
        "limitations": ["runtime_fallback:sidecar unavailable"],
        "side_effect_delta": {
            "plan_revision": 0,
            "plan_step_count": 0,
            "case_task_count": 0,
        },
    }
    assert deterministic_fallback_ok(explicit) is True
    assert deterministic_fallback_ok({
        **explicit,
        "limitations": ["not enough evidence"],
    }) is False
    assert deterministic_fallback_ok({
        **explicit,
        "side_effect_delta": {"case_task_count": 1},
    }) is False


def test_provider_failure_gate_waits_for_explicit_sidecar_detail(tmp_path):
    gate = VmGate(tmp_path / "ssh-config", 30)
    gate.sidecar_state = lambda case_id: {
        "case_id": case_id,
        "status": "READY",
        "detail": "Error: unknown model provider",
    }
    state = gate.wait_sidecar_failure("case-1", timeout=1)
    assert state["detail"] == "Error: unknown model provider"
