#!/usr/bin/env python3
"""Pi Agent focused real-fault evaluation.

This runner is intentionally narrow: it injects one fault on a worker, asks the
Pi Agent to investigate, waits for the agent to settle, and scores the observed
tool usage and final answer.  It does not depend on the deterministic diagnosis
runner or the Online Boutique test set.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko


def ssh_run(host: str, user: str, password: str, cmd: str, timeout: int = 30) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=15)
    _, out, err = client.exec_command(cmd, timeout=timeout)
    data = out.read().decode() + err.read().decode()
    client.close()
    return data.strip()


def http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 20) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_fault(host: str, user: str, password: str, fault: str, duration: int) -> int:
    ssh_run(host, user, password, "systemctl stop pi-agent-eval 2>/dev/null; systemctl reset-failed pi-agent-eval 2>/dev/null || true")
    cmd = (
        "systemd-run --unit=pi-agent-eval --collect sh -c "
        f"'cd /jyl/mini-drop && python3 demo/vm_test_targets.py --inject-fault {fault} --duration {duration} "
        f">/tmp/pi_agent_eval.log 2>&1'"
    )
    ssh_run(host, user, password, cmd)
    time.sleep(3)
    pid_line = ssh_run(
        host, user, password,
        f"pgrep -f '^python3 demo/vm_test_targets.py --inject-fault {fault}' | head -1",
    )
    return int(pid_line.strip())


def create_case_and_turn(
    control_url: str,
    agent_id: str,
    pid: int,
    fault: str,
    *,
    strategy_id: str | None = None,
    runtime_options: dict[str, Any] | None = None,
    runtime_policy: dict[str, Any] | None = None,
) -> tuple[str, str]:
    with urllib.request.urlopen(f"{control_url.rstrip('/')}/api/agents", timeout=10) as resp:
        agents = json.load(resp)["data"]["items"]
    host_id = next((a["hostname"] for a in agents if a["id"] == agent_id), agent_id)

    scope = {
        "service_id": "pi-agent-eval",
        "instances": [{
            "service_id": "pi-agent-eval",
            "instance_id": f"pi-eval-{agent_id}",
            "host_id": host_id,
            "agent_id": agent_id,
            "pid": pid,
            "environment": "production",
        }],
    }
    payload = {
        "title": "pi-agent-eval",
        "problem_description": f"目标进程出现 {fault} 故障，请定位根因",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": scope,
    }
    case = http_json(f"{control_url.rstrip('/')}/api/v1/cases", "POST", payload)["data"]
    case_id = case["case_id"]

    turn: dict[str, Any] = {
        "message": f"目标进程出现 {fault} 故障，请开始调查并定位根因",
        "intent": "investigate",
        "execute_safe_tools": True,
    }
    if strategy_id:
        turn["strategy_id"] = strategy_id
    if runtime_options:
        turn["runtime_options"] = runtime_options
    if runtime_policy:
        turn["runtime_policy"] = runtime_policy
    http_json(f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/agent/turn", "POST", turn)
    return case_id, turn["message"]


OPERATION_TO_TOOL = {
    "system.metrics": "sys_metrics",
    "process.list": "process_scan",
    "service.logs": "log_scan",
    "service.connection": "connection_probe",
}


def wait_for_settle(control_url: str, case_id: str, timeout: float = 600.0) -> dict[str, Any]:
    tools: list[str] = []
    operations: list[str] = []
    final_text = ""
    conclusion_text = ""
    settled = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = http_json(f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/events")["data"]["items"]
        try:
            runtime = http_json(f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/agent/runtime-state")["data"]
            runtime_events = runtime.get("events") or []
        except Exception:
            runtime_events = []
        for event in runtime_events:
            payload = event.get("payload") or {}
            etype = event.get("event_type", "")
            if etype == "tool_execution_start" and payload.get("tool_name"):
                name = payload["tool_name"]
                if name not in tools:
                    tools.append(name)
        for event in events:
            payload = event.get("payload") or {}
            etype = event.get("event_type", "")
            if etype == "tool_execution_start" and payload.get("tool_name"):
                name = payload["tool_name"]
                if name not in tools:
                    tools.append(name)
            if etype == "case_query_task_created" and payload.get("operation"):
                op = payload["operation"]
                if op not in operations:
                    operations.append(op)
                    mapped = OPERATION_TO_TOOL.get(op)
                    if mapped and mapped not in tools:
                        tools.append(mapped)
            if etype == "agent_finish_investigation" and payload.get("summary"):
                # The persisted conclusion is the authoritative final answer.
                # A later assistant retry/rejection message must not overwrite it.
                conclusion_text = payload["summary"]
                final_text = conclusion_text
                settled = True
            elif etype in ("assistant.message", "turn.completed", "agent_settled") and payload.get("content") and not conclusion_text:
                final_text = payload["content"]
            if etype in ("agent_settled", "agent_finish_investigation"):
                settled = True
        if settled and final_text:
            break
        time.sleep(5)
    return {"settled": settled, "tools": sorted(tools), "operations": sorted(operations), "final_answer": final_text}


def score(tools: list[str], final_text: str, fault: str) -> dict[str, Any]:
    text = (final_text or "").lower()
    relevant = {
        "cpu-hotspot": ["perf_cpu", "sys_metrics", "process_scan"],
        "memory-leak": ["memory_smaps", "sys_metrics"],
        "io-write": ["sys_metrics", "ebpf_io"],
        "lock-contend": ["runtime_snapshot", "sys_metrics", "perf_cpu"],
    }.get(fault, [])
    used = set(tools)
    relevant_set = set(relevant)
    recall = len(relevant_set & used) / len(relevant_set) if relevant_set else 0.0
    mentioned = any(kw in text for kw in ("cpu", "热点", "hotspot", "perf", "profile"))
    return {
        "fault": fault,
        "tool_recall": round(recall, 3),
        "used_relevant_tools": sorted(relevant_set & used),
        "missing_relevant_tools": sorted(relevant_set - used),
        "final_answer_mentions_fault": mentioned,
        "forbidden_tool_used": any(t in tools for t in ("shell", "bash", "exec", "edit")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-url", default="http://47.112.10.137")
    parser.add_argument("--worker-host", required=True)
    parser.add_argument("--worker-user", default="root")
    parser.add_argument("--worker-password", required=True)
    parser.add_argument("--agent-id", default="linux-worker-1")
    parser.add_argument("--fault", default="cpu-hotspot")
    parser.add_argument("--duration", type=int, default=360)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/pi-agent-eval"))
    parser.add_argument("--case-id", default=None, help="reuse an existing case instead of injecting a new fault")
    args = parser.parse_args(argv)

    if args.case_id:
        case_id = args.case_id
        pid = 0
        print(f"[pi-eval] reusing case {case_id}", flush=True)
    else:
        print(f"[pi-eval] starting fault {args.fault} on {args.worker_host}", flush=True)
        pid = start_fault(args.worker_host, args.worker_user, args.worker_password, args.fault, args.duration)
        print(f"[pi-eval] injector pid={pid}", flush=True)

        case_id, message = create_case_and_turn(args.control_url, args.agent_id, pid, args.fault)
        print(f"[pi-eval] case={case_id} turn submitted", flush=True)

    result = wait_for_settle(args.control_url, case_id, args.timeout)
    scores = score(result["tools"], result["final_answer"], args.fault)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "fault": args.fault,
        "injector_pid": pid,
        **result,
        "scores": scores,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "pi-agent-eval.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
