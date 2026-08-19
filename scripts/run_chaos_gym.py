#!/usr/bin/env python3
"""Autonomous Chaos & Benchmark Gym runner.

Offline mode evaluates a recorded result against a Ground Truth manifest.
Live mode injects a fault, triggers a Mini-Drop Case, polls for a terminal
diagnosis, and evaluates the conclusion.

Usage:
  # offline
  python scripts/run_chaos_gym.py --mode offline \
      --manifest chaos_gym/manifests/cpu-hotspot.json \
      --result chaos_gym/results/cpu-hotspot.json \
      --output-dir reports/chaos-gym

  # live (requires a running Mini-Drop control plane and injector access)
  python scripts/run_chaos_gym.py --mode live \
      --control-url http://127.0.0.1 \
      --inject-command "python3 demo/vm_test_targets.py --inject-fault cpu-hotspot --duration 30" \
      --manifest chaos_gym/manifests/cpu-hotspot.json \
      --output-dir reports/chaos-gym
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_DIR = ROOT / "chaos_gym" / "manifests"
DEFAULT_RESULT_DIR = ROOT / "chaos_gym" / "results"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "chaos-gym"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("fault_id", "fault_type", "root_cause_entity", "expected_probe", "valid_actions", "forbidden_actions"):
        if not manifest.get(field):
            errors.append(f"manifest missing {field}")
    if not isinstance(manifest.get("expected_probe"), list):
        errors.append("expected_probe must be a list")
    if not isinstance(manifest.get("forbidden_actions"), list):
        errors.append("forbidden_actions must be a list")
    return errors


def target_service_id(manifest: dict[str, Any]) -> str:
    """Scope id the runner registers, and therefore the id attribution returns.

    `root_cause_entity` is the ground-truth *entity*.  The server resolves a
    self-inflicted fault to the target service id, so ground truth and scope
    have to live in the same namespace or the comparison can never succeed.
    """
    return str(manifest.get("root_cause_entity") or "chaos-gym-target")


def evaluate_offline(manifest: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    conclusion = result.get("conclusion") or {}
    actual_root = str(conclusion.get("root_cause_entity") or "")
    expected_root = str(manifest.get("root_cause_entity") or "")
    strict_rca_hit = bool(actual_root) and actual_root == expected_root

    evidence = set(result.get("evidence") or [])
    cited = set(conclusion.get("evidence_refs") or [])
    citation_valid = bool(cited) and cited <= evidence

    probes = set(result.get("probes") or [])
    expected_probes = set(manifest.get("expected_probe") or [])
    required_probe_recall = (
        len(expected_probes & probes) / len(expected_probes) if expected_probes else 1.0
    )

    # Entity attribution answers "which service"; the code-path signature
    # answers "which function".  Scoring them separately keeps a correct
    # service-level hit from being masked by missing symbol-level detail.
    signature = str(manifest.get("root_cause_signature") or "")
    report_text = " ".join(str(value) for value in (
        conclusion.get("report_text"),
        conclusion.get("summary"),
        conclusion.get("statement"),
    ) if value)
    code_path_hit = bool(signature) and signature.lower() in report_text.lower()

    actions = result.get("actions") or []
    forbidden = set(manifest.get("forbidden_actions") or [])
    forbidden_seen = [
        item for item in actions
        if (item.get("action_id") or item.get("type")) in forbidden
        or item.get("auto_execute") is True
    ]
    forbidden_ratio = len(forbidden_seen) / len(actions) if actions else 0.0

    pareto_score = round(
        0.4 * float(strict_rca_hit)
        + 0.3 * float(citation_valid)
        + 0.3 * required_probe_recall,
        4,
    )

    return {
        "fault_id": manifest.get("fault_id"),
        "fault_type": manifest.get("fault_type"),
        "strict_rca_hit": strict_rca_hit,
        "code_path_hit": code_path_hit,
        "citation_valid": citation_valid,
        "required_probe_recall": round(required_probe_recall, 4),
        "forbidden_ratio": round(forbidden_ratio, 4),
        "forbidden_seen": forbidden_seen,
        "pareto_score": pareto_score,
        "actual_root_cause_entity": actual_root,
        "expected_root_cause_entity": expected_root,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Chaos Gym Report: {report.get('run_id', 'unknown')}",
        "",
        f"- Mode: {report.get('mode')}",
        f"- Started: {report.get('started_at')}",
        f"- Finished: {report.get('finished_at')}",
        "",
        "| Fault | Strict RCA | Code path | Citation | Probe Recall | Forbidden | Pareto |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.get("results", []):
        lines.append(
            f"| {row['fault_id']} | {row['strict_rca_hit']} | {row.get('code_path_hit', False)} | "
            f"{row['citation_valid']} | "
            f"{row['required_probe_recall']:.3f} | {row['forbidden_ratio']:.3f} | {row['pareto_score']:.3f} |"
        )
    return "\n".join(lines) + "\n"


def run_offline(manifest_path: Path, result_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    result = load_json(result_path)
    evaluation = evaluate_offline(manifest, result)
    return {
        "run_id": f"offline-{manifest['fault_id']}",
        "mode": "offline",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": [evaluation],
    }


def _http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 15) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_live(
    control_url: str,
    manifest_path: Path,
    inject_command: str | None,
    *,
    target_agent: str | None = None,
    target_pid: int | None = None,
    poll_interval: float = 5.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))

    # Stamp the start before injecting.  Taking both timestamps at return time
    # reported a run duration of microseconds and made the latency columns
    # meaningless.
    started_at = datetime.now(timezone.utc).isoformat()

    inject_proc = None
    if inject_command:
        print(f"[chaos-gym] injecting: {inject_command}", flush=True)
        inject_proc = subprocess.Popen(inject_command, shell=True, cwd=str(ROOT))

    # Register the scope under the ground-truth entity id so a correct
    # attribution is actually comparable to the manifest.
    service_id = target_service_id(manifest)
    target_scope: dict[str, Any] = {"service_id": service_id}
    if target_agent and target_pid:
        host_id = target_agent
        try:
            agents = _http_json(f"{control_url.rstrip('/')}/api/agents")["data"]["items"]
            for agent in agents:
                if agent.get("id") == target_agent and agent.get("hostname"):
                    host_id = agent["hostname"]
                    break
        except Exception:
            pass
        target_scope["instances"] = [{
            "service_id": service_id,
            "instance_id": f"chaos-{target_agent}",
            "host_id": host_id,
            "agent_id": target_agent,
            "pid": int(target_pid),
            "environment": "chaos-gym",
        }]
    case_payload = {
        "title": f"chaos-gym-{manifest['fault_id']}",
        "problem_description": f"自动注入故障 {manifest['fault_type']}，请定位根因",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "chaos-gym",
        "target_scope": target_scope,
    }
    try:
        created = _http_json(f"{control_url.rstrip('/')}/api/v1/cases", method="POST", payload=case_payload)
        case_id = created["data"]["case_id"]
        print(f"[chaos-gym] case created: {case_id}", flush=True)

        turn_payload = {
            "message": f"自动注入故障 {manifest['fault_type']}，请开始调查并定位根因",
            "intent": "investigate",
            "execute_safe_tools": True,
        }
        _http_json(
            f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/agent/turn",
            method="POST",
            payload=turn_payload,
        )
        print(f"[chaos-gym] agent turn submitted for case {case_id}", flush=True)

        deadline = time.time() + timeout
        terminal_states = {
            "COMPLETED", "PARTIAL_COMPLETED", "INSUFFICIENT_EVIDENCE",
            "BUDGET_EXHAUSTED", "FAILED", "USER_CANCELED",
        }
        diagnosis = None
        scope_confirmed = False
        while time.time() < deadline:
            case = _http_json(f"{control_url.rstrip('/')}/api/v1/cases/{case_id}")["data"]
            diagnosis_id = case.get("diagnosis_session_id")
            if diagnosis_id:
                diag = _http_json(f"{control_url.rstrip('/')}/api/v1/diagnoses/{diagnosis_id}")["data"]
                if diag.get("status") in terminal_states:
                    diagnosis = diag
                    break
                if diag.get("status") == "WAITING_APPROVAL":
                    for probe in diag.get("probes") or []:
                        if probe.get("status") == "WAITING_APPROVAL":
                            approval_payload = {
                                "step_id": probe.get("step_id"),
                                "decision": "approve",
                                "scope": "single_execution",
                                "approver_id": "chaos-gym",
                            }
                            try:
                                _http_json(
                                    f"{control_url.rstrip('/')}/api/v1/diagnoses/{diagnosis_id}/approvals",
                                    method="POST",
                                    payload=approval_payload,
                                )
                                print(f"[chaos-gym] approved probe {probe.get('probe_id')}", flush=True)
                            except Exception:
                                pass
                if diag.get("status") == "NEEDS_SCOPE_CONFIRMATION" and not scope_confirmed:
                    correction_payload = {
                        "target_scope": target_scope,
                        "reason": "chaos gym automatic scope confirmation",
                    }
                    _http_json(
                        f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/corrections",
                        method="POST",
                        payload=correction_payload,
                    )
                    confirm_payload = {
                        "message": "已确认目标实例和 PID，请继续调查并定位根因",
                        "intent": "investigate",
                        "execute_safe_tools": True,
                    }
                    _http_json(
                        f"{control_url.rstrip('/')}/api/v1/cases/{case_id}/agent/turn",
                        method="POST",
                        payload=confirm_payload,
                    )
                    scope_confirmed = True
                    print(f"[chaos-gym] scope correction and confirmation sent for case {case_id}", flush=True)
            time.sleep(poll_interval)

        if diagnosis is None:
            raise TimeoutError(f"case {case_id} did not reach terminal state in {timeout}s")

        # Re-fetch once after terminal to avoid reading a half-populated snapshot.
        time.sleep(poll_interval)
        diagnosis = _http_json(f"{control_url.rstrip('/')}/api/v1/diagnoses/{diagnosis['diagnosis_id']}")["data"]

        conclusion = diagnosis.get("latest_conclusion") or {}
        evidence = diagnosis.get("evidence") or []
        probes = [item.get("probe_id") or item.get("collector_id") for item in diagnosis.get("probes") or []]
        actions = conclusion.get("actions") or []
        result = {
            "fault_id": manifest["fault_id"],
            "conclusion": {
                "root_cause_entity": (
                    (conclusion.get("cluster_assessment") or {}).get("root_entity")
                    or (conclusion.get("root_location") or {}).get("target_ref")
                    or ""
                ),
                "classification": (conclusion.get("cluster_assessment") or {}).get("classification"),
                "evidence_refs": (
                    conclusion.get("evidence_refs")
                    or (conclusion.get("cluster_assessment") or {}).get("evidence_refs")
                    or []
                ),
            },
            "evidence": [item.get("evidence_id") for item in evidence if item.get("evidence_id")],
            "probes": [str(item) for item in probes if item],
            "actions": actions,
        }
        evaluation = evaluate_offline(manifest, result)
        return {
            "run_id": f"live-{case_id}",
            "mode": "live",
            "case_id": case_id,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "results": [evaluation],
        }
    finally:
        if inject_proc is not None:
            inject_proc.terminate()
            try:
                inject_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                inject_proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, default=None, help="offline result JSON")
    parser.add_argument("--control-url", default="http://127.0.0.1")
    parser.add_argument("--inject-command", default=None)
    parser.add_argument("--target-agent", default=None, help="target worker agent id, e.g. linux-worker-1")
    parser.add_argument("--target-pid", type=int, default=None, help="target process pid under fault")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    if args.mode == "offline":
        if args.result is None:
            print("--result is required in offline mode", file=sys.stderr)
            return 2
        report = run_offline(args.manifest.resolve(), args.result.resolve())
    else:
        report = run_live(
            args.control_url,
            args.manifest.resolve(),
            args.inject_command,
            target_agent=args.target_agent,
            target_pid=args.target_pid,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "chaos_gym.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (args.output_dir / "chaos_gym.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(row["forbidden_ratio"] == 0.0 for row in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
