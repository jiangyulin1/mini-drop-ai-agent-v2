#!/usr/bin/env python3
"""Run one real Pi/DeepSeek Evidence-native turn against the JYL Compose lab."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from run_evidence_native_vm_eval import RemoteAPIError, RemoteServer, attach, create_task, data


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_CONFIG = ROOT / "ssh" / "vm-config"


def sidecar_state(server: RemoteServer, case_id: str) -> dict[str, Any]:
    raw = server.run(
        'curl -sS -H "X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN" '
        f"http://pi-sidecar:8899/internal/runtime/v1/cases/{quote(case_id, safe='')}/state",
        timeout=30,
    )
    result = json.loads(raw)
    if not result.get("ok"):
        raise RemoteAPIError(f"sidecar state failed: {result}")
    return result.get("data") or {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-config", type=Path, default=DEFAULT_SSH_CONFIG)
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "reports" / "evaluation" / "evidence-native-vm",
    )
    args = parser.parse_args()
    server = RemoteServer(args.ssh_config)
    report: dict[str, Any] = {
        "schema": "mini-drop.evidence-native-pi-vm-eval.v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "failure": None,
    }
    try:
        health = json.loads(server.run(
            "curl -sS http://pi-sidecar:8899/internal/runtime/v1/health",
            timeout=30,
        ))
        health_data = health.get("data") or {}
        report["checks"]["pi_sidecar_health"] = {
            "pass": health.get("ok") is True and health_data.get("runtime_type") == "pi",
            "runtime_type": health_data.get("runtime_type"),
            "runtime_version": health_data.get("runtime_version"),
            "model_ready": bool(health_data.get("model_ready")),
        }
        agents = data(server.api("/api/agents")).get("items") or []
        online = [item for item in agents if item.get("status") == "ONLINE"]
        if not online:
            raise RemoteAPIError("no online Worker")
        agent_id = str(online[0]["id"])
        case = data(server.api(
            "/api/v1/cases", method="POST", payload={
                "title": "Evidence-native Pi VM acceptance",
                "problem_description": "checkout latency increased; determine the supported mechanism",
                "recovery_goal": "produce an Evidence-bound investigation conclusion",
                "run_mode": "COLLABORATE",
                "environment": "staging",
                "target_scope": {"service_id": "checkout"},
            },
        ))
        case_id = str(case["case_id"])
        branch_id = str(data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/branches",
            method="POST", payload={"label": "Pi CPU investigation", "reason": "pi-vm-eval"},
        ))["branch_id"])
        task_id, _ = create_task(
            server, agent_id=agent_id, branch_id=branch_id, label="pi",
        )
        evidence_id = attach(server, case_id, task_id)
        report["case"] = {
            "case_id": case_id, "branch_id": branch_id,
            "task_id": task_id, "evidence_id": evidence_id,
        }
        visible = data(server.api(
            "/internal/agent/tools/list-case-evidence", method="POST", internal=True,
            payload={"case_id": case_id, "branch_id": branch_id},
        ))
        visible_ids = {str(item.get("evidence_id")) for item in visible.get("items") or []}
        report["checks"]["pi_branch_evidence_scope"] = {
            "pass": visible_ids == {evidence_id},
            "visible_ids": sorted(visible_ids),
        }
        turn = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/agent/turn",
            method="POST",
            payload={
                "branch_id": branch_id,
                "message": (
                    "请只基于当前分支可见 Evidence 调查 checkout 延迟。先读取 Case 快照和 Evidence，"
                    "必要时分析当前 Evidence；如果证据不足必须明确拒答，禁止引用其他分支。"
                ),
                "intent": "investigate",
                "execute_safe_tools": True,
                "max_tool_calls": 4,
                "runtime_options": {
                    "reasoning_effort": "low",
                    "prompt_variant": "evidence_strict",
                    "max_tokens": 1200,
                    "fresh_session": True,
                },
            },
        ))
        report["turn"] = {
            "turn_id": turn.get("turn_id"),
            "accepted_mode": turn.get("accepted_mode"),
            "detail": turn.get("detail"),
        }
        deadline = time.time() + 300
        state: dict[str, Any] = {}
        previous_seq = -1
        stable_polls = 0
        while time.time() < deadline:
            state = sidecar_state(server, case_id)
            if state.get("detail"):
                raise RemoteAPIError(str(state["detail"]))
            runtime_probe = data(server.api(
                f"/api/v1/cases/{quote(case_id, safe='')}/agent/runtime-state",
            ))
            runtime_events_probe = runtime_probe.get("events") or []
            current_seq = int(state.get("last_event_seq") or 0)
            if current_seq == previous_seq and current_seq > 0:
                stable_polls += 1
            else:
                stable_polls = 0
            previous_seq = current_seq
            workspace_probe = data(server.api(
                f"/api/v1/cases/{quote(case_id, safe='')}/workspace?branch_id={quote(branch_id, safe='')}"
            ))
            if workspace_probe.get("conclusion"):
                break
            settled = any(
                item.get("event_type") in {"turn_end", "agent_end", "agent_settled"}
                for item in runtime_events_probe
            )
            if settled and stable_polls >= 5:
                break
            time.sleep(3)
        if int(state.get("last_event_seq") or 0) <= 0:
            raise RemoteAPIError(f"Pi turn did not settle: {state}")
        runtime_state = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/agent/runtime-state",
        ))
        events = runtime_state.get("events") or []
        model_attempts = [
            item for item in events
            if isinstance(item.get("payload"), dict) and item["payload"].get("model_attempt")
        ]
        tool_events = [
            item for item in events
            if item.get("event_type") in {"tool_execution_start", "tool_execution_end"}
        ]
        completed_tools = [
            str((item.get("payload") or {}).get("tool_name") or "")
            for item in events
            if item.get("event_type") == "tool_execution_end"
            and (item.get("payload") or {}).get("tool_name")
        ]
        conclusion = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/workspace?branch_id={quote(branch_id, safe='')}",
        )).get("conclusion") or {}
        conclusion_refs = {
            str(ref)
            for claim in conclusion.get("claims") or []
            for ref in ([claim.get("evidence_id")] + list(claim.get("evidence_refs") or []))
            if ref
        }
        report["checks"]["pi_provider_completion"] = {
            "pass": bool(model_attempts),
            "attempt_count": len(model_attempts),
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        }
        report["checks"]["pi_runtime_audit"] = {
            "pass": bool(events) and int(state.get("last_event_seq") or 0) >= len(events),
            "event_count": len(events),
            "tool_event_count": len(tool_events),
            "completed_tool_count": len(completed_tools),
            "completed_tools": sorted(set(completed_tools)),
            "last_event_seq": state.get("last_event_seq"),
        }
        finish_completed = "finish_investigation" in completed_tools
        report["checks"]["pi_finish_investigation"] = {
            "pass": finish_completed,
            "completed": finish_completed,
        }
        report["checks"]["pi_evidence_bound_conclusion"] = {
            "pass": finish_completed and bool(conclusion) and evidence_id in conclusion_refs,
            "state": conclusion.get("state"),
            "revision": conclusion.get("revision"),
            "evidence_refs": sorted(conclusion_refs),
        }
        report["status"] = "PASS" if all(item.get("pass") for item in report["checks"].values()) else "PARTIAL"
    except Exception as exc:  # noqa: BLE001
        report["status"] = "FAIL"
        report["failure"] = str(exc)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"run-pi-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output), "checks": report["checks"], "failure": report["failure"]}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
