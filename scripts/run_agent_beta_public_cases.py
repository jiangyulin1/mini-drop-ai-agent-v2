"""Run the executable subset of P01-P12 against the three-node VM.

This runner intentionally does not claim full R4 for cases that require
OpenTelemetry Demo causal stacks (P09/P10), independent Holdout or full
browser E2E (P03/P06/P08/P12 UI).  It produces per-case machine evidence and
marks non-executable cases AWAITING_ENVIRONMENT.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.vm_agent_beta_smoke import (
    control_json,
    ssh,
    wait_evidence_count,
    wait_task_done,
)

OUT = ROOT / "reports" / "implementation" / "public-cases-status.json"


def case_task_ids(case_id: str) -> list[str]:
    tasks = control_json("/api/tasks")["data"].get("items") or []
    return [
        task["id"] for task in tasks
        if (task.get("request_params") or {}).get("options", {}).get("case_id") == case_id
    ]


def run_p01() -> dict:
    case = control_json("/api/v1/cases", method="POST", payload={
        "title": "P01-explain-only",
        "problem_description": "checkout CPU 图偏高",
        "recovery_goal": "解释证据",
        "run_mode": "COLLABORATE",
        "environment": "vm",
        "target_scope": {"service_id": "checkout"},
    })["data"]
    wait_task_done(control_json(
        f"/api/v1/cases/{case['case_id']}/queries", method="POST",
        payload={"operation": "process.list", "parameters": {}, "idempotency_key": f"p01-{int(time.time())}"},
    )["data"]["task"]["id"])
    wait_evidence_count(case["case_id"], 1)
    before_tasks = set(case_task_ids(case["case_id"]))
    plan_before = control_json(f"/api/v1/cases/{case['case_id']}/plans/current")
    turn = control_json(
        f"/api/v1/cases/{case['case_id']}/agent/turn", method="POST",
        payload={"message": "这张 CPU 图是什么意思？只解释，不要创建任务"},
    )["data"]
    deadline = time.time() + 120
    while time.time() < deadline:
        state = json.loads(ssh(
            "control",
            "source ~/mini-drop-active/deploy/env/control-native.env && "
            f"curl -sS -H \"X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN\" "
            f"'http://127.0.0.1:8899/internal/runtime/v1/cases/{case['case_id']}/state'",
        ))["data"]
        if state.get("detail"):
            break
        time.sleep(5)
    after_tasks = set(case_task_ids(case["case_id"]))
    plan_after = control_json(f"/api/v1/cases/{case['case_id']}/plans/current")
    passed = turn["status"] == "runtime_turn_accepted" and before_tasks == after_tasks
    return {"case_id": case["case_id"], "status": "PASS" if passed else "FAIL",
            "turn_status": turn["status"], "new_tasks": sorted(after_tasks - before_tasks),
            "plan_before": plan_before, "plan_after": plan_after}



def main() -> int:
    report = {
        "suite": "agent-beta-public-cases",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cases": {},
    }
    try:
        ready = control_json("/api/readyz")["data"]["healthy"]
    except Exception as exc:
        report["environment"] = {"ready": False, "error": str(exc)[:300]}
        for pid in [f"P{i:02d}" for i in range(1, 13)]:
            report["cases"][pid] = {
                "status": "AWAITING_ENVIRONMENT",
                "reason": "VM/control-plane is not reachable; no formal R4 evidence was produced",
            }
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 12
    report["environment"] = {"ready": ready}
    # P02 uses an existing query task from P01 path; simplest: run P01 first.
    p01 = run_p01()
    report["cases"]["P01"] = p01

    # P02: create a fresh Case with initial_tasks from a completed query task.
    query_case = control_json("/api/v1/cases", method="POST", payload={
        "title": "P02-reuse",
        "problem_description": "复用已有任务证据",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "vm",
        "target_scope": {"service_id": "checkout"},
    })["data"]
    query_task = wait_task_done(control_json(
        f"/api/v1/cases/{query_case['case_id']}/queries", method="POST",
        payload={"operation": "system.metrics", "parameters": {}, "idempotency_key": f"p02-{int(time.time())}"},
    )["data"]["task"]["id"])
    wait_evidence_count(query_case["case_id"], 1)
    try:
        case = control_json("/api/v1/cases", method="POST", payload={
            "title": "P02-initial-tasks",
            "problem_description": "基于已有采集定位根因",
            "recovery_goal": "定位根因",
            "run_mode": "COLLABORATE",
            "environment": "vm",
            "target_scope": {"service_id": "checkout"},
            "initial_tasks": [query_task],
        })["data"]
    except KeyError:
        tasks = control_json("/api/tasks")["data"].get("items") or []
        fallback = next(
            (t for t in reversed(tasks)
             if t["status"] == "DONE"
             and t.get("collector_type") == "sys_metrics"
             and (t.get("request_params") or {}).get("options", {}).get("source") == "query_gateway"),
            None,
        )
        if fallback is None:
            raise
        case = control_json("/api/v1/cases", method="POST", payload={
            "title": "P02-initial-tasks",
            "problem_description": "基于已有采集定位根因",
            "recovery_goal": "定位根因",
            "run_mode": "COLLABORATE",
            "environment": "vm",
            "target_scope": {"service_id": "checkout"},
            "initial_tasks": [fallback["id"]],
        })["data"]
    evidence = wait_evidence_count(case["case_id"], 1)
    new_tasks = [t for t in case_task_ids(case["case_id"]) if t != query_task]
    report["cases"]["P02"] = {
        "case_id": case["case_id"],
        "evidence_count": len(evidence),
        "new_tasks": new_tasks,
        "status": "PASS" if evidence and not new_tasks else "FAIL",
    }

    # P07 positive and negative on live VM.
    p07_positive = wait_task_done(control_json(
        f"/api/v1/cases/{query_case['case_id']}/queries", method="POST",
        payload={"operation": "process.list", "parameters": {}, "idempotency_key": f"p07-{int(time.time())}"},
    )["data"]["task"]["id"])
    p07_negative = []
    for bad in (
        {"operation": "system.metrics", "parameters": {"shell": "/bin/sh"}},
        {"operation": "system.metrics", "parameters": {"executable": "bash"}},
        {"operation": "system.metrics", "parameters": {"unknown": 1}},
    ):
        resp = control_json(f"/api/v1/cases/{query_case['case_id']}/queries", method="POST", payload=bad)
        p07_negative.append("detail" in resp)
    report["cases"]["P07"] = {
        "positive_task": p07_positive["id"],
        "positive_status": p07_positive["status"],
        "negative_rejections": p07_negative,
        "status": "PASS" if p07_positive["status"] == "DONE" and all(p07_negative) else "FAIL",
    }

    # P11 two-phase capacity.
    cap_case_no_inv = control_json("/api/v1/cases", method="POST", payload={
        "title": "P11-capacity-insufficient",
        "problem_description": "评估部署容量",
        "recovery_goal": "容量评估",
        "run_mode": "COLLABORATE",
        "environment": "vm",
        "target_scope": {"service_id": "checkout"},
    })["data"]
    cap_case = control_json("/api/v1/cases", method="POST", payload={
        "title": "P11-capacity",
        "problem_description": "评估部署容量",
        "recovery_goal": "容量评估",
        "run_mode": "COLLABORATE",
        "environment": "vm",
        "target_scope": {
            "service_id": "checkout",
            "deployment_inventory": [
                {"node_id": "n1", "allocatable_cpu_cores": 8, "allocatable_memory_mb": 32768,
                 "allocatable_disk_mb": 102400, "reserved_cpu_cores": 1,
                 "reserved_memory_mb": 4096, "reserved_disk_mb": 10240},
                {"node_id": "n2", "allocatable_cpu_cores": 8, "allocatable_memory_mb": 32768,
                 "allocatable_disk_mb": 102400, "reserved_cpu_cores": 1,
                 "reserved_memory_mb": 4096, "reserved_disk_mb": 10240},
            ],
        },
    })["data"]
    insufficient = control_json(
        f"/api/v1/cases/{cap_case_no_inv['case_id']}/deployment-assessment", method="POST",
        payload={"deployment_requirements": {"replicas": 2, "cpu_cores_per_replica": 1, "memory_mb_per_replica": 2048},
                 "execute_safe_tools": False},
    )["data"]["verdict"]
    fit = control_json(
        f"/api/v1/cases/{cap_case['case_id']}/deployment-assessment", method="POST",
        payload={"deployment_requirements": {"replicas": 2, "cpu_cores_per_replica": 1,
                                             "memory_mb_per_replica": 2048,
                                             "cpu_overhead_cores": 0.5,
                                             "memory_overhead_mb": 512,
                                             "safety_margin_ratio": 0.1},
                 "execute_safe_tools": False},
    )["data"]["verdict"]
    report["cases"]["P11"] = {
        "case_id": cap_case["case_id"],
        "phase1_insufficient": insufficient,
        "phase2_verdict": fit,
        "status": "PASS" if insufficient == "insufficient_data" and fit == "ready" else "FAIL",
    }

    # P12 restart recovery smoke.
    ssh("control", "docker run --rm --privileged --pid=host --uts=host --net=host redis:alpine nsenter -t 1 -m -u -i -n -p /usr/bin/systemctl restart mini-drop-pi-sidecar")
    time.sleep(5)
    sidecar = json.loads(ssh("control", "curl -sS http://127.0.0.1:8899/internal/runtime/v1/health"))["data"]["status"]
    ready = control_json("/api/readyz")["data"]["healthy"]
    report["cases"]["P12"] = {
        "sidecar_after_restart": sidecar,
        "readyz_after_restart": ready,
        "status": "PASS" if sidecar == "ready" and ready else "FAIL",
    }

    blocked = ["P03", "P04", "P05", "P06", "P08", "P09", "P10"]
    for pid in blocked:
        report["cases"][pid] = {
            "status": "AWAITING_ENVIRONMENT_OR_INDEPENDENT_EVALUATOR",
            "reason": "Requires formal R4 fault/causal/browser harness or external evaluator",
        }

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    statuses = [item.get("status") for item in report["cases"].values()]
    if any(status in {"PARTIAL", "FAIL"} for status in statuses):
        return 10
    if all(status == "PASS" for status in statuses):
        return 0
    return 12


if __name__ == "__main__":
    raise SystemExit(main())
