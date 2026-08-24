#!/usr/bin/env python3
"""Run the Evidence-native acceptance path against the JYL control container.

The runner deliberately talks to the Server container over SSH.  It never
reads or prints an API/provider secret; the container expands its protected
environment when making API requests.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SSH_CONFIG = ROOT / "ssh" / "vm-config"
SERVER_CONTAINER = "mini-drop-jyl-control-server-1"


class RemoteAPIError(RuntimeError):
    pass


class RemoteServer:
    def __init__(self, ssh_config: Path):
        self.ssh_config = ssh_config.resolve()

    def run(self, inner: str, timeout: int = 180) -> str:
        remote = f"docker exec {SERVER_CONTAINER} sh -c {shlex.quote(inner)}"
        proc = subprocess.run(
            [
                "ssh", "-F", str(self.ssh_config), "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10", "control", remote,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode:
            raise RemoteAPIError(proc.stderr[-1000:] or proc.stdout[-1000:])
        return proc.stdout.strip()

    def api(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        internal: bool = False,
    ) -> dict[str, Any]:
        encoded = base64.b64encode(
            json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        header = (
            '"X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN"'
            if internal
            else '"X-API-Key: $MINI_DROP_API_KEY"'
        )
        method_arg = f"-X {shlex.quote(method)}" if method != "GET" else ""
        prelude = ""
        body_arg = ""
        if method != "GET":
            prelude = (
                f"printf %s {shlex.quote(encoded)} | base64 -d > /tmp/md-eval-payload.json; "
            )
            body_arg = "--data-binary @/tmp/md-eval-payload.json"
        url = f"http://127.0.0.1:8191{path}"
        command = (
            "set -eu; " + prelude +
            f"curl -sS {method_arg} -H {header} "
            "-H 'Content-Type: application/json' "
            f"{body_arg} {shlex.quote(url)}; "
            "rm -f /tmp/md-eval-payload.json"
        )
        raw = self.run(command)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RemoteAPIError(f"invalid JSON from {path}: {raw[:300]}") from exc
        if int(result.get("code") or 0) not in {0, 200}:
            raise RemoteAPIError(f"{method} {path}: {result}")
        return result


def data(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("data")
    if not isinstance(value, dict):
        raise RemoteAPIError(f"missing object data: {result}")
    return value


def wait_task(server: RemoteServer, task_id: str, timeout: int = 150) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = data(server.api(f"/api/tasks/{quote(task_id, safe='')}"))
        if last.get("status") in {"DONE", "FAILED", "CANCELLED"}:
            return last
        time.sleep(2)
    raise RemoteAPIError(f"task did not settle: {task_id}, last={last}")


def create_task(
    server: RemoteServer,
    *,
    agent_id: str,
    branch_id: str,
    label: str,
) -> tuple[str, dict[str, Any]]:
    created = data(server.api(
        "/api/tasks",
        method="POST",
        payload={
            "name": f"evidence-native-vm-{label}",
            "agent_id": agent_id,
            "target_pid": 1,
            "collector_type": "sys_metrics",
            "sample_rate": 5,
            "duration_sec": 5,
            "options": {
                "source": "evidence-native-vm-eval",
                "branch_id": branch_id,
                "evaluation_label": label,
            },
        },
    ))
    task_id = str(created.get("task_id") or created.get("id") or "")
    if not task_id:
        raise RemoteAPIError(f"task id missing: {created}")
    task = wait_task(server, task_id)
    if task.get("status") != "DONE":
        raise RemoteAPIError(f"task failed: {task}")
    deadline = time.time() + 60
    artifacts: list[dict[str, Any]] = []
    while time.time() < deadline:
        artifact_result = server.api(f"/api/tasks/{quote(task_id, safe='')}/artifacts")
        artifact_value = artifact_result.get("data")
        artifacts = artifact_value if isinstance(artifact_value, list) else (artifact_value or {}).get("items", [])
        if artifacts:
            break
        time.sleep(2)
    if not artifacts:
        raise RemoteAPIError(f"task completed without artifacts: {task_id}")
    return task_id, task


def attach(server: RemoteServer, case_id: str, task_id: str) -> str:
    items = data(server.api(
        f"/api/v1/cases/{quote(case_id, safe='')}/attachments",
        method="POST",
        payload={
            "references": [{"type": "task", "id": task_id}],
            "purpose": "evidence-native-vm-eval",
        },
    )).get("items") or []
    accepted = next((item for item in items if item.get("result") == "ACCEPTED"), None)
    if not accepted or not accepted.get("evidence_ids"):
        raise RemoteAPIError(f"task attachment was not accepted: {items}")
    return str(accepted["evidence_ids"][0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-config", type=Path, default=DEFAULT_SSH_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "evaluation" / "evidence-native-vm")
    args = parser.parse_args()
    server = RemoteServer(args.ssh_config)
    report: dict[str, Any] = {
        "schema": "mini-drop.evidence-native-vm-eval.v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "failure": None,
    }
    try:
        agents = data(server.api("/api/agents")).get("items") or []
        online = [item for item in agents if item.get("status") == "ONLINE"]
        if not online:
            raise RemoteAPIError("no online Worker")
        agent_id = str(online[0]["id"])
        report["checks"]["online_worker"] = {"pass": True, "agent_id": agent_id}

        case = data(server.api(
            "/api/v1/cases",
            method="POST",
            payload={
                "title": "Evidence-native VM acceptance",
                "problem_description": "checkout latency increased during a controlled acceptance run",
                "recovery_goal": "validate evidence lifecycle and branch isolation",
                "run_mode": "COLLABORATE",
                "environment": "staging",
                "target_scope": {"service_id": "checkout"},
            },
        ))
        case_id = str(case["case_id"])
        branch_a = str(data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/branches",
            method="POST",
            payload={"label": "CPU hypothesis", "reason": "vm-eval-a"},
        ))["branch_id"])
        branch_b = str(data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/branches",
            method="POST",
            payload={"label": "Independent hypothesis", "reason": "vm-eval-b"},
        ))["branch_id"])
        report["case"] = {"case_id": case_id, "branches": [branch_a, branch_b]}

        task_a, _ = create_task(server, agent_id=agent_id, branch_id=branch_a, label="a")
        task_b, _ = create_task(server, agent_id=agent_id, branch_id=branch_b, label="b")
        evidence_a = attach(server, case_id, task_a)
        evidence_b = attach(server, case_id, task_b)
        report["tasks"] = {"a": task_a, "b": task_b}
        report["evidence"] = {"a": evidence_a, "b": evidence_b}

        visible_a = data(server.api(
            "/internal/agent/tools/list-case-evidence",
            method="POST",
            internal=True,
            payload={"case_id": case_id, "branch_id": branch_a},
        )).get("items") or []
        visible_b = data(server.api(
            "/internal/agent/tools/list-case-evidence",
            method="POST",
            internal=True,
            payload={"case_id": case_id, "branch_id": branch_b},
        )).get("items") or []
        ids_a = {str(item.get("evidence_id")) for item in visible_a}
        ids_b = {str(item.get("evidence_id")) for item in visible_b}
        isolation_pass = ids_a == {evidence_a} and ids_b == {evidence_b}
        report["checks"]["branch_evidence_isolation"] = {
            "pass": isolation_pass,
            "branch_a_ids": sorted(ids_a),
            "branch_b_ids": sorted(ids_b),
        }
        if not isolation_pass:
            raise RemoteAPIError(f"branch evidence isolation failed: {ids_a=} {ids_b=}")

        envelope = {
            "case_id": case_id,
            "branch_id": branch_a,
            "expected_scope_revision": case["scope_revision"],
            "expected_control_revision": case["control_revision"],
        }
        hypotheses = server.api(
            "/internal/agent/tools/hypotheses",
            method="POST",
            internal=True,
            payload={**envelope, "hypotheses": [{
                "hypothesis_id": "cpu-saturation",
                "statement": "CPU saturation increases checkout latency",
                "status": "SUPPORTED",
                "supporting_evidence_refs": [evidence_a],
            }]},
        )
        graph = server.api(
            "/internal/agent/tools/causal-graph",
            method="POST",
            internal=True,
            payload={
                **envelope,
                "expected_evidence_watermark": 1,
                "nodes": [
                    {"node_id": "cpu", "entity_ref": "service:checkout", "mechanism": "CPU saturation", "role": "PRIMARY_ROOT_CAUSE", "supporting_evidence_refs": [evidence_a]},
                    {"node_id": "latency", "entity_ref": "service:checkout", "mechanism": "request latency", "role": "SYMPTOM", "supporting_evidence_refs": [evidence_a]},
                ],
                "edges": [{"edge_id": "cpu-latency", "source_node_id": "cpu", "target_node_id": "latency", "relation": "CAUSES", "supporting_evidence_refs": [evidence_a]}],
            },
        )
        finished = data(server.api(
            "/internal/agent/tools/finish",
            method="POST",
            internal=True,
            payload={
                **envelope,
                "summary": "CPU saturation is the supported mechanism",
                "state": "CONFIRMED",
                "evidence_ids": [evidence_a],
                "primary_root_causes": [{"summary": "CPU saturation"}],
            },
        ))
        conclusion_state = str(finished.get("state") or "")
        report["checks"]["branch_reasoning_and_conclusion"] = {
            "pass": bool(
                data(hypotheses).get("graph", {}).get("hypotheses")
                and data(graph).get("graph", {}).get("branch_id") == branch_a
                and conclusion_state in {"PARTIALLY_CONFIRMED", "CONFIRMED"}
            ),
            "state": conclusion_state,
            "hypothesis_branch_id": data(hypotheses).get("graph", {}).get("branch_id"),
            "causal_graph_branch_id": data(graph).get("graph", {}).get("branch_id"),
        }

        workspace = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/workspace?branch_id={quote(branch_a, safe='')}"
        ))
        report["checks"]["workspace_projection"] = {
            "pass": workspace.get("branch_id") == branch_a and bool(workspace.get("evidence")) and bool(workspace.get("hypothesis_graph", {}).get("hypotheses")),
            "branch_id": workspace.get("branch_id"),
            "conclusion_state": (workspace.get("conclusion") or {}).get("state"),
        }

        preview = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/evidence/{quote(evidence_a, safe='')}/reviews/preview",
            method="POST",
            payload={"decision": "EXCLUDED", "assessment": {}},
        ))
        reviewed = server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/evidence/{quote(evidence_a, safe='')}/reviews",
            method="POST",
            payload={
                "evidence_id": evidence_a,
                "decision": "EXCLUDED",
                "expected_review_revision": preview["current_review_revision"],
                "impact_token": preview["impact_token"],
                "reason_code": "VM_EVAL_EXCLUDED",
                "reason": "controlled Evidence-native acceptance review",
                "assessment": {},
            },
        )
        post_review = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/workspace?branch_id={quote(branch_a, safe='')}"
        ))
        branch_b_post_review = data(server.api(
            f"/api/v1/cases/{quote(case_id, safe='')}/workspace?branch_id={quote(branch_b, safe='')}"
        ))
        report["checks"]["review_invalidation"] = {
            "pass": bool(
                data(reviewed).get("decision") == "EXCLUDED"
                and (post_review.get("conclusion") or {}).get("state") == "RECHECK_REQUIRED"
                and (post_review.get("conclusion") or {}).get("revision") == 2
                and (branch_b_post_review.get("conclusion") or {}).get("state") in {"PARTIALLY_CONFIRMED", "CONFIRMED"}
                and (branch_b_post_review.get("conclusion") or {}).get("revision") == 1
            ),
            "conclusion_state": (post_review.get("conclusion") or {}).get("state"),
            "conclusion_revision": (post_review.get("conclusion") or {}).get("revision"),
            "branch_b_state": (branch_b_post_review.get("conclusion") or {}).get("state"),
            "branch_b_revision": (branch_b_post_review.get("conclusion") or {}).get("revision"),
        }
        report["status"] = "PASS" if all(item.get("pass") for item in report["checks"].values()) else "PARTIAL"
    except Exception as exc:  # noqa: BLE001 - report the first reproducible failure
        report["status"] = "FAIL"
        report["failure"] = str(exc)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(output), "checks": report["checks"], "failure": report["failure"]}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
