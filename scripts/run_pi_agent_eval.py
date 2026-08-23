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
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko


_HTTP_API_KEY = ""
_HTTP_SSL_CONTEXT: ssl.SSLContext | None = None


def configure_http(*, api_key: str = "", ca_file: str | None = None) -> None:
    """Configure authenticated HTTPS without placing credentials in URLs."""
    global _HTTP_API_KEY, _HTTP_SSL_CONTEXT
    _HTTP_API_KEY = api_key.strip()
    _HTTP_SSL_CONTEXT = ssl.create_default_context(cafile=ca_file) if ca_file else None


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


def ssh_run(
    host: str,
    user: str,
    password: str,
    cmd: str,
    timeout: int = 30,
    *,
    key_filename: str | None = None,
) -> str:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs: dict[str, Any] = {
        "hostname": host,
        "username": user,
        "timeout": 15,
    }
    if password:
        connect_kwargs["password"] = password
    if key_filename:
        connect_kwargs["key_filename"] = key_filename
        connect_kwargs["look_for_keys"] = False
    client.connect(**connect_kwargs)
    _, out, err = client.exec_command(cmd, timeout=timeout)
    data = out.read().decode() + err.read().decode()
    status = out.channel.recv_exit_status()
    client.close()
    if status != 0:
        raise RuntimeError(f"ssh command failed ({status}): {data[-600:]}")
    return data.strip()


def http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 20) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if _HTTP_API_KEY:
        headers["X-API-Key"] = _HTTP_API_KEY
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_HTTP_SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_fault(
    host: str,
    user: str,
    password: str,
    fault: str,
    duration: int,
    *,
    key_filename: str | None = None,
) -> int:
    symptom_for_fault(fault)  # Reject scenarios without a blind public prompt.
    ssh_run(
        host, user, password,
        "systemctl stop pi-agent-eval 2>/dev/null; "
        "systemctl reset-failed pi-agent-eval 2>/dev/null || true",
        key_filename=key_filename,
    )
    cmd = (
        f"systemd-run --unit=pi-agent-eval --collect --setenv=MINI_DROP_EVAL_SCENARIO={fault} sh -c "
        f"'cd /jyl/mini-drop-active && exec python3 demo/vm_test_targets.py "
        f"--inject-fault-env MINI_DROP_EVAL_SCENARIO --duration {duration} "
        f">/tmp/pi_agent_eval.log 2>&1'"
    )
    ssh_run(host, user, password, cmd, key_filename=key_filename)
    time.sleep(3)
    pid_line = ssh_run(
        host, user, password,
        "pgrep -f '^python3 demo/vm_test_targets.py --inject-fault-env MINI_DROP_EVAL_SCENARIO' | head -1",
        key_filename=key_filename,
    )
    return int(pid_line.strip())


def stop_fault(
    host: str,
    user: str,
    password: str,
    *,
    key_filename: str | None = None,
) -> None:
    ssh_run(
        host, user, password,
        "systemctl stop pi-agent-eval 2>/dev/null || true; "
        "systemctl reset-failed pi-agent-eval 2>/dev/null || true; "
        "rm -f /tmp/mini_drop_io_test.bin /tmp/mini_drop_dd_test",
        key_filename=key_filename,
    )


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
    agents = http_json(f"{control_url.rstrip('/')}/api/agents", timeout=10)["data"]["items"]
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
    # Keep every invocation as an ordered event.  A set of tool names loses
    # retries and makes it impossible to explain why the second call happened.
    tool_calls_by_key: dict[str, dict[str, Any]] = {}
    tool_order: list[str] = []
    seen_event_keys: set[str] = set()
    operations: list[str] = []
    final_text = ""
    conclusion_text = ""
    conclusion_evidence_refs: list[str] = []
    conclusion_verifier = ""
    conclusion_state = ""
    settled = False
    stable_after_conclusion = 0
    previous_runtime_seq = -1
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = http_json(f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/events")["data"]["items"]
        try:
            runtime = http_json(f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/agent/runtime-state")["data"]
            runtime_events = runtime.get("events") or []
            runtime_seq = int((runtime.get("binding") or {}).get("last_event_seq") or 0)
        except Exception:
            runtime_events = []
            runtime_seq = 0
        for event in runtime_events:
            payload = event.get("payload") or {}
            etype = event.get("event_type", "")
            if etype not in {"tool_execution_start", "tool_execution_end"}:
                continue
            audit = payload.get("tool_audit") or {}
            call_id = str(payload.get("tool_call_id") or audit.get("tool_call_id") or "")
            name = str(payload.get("tool_name") or audit.get("tool_name") or "")
            if not name:
                continue
            event_key = str(event.get("event_id") or "") or (
                f"{event.get('runtime_generation', '')}:{event.get('event_seq', '')}:"
                f"{etype}:{call_id}:{name}"
            )
            if event_key in seen_event_keys:
                continue
            seen_event_keys.add(event_key)
            key = call_id or event_key
            record = tool_calls_by_key.setdefault(key, {
                "event_seq": event.get("event_seq"),
                "tool_call_id": call_id or None,
                "tool_name": name,
                "audit": {},
            })
            record["audit"].update(audit)
            if etype == "tool_execution_start":
                record["started_event_seq"] = event.get("event_seq")
                tool_order.append(name)
        for event in events:
            payload = event.get("payload") or {}
            etype = event.get("event_type", "")
            if etype in {"tool_execution_start", "tool_execution_end"}:
                audit = payload.get("tool_audit") or {}
                call_id = str(payload.get("tool_call_id") or audit.get("tool_call_id") or "")
                name = str(payload.get("tool_name") or audit.get("tool_name") or "")
                if name:
                    event_key = str(event.get("event_id") or "") or (
                        f"{event.get('runtime_generation', '')}:{event.get('event_seq', '')}:"
                        f"{etype}:{call_id}:{name}"
                    )
                    if event_key not in seen_event_keys:
                        seen_event_keys.add(event_key)
                        key = call_id or event_key
                        record = tool_calls_by_key.setdefault(key, {
                            "event_seq": event.get("event_seq"),
                            "tool_call_id": call_id or None,
                            "tool_name": name,
                            "audit": {},
                        })
                        record["audit"].update(audit)
                        if etype == "tool_execution_start":
                            record["started_event_seq"] = event.get("event_seq")
                            tool_order.append(name)
            if etype == "case_query_task_created" and payload.get("operation"):
                op = payload["operation"]
                if op not in operations:
                    operations.append(op)
                    mapped = OPERATION_TO_TOOL.get(op)
                    if mapped:
                        tool_order.append(mapped)
                        synthetic_key = f"operation:{event.get('event_id') or op}"
                        tool_calls_by_key.setdefault(synthetic_key, {
                            "event_seq": event.get("event_seq"),
                            "tool_call_id": None,
                            "tool_name": mapped,
                            "audit": {"source": "case_query_task", "operation": op},
                        })
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
            # agent_settled is only a Pi run boundary. A collection proposal
            # intentionally settles while Mini-Drop waits for durable Evidence;
            # only the verified finish event is terminal for this evaluation.
        if settled and final_text:
            if runtime_seq > 0 and runtime_seq == previous_runtime_seq:
                stable_after_conclusion += 1
                if stable_after_conclusion >= 3:
                    break
            else:
                stable_after_conclusion = 0
        previous_runtime_seq = runtime_seq
        time.sleep(5)
    return {
        "settled": settled,
        "tools": sorted({str(item.get("tool_name")) for item in tool_calls_by_key.values()}),
        "tool_sequence": tool_order,
        "tool_counts": {
            name: tool_order.count(name) for name in sorted(set(tool_order))
        },
        "tool_calls": list(tool_calls_by_key.values()),
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
    parser.add_argument("--worker-password", default="")
    parser.add_argument("--worker-ssh-key", type=Path)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--agent-id", default="linux-worker-1")
    parser.add_argument("--fault", default="cpu-hotspot")
    parser.add_argument("--duration", type=int, default=360)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/pi-agent-eval"))
    parser.add_argument("--case-id", default=None, help="reuse an existing case instead of injecting a new fault")
    args = parser.parse_args(argv)

    api_key = ""
    if args.api_key_file:
        api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    configure_http(
        api_key=api_key,
        ca_file=str(args.ca_file.resolve()) if args.ca_file else None,
    )
    ssh_key = str(args.worker_ssh_key.resolve()) if args.worker_ssh_key else None
    started_at = datetime.now(timezone.utc).isoformat()
    fault_started = False

    try:
        if args.case_id:
            case_id = args.case_id
            pid = 0
            print(f"[pi-eval] reusing case {case_id}", flush=True)
        else:
            print(f"[pi-eval] starting fault {args.fault} on {args.worker_host}", flush=True)
            pid = start_fault(
                args.worker_host, args.worker_user, args.worker_password,
                args.fault, args.duration, key_filename=ssh_key,
            )
            fault_started = True
            print(f"[pi-eval] injector pid={pid}", flush=True)

            case_id, _message = create_case_and_turn(
                args.control_url, args.agent_id, pid, args.fault,
            )
            print(f"[pi-eval] case={case_id} turn submitted", flush=True)

        result = wait_for_settle(args.control_url, case_id, args.timeout)
        try:
            workspace = http_json(
                f"{args.control_url.rstrip('/')}/api/v1/cases/{case_id}/workspace",
            )["data"]
        except Exception:
            workspace = {}
        collectors_used = sorted({
            str(item.get("collector_id"))
            for item in (workspace.get("collection_requests") or [])
            if item.get("collector_id") and item.get("status") in {"DISPATCHED", "COMPLETED"}
        })
        scores = score(collectors_used, result["final_answer"], args.fault)
        try:
            evidence = http_json(
                f"{args.control_url.rstrip('/')}/api/v1/cases/{case_id}/evidence",
            )["data"]["items"]
        except Exception:
            evidence = []
        try:
            attempts = http_json(
                f"{args.control_url.rstrip('/')}/api/v1/cases/{case_id}/model-attempts",
            )["data"]["items"]
        except Exception:
            attempts = []
        citation_score = score_evidence_citations(result["conclusion"], evidence)
    finally:
        if fault_started:
            stop_fault(
                args.worker_host, args.worker_user, args.worker_password,
                key_filename=ssh_key,
            )

    usage = {
        "attempt_count": len(attempts),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in attempts),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in attempts),
        "latency_ms": sum(int(item.get("latency_ms") or 0) for item in attempts),
        "cost": round(sum(float(item.get("cost") or 0.0) for item in attempts), 6),
    }
    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "fault": args.fault,
        "injector_pid": pid,
        **result,
        "scores": scores,
        "collectors_used": collectors_used,
        "citation_score": citation_score,
        "usage": usage,
        "evidence_count": len(evidence),
        "cleanup_completed": fault_started,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "pi-agent-eval.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
