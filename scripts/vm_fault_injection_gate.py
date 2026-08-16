#!/usr/bin/env python3
"""Scoped three-node fault gate with TTL watchdogs and unconditional cleanup."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_CONFIG = ROOT / "ssh" / "vm-config"
REPORT = ROOT / "reports" / "implementation" / "vm-fault-injection-gate.json"
SIDECAR_SERVICE = "mini-drop-pi-sidecar"
ALLOWED_SERVICES = {SIDECAR_SERVICE}


class RemoteError(RuntimeError):
    pass


def deterministic_fallback_ok(turn: dict) -> bool:
    """Recognize an explicit, fail-closed fallback without accepting a soft pass."""
    limitations = turn.get("limitations") or []
    delta = turn.get("side_effect_delta") or {}
    return (
        turn.get("status") in {"answered", "insufficient_data"}
        and any(str(item).startswith("runtime_fallback:") for item in limitations)
        and all(int(value or 0) == 0 for value in delta.values())
    )


def host_systemctl(action: str, service: str) -> str:
    if action not in {"start", "stop", "restart", "is-active"}:
        raise ValueError(f"unsupported systemctl action: {action}")
    if service not in ALLOWED_SERVICES:
        raise ValueError(f"service is outside the fault allowlist: {service}")
    return (
        "docker run --rm --privileged --pid=host --uts=host --net=host redis:alpine "
        f"nsenter -t 1 -m -u -i -n -p /usr/bin/systemctl {action} {service}"
    )


class ScopedRemoteServiceFault:
    """Stop one allowlisted service, with remote TTL recovery and finally cleanup."""

    def __init__(
        self,
        execute: Callable[[str], str],
        service: str,
        *,
        ttl_seconds: int,
        token: str | None = None,
    ):
        if service not in ALLOWED_SERVICES:
            raise ValueError(f"service is outside the fault allowlist: {service}")
        if not 10 <= ttl_seconds <= 600:
            raise ValueError("fault TTL must be between 10 and 600 seconds")
        self.execute = execute
        self.service = service
        self.ttl_seconds = ttl_seconds
        self.token = token or secrets.token_hex(8)
        self.watchdog_pid: str | None = None
        self.cleanup_ok = False

    def __enter__(self):
        log = f"/tmp/mini-drop-fault-{self.token}.log"
        watchdog = (
            f"( sleep {self.ttl_seconds}; {host_systemctl('start', self.service)} ) "
            f">{log} 2>&1 & echo $!"
        )
        self.watchdog_pid = self.execute(watchdog).strip()
        if not self.watchdog_pid.isdigit():
            raise RemoteError("remote fault watchdog did not return a PID")
        self.execute(host_systemctl("stop", self.service))
        return self

    def cleanup(self) -> None:
        errors: list[str] = []
        try:
            self.execute(host_systemctl("start", self.service))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"service restore failed: {exc}")
        if self.watchdog_pid:
            try:
                self.execute(
                    f"kill {self.watchdog_pid} 2>/dev/null || true; "
                    f"rm -f /tmp/mini-drop-fault-{self.token}.log"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"watchdog cleanup failed: {exc}")
        try:
            active = self.execute(host_systemctl("is-active", self.service)).strip()
            if active != "active":
                errors.append(f"service final state is {active!r}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"service health check failed: {exc}")
        self.cleanup_ok = not errors
        if errors:
            raise RemoteError("; ".join(errors))

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False


class ScopedProviderFault:
    """Inject an invalid Pi provider without exposing or permanently editing secrets."""

    env_path = "/home/control/mini-drop-active/deploy/env/sidecar.env"

    def __init__(self, execute: Callable[[str], str], *, ttl_seconds: int, token: str | None = None):
        if not 10 <= ttl_seconds <= 600:
            raise ValueError("fault TTL must be between 10 and 600 seconds")
        self.execute = execute
        self.ttl_seconds = ttl_seconds
        self.token = token or secrets.token_hex(8)
        self.backup = f"/tmp/mini-drop-sidecar-env-{self.token}"
        self.watchdog_pid: str | None = None
        self.cleanup_ok = False

    @property
    def marker(self) -> str:
        return f"MINI_DROP_FAULT_SCOPE={self.token}"

    def _restore_command(self) -> str:
        return (
            f"if grep -q '{self.marker}' {self.env_path} && test -f {self.backup}; then "
            f"cp {self.backup} {self.env_path}; fi; rm -f {self.backup}; "
            f"{host_systemctl('restart', SIDECAR_SERVICE)}"
        )

    def __enter__(self):
        self.execute(
            f"umask 077; cp {self.env_path} {self.backup}; "
            f"printf '\n# {self.marker}\nMINI_DROP_PI_MODEL_PROVIDER=__fault_invalid_provider__\n' "
            f">> {self.env_path}"
        )
        watchdog = (
            f"( sleep {self.ttl_seconds}; {self._restore_command()} ) "
            f">/tmp/mini-drop-provider-fault-{self.token}.log 2>&1 & echo $!"
        )
        self.watchdog_pid = self.execute(watchdog).strip()
        if not self.watchdog_pid.isdigit():
            self.execute(self._restore_command())
            raise RemoteError("remote provider-fault watchdog did not return a PID")
        self.execute(host_systemctl("restart", SIDECAR_SERVICE))
        return self

    def cleanup(self) -> None:
        errors: list[str] = []
        try:
            self.execute(self._restore_command())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"provider environment restore failed: {exc}")
        if self.watchdog_pid:
            try:
                self.execute(
                    f"kill {self.watchdog_pid} 2>/dev/null || true; "
                    f"rm -f /tmp/mini-drop-provider-fault-{self.token}.log"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"provider watchdog cleanup failed: {exc}")
        try:
            active = self.execute(host_systemctl("is-active", SIDECAR_SERVICE)).strip()
            if active != "active":
                errors.append(f"sidecar final state is {active!r}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sidecar final check failed: {exc}")
        self.cleanup_ok = not errors
        if errors:
            raise RemoteError("; ".join(errors))

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False


class VmGate:
    def __init__(self, ssh_config: Path, ttl_seconds: int):
        self.ssh_config = ssh_config.resolve()
        self.ttl_seconds = ttl_seconds

    def ssh(self, node: str, command: str, timeout: int = 180) -> str:
        proc = subprocess.run(
            [
                "ssh", "-F", str(self.ssh_config), "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10", node, command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode:
            raise RemoteError(f"ssh {node} failed: {proc.stderr[-500:]}")
        return proc.stdout

    def control(self, command: str, timeout: int = 180) -> str:
        return self.ssh("control", command, timeout)

    def api(self, path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
        command = "source ~/mini-drop-active/deploy/env/control-native.env && "
        command += "curl -sk -H \"X-API-Key: $MINI_DROP_API_KEY\""
        if method == "POST":
            encoded = base64.b64encode(json.dumps(payload or {}).encode("utf-8")).decode("ascii")
            command += (
                " -X POST -H 'Content-Type: application/json' "
                f"--data-binary @<(printf '%s' '{encoded}' | base64 -d)"
            )
        command += f" 'https://127.0.0.1{path}'"
        # Keep credential expansion inside the inner Bash, after the protected
        # environment file has been sourced.  JSON double-quoting lets the
        # outer login shell expand an unset $MINI_DROP_API_KEY first.
        output = self.control(f"bash -c {shlex.quote(command)}").strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RemoteError(f"invalid API JSON for {path}: {output[:300]}") from exc

    def wait_task(self, task_id: str, timeout: int = 120) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.api(f"/api/tasks/{task_id}")["data"]
            if task["status"] in {"DONE", "FAILED", "CANCELLED"}:
                return task
            time.sleep(2)
        raise TimeoutError(f"task {task_id} did not settle")

    def sidecar_state(self, case_id: str) -> dict:
        safe_case_id = quote(case_id, safe="")
        command = (
            "source ~/mini-drop-active/deploy/env/control-native.env && "
            "curl -sk -H \"X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN\" "
            f"'http://127.0.0.1:8899/internal/runtime/v1/cases/{safe_case_id}/state'"
        )
        output = self.control(command).strip()
        try:
            body = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RemoteError(f"invalid Sidecar state JSON: {output[:300]}") from exc
        if body.get("ok") is not True or not isinstance(body.get("data"), dict):
            raise RemoteError(f"Sidecar state request failed: {body!r}")
        return body["data"]

    def wait_sidecar_failure(self, case_id: str, timeout: int = 45) -> dict:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self.sidecar_state(case_id)
            if str(last.get("detail") or "").strip():
                return last
            time.sleep(1)
        raise TimeoutError(f"Provider failure did not become explicit in Sidecar state: {last!r}")

    def sidecar_health(self) -> dict:
        command = (
            "source ~/mini-drop-active/deploy/env/control-native.env && "
            "curl -sk -H \"X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN\" "
            "'http://127.0.0.1:8899/internal/runtime/v1/health'"
        )
        output = self.control(command).strip()
        try:
            body = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RemoteError(f"invalid Sidecar health JSON: {output[:300]}") from exc
        if body.get("ok") is not True or not isinstance(body.get("data"), dict):
            raise RemoteError(f"Sidecar health request failed: {body!r}")
        return body["data"]

    def ordinary_drop(self, suffix: str) -> dict:
        agents = self.api("/api/agents")["data"]["items"]
        agent = next(item for item in agents if item["status"] == "ONLINE")
        capabilities = set(agent.get("capabilities") or [])
        collector = "process_scan" if "process_scan" in capabilities else "sys_metrics"
        created = self.api("/api/tasks", method="POST", payload={
            "name": f"vm-fault-gate-{suffix}",
            "agent_id": agent["id"],
            "target_pid": 1,
            "collector_type": collector,
            "sample_rate": 10,
            "duration_sec": 5,
            "options": {"fault_gate": suffix},
        })["data"]
        task_id = created.get("task_id") or created.get("id")
        if not task_id:
            raise RemoteError(f"task creation did not return an identifier: {created!r}")
        return self.wait_task(task_id)

    def create_case(self, suffix: str) -> dict:
        return self.api("/api/v1/cases", method="POST", payload={
            "title": f"vm-fault-gate-{suffix}",
            "problem_description": "验证故障回退与清理",
            "recovery_goal": "保持普通 Drop 可用",
            "run_mode": "COLLABORATE",
            "environment": "vm",
            "target_scope": {"service_id": "checkout"},
        })["data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-config", type=Path, default=DEFAULT_SSH_CONFIG)
    parser.add_argument("--ttl-seconds", type=int, default=120)
    args = parser.parse_args()
    if not args.ssh_config.is_file():
        print(f"missing SSH config: {args.ssh_config}")
        return 2

    gate = VmGate(args.ssh_config, args.ttl_seconds)
    report: dict = {
        "schema_version": "vm-fault-injection-gate-v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ttl_seconds": args.ttl_seconds,
        "assertions": {},
    }

    def assertion(name: str, predicate: bool, detail=None) -> None:
        report["assertions"][name] = {
            "status": "PASS" if predicate else "FAIL",
            "detail": detail,
        }
        if not predicate:
            raise AssertionError(f"{name}: {detail}")

    try:
        assertion("initial_ready", gate.api("/api/readyz")["data"]["healthy"] is True)
        assertion("ordinary_drop", gate.ordinary_drop("baseline")["status"] == "DONE")

        pi_case = gate.create_case("pi-unavailable")
        service_fault = ScopedRemoteServiceFault(
            gate.control, SIDECAR_SERVICE, ttl_seconds=args.ttl_seconds,
        )
        with service_fault:
            turn = gate.api(
                f"/api/v1/cases/{pi_case['case_id']}/agent/turn",
                method="POST",
                payload={"message": "只解释当前证据", "requested_disposition": "ANSWER_ONLY"},
            )["data"]
            assertion("pi_unavailable_deterministic_fallback", deterministic_fallback_ok(turn), turn)
            assertion("drop_works_during_pi_fault", gate.ordinary_drop("pi-down")["status"] == "DONE")
        assertion("pi_fault_cleanup", service_fault.cleanup_ok)

        provider_case = gate.create_case("provider-unavailable")
        provider_fault = ScopedProviderFault(gate.control, ttl_seconds=args.ttl_seconds)
        with provider_fault:
            time.sleep(3)
            turn = gate.api(
                f"/api/v1/cases/{provider_case['case_id']}/agent/turn",
                method="POST",
                payload={"message": "解释当前状态", "requested_disposition": "ANSWER_ONLY"},
            )["data"]
            failure = gate.wait_sidecar_failure(provider_case["case_id"])
            assertion(
                "provider_failure_explicit",
                turn.get("status") == "runtime_turn_accepted"
                and bool(str(failure.get("detail") or "").strip()),
                {"turn": turn, "sidecar_state": failure},
            )
        assertion("provider_fault_cleanup", provider_fault.cleanup_ok)

        mcp = gate.api("/api/v1/mcp/facts/query", method="POST", payload={
            "missing_fact": "billing_quota_usage",
            "resource": {"service_id": "checkout"},
        })["data"]
        assertion(
            "mcp_unavailable_is_explicit_gap",
            mcp.get("decision") in {"INSUFFICIENT", "MCP_FAILED"},
            mcp,
        )
        assertion("final_ready", gate.api("/api/readyz")["data"]["healthy"] is True)
        assertion("final_sidecar_ready", gate.sidecar_health().get("status") == "ready")
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            final_healthy = gate.api("/api/readyz")["data"]["healthy"] is True
            report["assertions"]["final_health_check"] = {
                "status": "PASS" if final_healthy else "FAIL",
                "detail": None,
            }
        except Exception as exc:  # noqa: BLE001
            report["assertions"]["final_health_check"] = {
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        required = {
            "initial_ready", "ordinary_drop", "pi_unavailable_deterministic_fallback",
            "drop_works_during_pi_fault", "pi_fault_cleanup", "provider_failure_explicit",
            "provider_fault_cleanup", "mcp_unavailable_is_explicit_gap", "final_ready",
            "final_sidecar_ready", "final_health_check",
        }
        assertions = report["assertions"]
        report["status"] = (
            "PASS" if set(assertions) == required
            and all(item["status"] == "PASS" for item in assertions.values())
            else "FAIL"
        )
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
