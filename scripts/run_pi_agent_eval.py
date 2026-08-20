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


FAULT_SYMPTOMS = {
    "cpu-hotspot": "目标进程持续占用接近一个 CPU 核心，服务响应开始变慢",
    "memory-leak": "目标进程内存占用持续增长，长时间运行后可能触发 OOM",
    "io-write": "目标进程运行期间磁盘写入和 I/O 等待明显升高",
    "lock-contend": "目标进程吞吐下降且线程切换频繁，多个工作线程疑似无法继续推进",
}


def symptom_for_fault(fault: str) -> str:
    """Return the public symptom while keeping the private fault label out of model context."""
    try:
        return FAULT_SYMPTOMS[fault]
    except KeyError as exc:
        raise ValueError(f"unsupported blind-evaluation fault: {fault}") from exc


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
    symptom_for_fault(fault)  # Reject scenarios without a blind public prompt.
    ssh_run(host, user, password, "systemctl stop pi-agent-eval 2>/dev/null; systemctl reset-failed pi-agent-eval 2>/dev/null || true")
    cmd = (
        f"systemd-run --unit=pi-agent-eval --collect --setenv=MINI_DROP_EVAL_SCENARIO={fault} sh -c "
        f"'cd /jyl/mini-drop && exec python3 demo/vm_test_targets.py "
        f"--inject-fault-env MINI_DROP_EVAL_SCENARIO --duration {duration} "
        f">/tmp/pi_agent_eval.log 2>&1'"
    )
    ssh_run(host, user, password, cmd)
    time.sleep(3)
    pid_line = ssh_run(
        host, user, password,
        "pgrep -f '^python3 demo/vm_test_targets.py --inject-fault-env MINI_DROP_EVAL_SCENARIO' | head -1",
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
    symptom = symptom_for_fault(fault)
    payload = {
        "title": "pi-agent-eval",
        "problem_description": symptom,
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": scope,
    }
    case = http_json(f"{control_url.rstrip('/')}/api/v1/cases", "POST", payload)["data"]
    case_id = case["case_id"]

    turn: dict[str, Any] = {
        "message": f"{symptom}，请基于实际采集证据开始调查并定位根因",
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
    conclusion_evidence_refs: list[str] = []
    conclusion_verifier = ""
    conclusion_state = ""
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
                conclusion_evidence_refs = [str(item) for item in (payload.get("evidence_refs") or [])]
                conclusion_verifier = str(payload.get("verifier") or "")
                conclusion_state = str(payload.get("state") or "")
                settled = True
            elif etype in ("assistant.message", "turn.completed", "agent_settled") and payload.get("content") and not conclusion_text:
                final_text = payload["content"]
            if etype in ("agent_settled", "agent_finish_investigation"):
                settled = True
        if settled and final_text:
            break
        time.sleep(5)
    return {
        "settled": settled,
        "tools": sorted(tools),
        "operations": sorted(operations),
        "final_answer": final_text,
        "conclusion": {
            "evidence_refs": conclusion_evidence_refs,
            "verifier": conclusion_verifier,
            "state": conclusion_state,
        },
    }


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
    cause_terms = {
        "cpu-hotspot": (("cpu",), ("热点", "hotspot", "忙循环", "自旋", "计算密集")),
        "memory-leak": (("内存", "memory", "rss"), ("泄漏", "leak")),
        "io-write": (("io", "i/o", "磁盘"), ("写循环", "高频写", "顺序写", "write loop", "持续写入")),
        "lock-contend": (("锁", "lock"), ("竞争", "争用", "contention", "阻塞")),
    }
    groups = cause_terms.get(fault)
    mentioned = bool(groups) and all(any(term in text for term in group) for group in groups)
    return {
        "fault": fault,
        "tool_recall": round(recall, 3),
        "used_relevant_tools": sorted(relevant_set & used),
        "missing_relevant_tools": sorted(relevant_set - used),
        "final_answer_mentions_fault": mentioned,
        "root_cause_signature": fault if mentioned else "unknown",
        "forbidden_tool_used": any(t in tools for t in ("shell", "bash", "exec", "edit")),
    }


def score_evidence_citations(conclusion: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Score only citations accepted by the server's factual conclusion verifier."""
    refs = list(dict.fromkeys(str(item) for item in (conclusion.get("evidence_refs") or []) if item))
    active = {
        str(item.get("evidence_id"))
        for item in evidence
        if item.get("status") == "ACTIVE" and item.get("projection_hash")
    }
    invalid = sorted(set(refs) - active)
    verifier = str(conclusion.get("verifier") or "")
    valid = bool(refs) and not invalid and verifier == "causal-report-verifier.v1"
    return {
        "valid": valid,
        "score": 1.0 if valid else 0.0,
        "cited_count": len(refs),
        "invalid_refs": invalid,
        "verifier": verifier,
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
