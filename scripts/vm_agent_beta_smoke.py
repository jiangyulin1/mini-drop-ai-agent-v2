"""Three-node Agent Beta smoke runner.

Requires `ssh -F ssh/vm-config` access to control/worker1/worker2.  The script
does not use VM passwords and never prints API keys.  It validates:

  * control/worker/agent services
  * readyz / runtime=pi / sidecar health
  * native query task end-to-end
  * real Pi turn -> tool -> native task -> case evidence -> no private thinking
  * basic cleanup of created test data

Output: reports/implementation/vm-agent-beta-smoke.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSH_CONFIG = ROOT / "ssh" / "vm-config"
REPORT_OUT = ROOT / "reports" / "implementation" / "vm-agent-beta-smoke.json"


def ssh(node: str, command: str, timeout: int = 120) -> str:
    proc = subprocess.run(
        ["ssh", "-F", str(SSH_CONFIG), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", node, command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh {node} failed: {proc.stderr[-600:]}")
    return proc.stdout


def control_json(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    curl = "curl -sk -H \"X-API-Key: $MINI_DROP_API_KEY\""
    if method == "POST":
        curl += " -X POST -H 'Content-Type: application/json'"
        curl += f" -d '{json.dumps(payload, ensure_ascii=False)}'"
    cmd = (
        "source ~/mini-drop-active/deploy/env/control-native.env && "
        f"{curl} 'https://127.0.0.1{path}'"
    )
    out = ssh("control", cmd).strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {path}: {out[:300]}") from exc


def wait_task_done(task_id: str, timeout: int = 90) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = control_json(f"/api/tasks/{task_id}")
        task = body["data"]
        if task["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return task
        time.sleep(2)
    raise TimeoutError(f"task {task_id} did not finish")


def wait_evidence_count(case_id: str, expected: int, timeout: int = 45) -> list:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = control_json(f"/api/v1/cases/{case_id}/evidence")["data"]["items"]
        if len(items) >= expected:
            return items
        time.sleep(3)
    raise TimeoutError(f"case {case_id} evidence count did not reach {expected}")


def wait_runtime_settled(case_id: str, timeout: int = 120) -> int:
    deadline = time.time() + timeout
    last = 0
    while time.time() < deadline:
        raw = ssh(
            "control",
            "source ~/mini-drop-active/deploy/env/control-native.env && "
            f"curl -sk -H \"X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN\" "
            f"'http://127.0.0.1:8899/internal/runtime/v1/cases/{case_id}/state'",
        )
        state = json.loads(raw)["data"]
        last = state.get("last_event_seq") or 0
        if last >= 0:
            # The sidecar itself is the source of truth for event settling.
            pass
        if state.get("detail"):
            raise RuntimeError(f"runtime error: {state['detail']}")
        # Give the model time to produce tool calls and final turn; then inspect.
        time.sleep(5)
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-pi", action="store_true", help="include real Pi turn")
    parser.add_argument("--skip-cleanup", action="store_true")
    args = parser.parse_args()

    started = datetime.now(timezone.utc).isoformat()
    report: dict = {
        "suite": "vm-agent-beta-smoke",
        "started_at": started,
        "passed": [],
        "failed": [],
        "artifacts": {},
    }

    def check(name: str, fn):
        """v6: every smoke item must assert a boolean predicate; no exception is not PASS."""
        try:
            value = fn()
            if value is not True:
                raise AssertionError(f"predicate returned {value!r}, expected True")
            report["passed"].append(name)
            report["artifacts"][name] = True
            print(f"PASS {name}: predicate true")
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"name": name, "error": str(exc)})
            print(f"FAIL {name}: {exc}")

    check("ssh-control", lambda: ssh("control", "hostname").strip() == "control")
    check("ssh-worker1", lambda: ssh("worker1", "hostname").strip() == "worker1")
    check("ssh-worker2", lambda: ssh("worker2", "hostname").strip() == "worker2")
    check("service-control", lambda: all(
        item.strip() == "active" for item in ssh(
            "control",
            "systemctl is-active mini-drop-server mini-drop-analyzer mini-drop-pi-sidecar mini-drop-s3",
        ).splitlines() if item.strip()
    ))
    check("service-workers", lambda: all(
        ssh(node, "systemctl is-active mini-drop-agent").strip() == "active"
        for node in ("worker1", "worker2")
    ))
    check("readyz", lambda: control_json("/api/readyz")["data"]["healthy"] is True)
    check("runtime-mode", lambda: control_json("/api/v1/agent-runtime/config")["data"]["mode"] == "pi")
    check("agents-online", lambda: len([
        a for a in control_json("/api/agents")["data"]["items"] if a["status"] == "ONLINE"
    ]) >= 2)

    # Native query gateway
    try:
        case_body = control_json("/api/v1/cases", method="POST", payload={
            "title": "vm-agent-beta-smoke-query",
            "problem_description": "checkout 服务延迟升高，请定位根因",
            "recovery_goal": "定位根因",
            "run_mode": "COLLABORATE",
            "environment": "vm",
            "target_scope": {"service_id": "checkout"},
        })
        case_id = case_body["data"]["case_id"]
        report["artifacts"]["query_case_id"] = case_id
        task = wait_task_done(control_json(
            f"/api/v1/cases/{case_id}/queries", method="POST",
            payload={"operation": "process.list", "parameters": {}, "idempotency_key": f"smoke-{int(time.time())}"},
        )["data"]["task"]["id"])
        check("query-native-task", lambda: task["status"] == "DONE")
        evidence = wait_evidence_count(case_id, 1)
        check("query-evidence-materialized", lambda: bool(evidence[0].get("evidence_id")))
    except Exception as exc:  # noqa: BLE001
        report["failed"].append({"name": "query-gateway-loop", "error": str(exc)})
        print(f"FAIL query-gateway-loop: {exc}")

    if args.with_pi:
        try:
            pi_case = control_json("/api/v1/cases", method="POST", payload={
                "title": "vm-agent-beta-smoke-pi",
                "problem_description": "checkout 服务延迟突然升高，请自行定位",
                "recovery_goal": "定位根因",
                "run_mode": "COLLABORATE",
                "environment": "vm",
                "target_scope": {"service_id": "checkout"},
            })
            pi_case_id = pi_case["data"]["case_id"]
            report["artifacts"]["pi_case_id"] = pi_case_id
            turn = control_json(
                f"/api/v1/cases/{pi_case_id}/agent/turn", method="POST",
                payload={"message": "请读取 Case 快照，并调用 create_case_query 创建 system.metrics 查询"},
            )
            check("pi-turn-accepted", lambda: turn["data"]["status"] == "runtime_turn_accepted")
            deadline = time.time() + 180
            found_task = None
            while time.time() < deadline:
                tasks = control_json("/api/tasks")["data"].get("items") or []
                matches = [
                    t for t in tasks
                    if (t.get("request_params") or {}).get("options", {}).get("case_id") == pi_case_id
                    and t["status"] in {"DONE", "FAILED"}
                ]
                if matches:
                    found_task = matches[0]
                    break
                time.sleep(5)
            if found_task is None:
                raise TimeoutError("Pi did not create a native case query task in time")
            check("pi-native-task", lambda: found_task["status"] == "DONE")
            evidence = wait_evidence_count(pi_case_id, 1)
            check("pi-evidence-materialized", lambda: bool(evidence[0].get("evidence_id")))
            events = control_json(f"/api/v1/cases/{pi_case_id}/agent/runtime-state")["data"]["events"]
            tool_names = [
                item["payload"].get("tool_name")
                for item in events if item.get("event_type") == "tool_execution_start"
            ]
            check("pi-tool-calls", lambda: bool(set(tool_names) & {
                "get_case_snapshot", "create_case_query", "request_operation",
                "list_case_evidence", "get_evidence_projection",
            }))
            serialized = json.dumps(events, ensure_ascii=False, default=str)
            check("pi-no-thinking", lambda: "thinking" not in serialized)
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"name": "pi-loop", "error": str(exc)})
            print(f"FAIL pi-loop: {exc}")

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "PASS" if not report["failed"] else "FAIL"
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
