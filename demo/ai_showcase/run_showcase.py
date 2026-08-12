#!/usr/bin/env python3
"""Run the deterministic Mini-Drop AI capability showcase.

This suite intentionally disables external model calls. It demonstrates the
system's deterministic intent fallback, diagnosis analyzers, evidence
verification, action rendering, and safety boundaries.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SHOWCASE_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError

from server.app.diagnosis.actions import collect_action
from server.app.diagnosis.eval_harness import evaluate_scenario, load_scenarios
from server.app.diagnosis.intent import parse_diagnosis_intent
from server.app.diagnosis.probe_registry import list_probes
from server.app.diagnosis.report_verifier import evidence_integrity_hash, verify_report
from server.app.diagnosis.schemas import CreateDiagnosisRequest, DiagnosisAction
from server.app.rca.candidates import generate_candidates
from server.app.rca.models import EvidenceInput


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _result(case_id: str, passed: bool, *, expected: Any = None, actual: Any = None,
            description: str = "") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "description": description,
        "passed": passed,
        "expected": expected,
        "actual": actual,
    }


def _contains_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_expected(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return actual == expected
    return actual == expected


def run_intent_cases() -> dict[str, Any]:
    cases = _load_json(SHOWCASE_ROOT / "scenarios" / "intent_cases.json")
    results = []
    with patch("server.app.diagnosis.intent.is_feature_enabled", return_value=False):
        for case in cases:
            try:
                request = CreateDiagnosisRequest.model_validate(case["request"])
                actual = parse_diagnosis_intent(request).model_dump(mode="json")
                passed = _contains_expected(actual, case["expected"])
            except Exception as exc:  # noqa: BLE001 - a showcase should report every case
                actual = {"error": f"{type(exc).__name__}: {exc}"}
                passed = False
            results.append(_result(
                case["case_id"],
                passed,
                description=case.get("description", ""),
                expected=case["expected"],
                actual=actual,
            ))
    return _group("intent_and_scope", results)


def run_diagnosis_cases() -> dict[str, Any]:
    roots = [
        REPO_ROOT / "golden_scenarios",
        SHOWCASE_ROOT / "scenarios" / "diagnosis",
    ]
    scenarios = [scenario for root in roots for scenario in load_scenarios(root)]
    results = [evaluate_scenario(scenario) for scenario in scenarios]
    return _group("diagnosis_and_attribution", results, case_key="scenario_id")


def run_rca_cases() -> dict[str, Any]:
    cases = _load_json(SHOWCASE_ROOT / "scenarios" / "rca_cases.json")
    results = []
    for case in cases:
        try:
            candidates = generate_candidates(EvidenceInput.model_validate(case["evidence"]))
            actual_ids = [item.candidate_id for item in candidates]
            expected_ids = case["expected_candidate_ids"]
            passed = set(expected_ids).issubset(set(actual_ids))
            actual = {
                "candidate_ids": actual_ids,
                "ranked_candidates": [
                    {
                        "candidate_id": item.candidate_id,
                        "rule_score": item.rule_score,
                        "evidence_refs": item.evidence_refs,
                        "missing_evidence": item.missing_evidence,
                    }
                    for item in candidates
                ],
            }
        except Exception as exc:  # noqa: BLE001 - keep running the full matrix
            passed = False
            actual = {"error": f"{type(exc).__name__}: {exc}"}
        results.append(_result(
            case["case_id"],
            passed,
            description=case.get("description", ""),
            expected={"candidate_ids_include": case["expected_candidate_ids"]},
            actual=actual,
        ))
    return _group("task_rca_candidates", results)


def _safe_action(*, agent_id: str = "agent-safe", pid: int = 26001,
                 collector_type: str = "sys_metrics") -> dict[str, Any]:
    return collect_action(
        action_id="showcase_action",
        title="Showcase safe collection",
        collector_type=collector_type,
        target={
            "service_id": "showcase",
            "instance_id": "showcase-1",
            "host_id": "host-showcase",
            "agent_id": agent_id,
            "pid": pid,
        },
        duration_sec=15,
        sample_rate=11,
        comment="Demonstrate deterministic action rendering.",
        risk_level="R1",
        evidence_refs=[],
        confidence_level="中",
    )


def _verification_issues(conclusion: dict[str, Any], evidence: list[dict[str, Any]],
                         instances: list[dict[str, Any]]) -> list[str]:
    return verify_report(conclusion, evidence, {"instances": instances})["issues"]


def _run_safety_case(case: dict[str, Any]) -> tuple[bool, Any]:
    kind = case["type"]

    if kind == "probe_registry":
        probes = list_probes()
        expected_ids = {
            "host_process_metrics",
            "process_cpu_profile",
            "process_io_latency",
            "process_memory_map",
            "process_log_scan",
            "runtime_thread_snapshot",
            "endpoint_connectivity_probe",
        }
        actual_ids = {item.probe_id for item in probes}
        collectors = {item.runner_task_kind for item in probes}
        passed = (
            actual_ids == expected_ids
            and collectors == {
                "sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps",
                "log_scan", "runtime_snapshot", "connection_probe",
            }
            and all(item.risk_level != "R3" for item in probes)
            and all(item.requires_approval for item in probes if item.risk_level == "R2")
        )
        return passed, {
            "probe_ids": sorted(actual_ids),
            "collector_types": sorted(collectors),
            "r2_requires_approval": all(
                item.requires_approval for item in probes if item.risk_level == "R2"
            ),
            "r3_probe_count": sum(item.risk_level == "R3" for item in probes),
        }

    if kind in {"strict_request_schema", "budget_schema_bound"}:
        payload = case.get("payload") or {
            "query": "service-a CPU high",
            "context": {"service_id": "service-a"},
            "budget": {
                "max_hosts": 999,
                "max_parallel_probes": 999,
                "max_model_calls": 999,
            },
        }
        try:
            CreateDiagnosisRequest.model_validate(payload)
            return False, {"error": "request unexpectedly accepted"}
        except ValidationError as exc:
            error = str(exc)
            return case["expected_error_contains"].lower() in error.lower(), {"error": error}

    if kind == "action_preview_tamper":
        action = _safe_action()
        action["rendered_command"] += " --duration 99"
        issues = _verification_issues(
            {"actions": [action]},
            [],
            [{"agent_id": "agent-safe", "pid": 26001}],
        )
        expected = case["expected_issue_contains"]
        return any(expected.lower() in issue.lower() for issue in issues), {"issues": issues}

    if kind == "evidence_hash_tamper":
        evidence = {
            "evidence_id": "ev-showcase",
            "source_type": "derived_artifact",
            "source_system": "agent",
            "evidence_role": "incident",
            "target": {"agent_id": "agent-safe", "pid": 26001},
            "event_time_range": {},
            "ingestion_time": datetime.now(timezone.utc),
            "query_or_probe": "sys_metrics",
            "raw_artifact_ref": None,
            "derived_artifact_ref": "showcase.json",
            "derivation_version": "v2",
            "observed_value": {"cpu": 10},
            "baseline_value": {},
            "anomaly_score": {},
            "data_quality": {"domains": ["host"]},
            "claim_links": [],
        }
        evidence["integrity_hash"] = evidence_integrity_hash(evidence)
        evidence["observed_value"]["cpu"] = 99
        issues = _verification_issues(
            {
                "root_location": {
                    "type": "self",
                    "target_ref": "showcase-1",
                    "evidence_refs": ["ev-showcase"],
                },
                "actions": [],
            },
            [evidence],
            [{"agent_id": "agent-safe", "pid": 26001}],
        )
        expected = case["expected_issue_contains"]
        return any(expected.lower() in issue.lower() for issue in issues), {"issues": issues}

    if kind == "unknown_evidence":
        issues = _verification_issues(
            {
                "root_location": {
                    "type": "self",
                    "target_ref": "showcase-1",
                    "evidence_refs": ["ev-does-not-exist"],
                },
                "actions": [],
            },
            [],
            [{"agent_id": "agent-safe", "pid": 26001}],
        )
        expected = case["expected_issue_contains"]
        return any(expected.lower() in issue.lower() for issue in issues), {"issues": issues}

    if kind in {"out_of_scope_action", "unknown_collector"}:
        action = _safe_action(
            agent_id="agent-outside" if kind == "out_of_scope_action" else "agent-safe",
            collector_type="arbitrary_shell" if kind == "unknown_collector" else "sys_metrics",
        )
        issues = _verification_issues(
            {"actions": [action]},
            [],
            [{"agent_id": "agent-safe", "pid": 26001}],
        )
        marker = "范围" if kind == "out_of_scope_action" else "注册"
        passed = any(
            case["expected_issue_contains"].lower() in issue.lower() and marker in issue
            for issue in issues
        )
        return passed, {"issues": issues}

    if kind == "auto_execute_forbidden":
        action = _safe_action()
        action = {
            key: value for key, value in action.items()
            if key not in {"command_id", "command", "confidence"}
        }
        action["auto_execute"] = True
        try:
            DiagnosisAction.model_validate(action)
            return False, {"error": "action unexpectedly accepted"}
        except ValidationError as exc:
            error = str(exc)
            return case["expected_error_contains"].lower() in error.lower(), {"error": error}

    return False, {"error": f"unknown safety case type: {kind}"}


def run_safety_cases() -> dict[str, Any]:
    cases = _load_json(SHOWCASE_ROOT / "scenarios" / "safety_cases.json")
    results = []
    for case in cases:
        try:
            passed, actual = _run_safety_case(case)
        except Exception as exc:  # noqa: BLE001 - keep running the full matrix
            passed, actual = False, {"error": f"{type(exc).__name__}: {exc}"}
        results.append(_result(
            case["case_id"],
            passed,
            description=case.get("description", ""),
            actual=actual,
        ))
    return _group("evidence_and_safety", results)


def _group(name: str, results: list[dict[str, Any]], *,
           case_key: str = "case_id") -> dict[str, Any]:
    normalized = []
    for item in results:
        if case_key != "case_id":
            item = deepcopy(item)
            item["case_id"] = item.pop(case_key)
        normalized.append(item)
    passed = sum(bool(item.get("passed")) for item in normalized)
    return {
        "name": name,
        "total": len(normalized),
        "passed": passed,
        "failed": len(normalized) - passed,
        "results": normalized,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Mini-Drop AI 演示测试报告",
        "",
        f"- 总用例：{report['summary']['total']}",
        f"- 通过：{report['summary']['passed']}",
        f"- 失败：{report['summary']['failed']}",
        f"- 通过率：{report['summary']['pass_rate']:.2%}",
        f"- 外部模型调用：{report['summary']['external_model_calls']}",
        "",
        "| 能力组 | 用例 | 结果 | 实际摘要 |",
        "|---|---|---|---|",
    ]
    for group in report["groups"]:
        for item in group["results"]:
            actual = item.get("actual", {})
            if isinstance(actual, dict):
                digest = (
                    actual.get("classification")
                    or actual.get("symptom")
                    or (
                        ",".join(actual["candidate_ids"][:2])
                        if "candidate_ids" in actual
                        else ""
                    )
                    or ("issues=" + str(len(actual["issues"])) if "issues" in actual else "")
                )
            else:
                digest = str(actual)
            lines.append(
                f"| {group['name']} | {item['case_id']} | "
                f"{'PASS' if item['passed'] else 'FAIL'} | {digest} |"
            )
    lines.extend([
        "",
        "## 解释边界",
        "",
        "离线套件证明的是 Mini-Drop 自身的确定性诊断、证据约束和安全编排能力；"
        "它刻意不调用外部大模型。外部模型连通性请使用 `run_live_showcase.py` 单独检查。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optionally write the machine-readable result to this JSON file.",
    )
    args = parser.parse_args()

    groups = [
        run_intent_cases(),
        run_diagnosis_cases(),
        run_rca_cases(),
        run_safety_cases(),
    ]
    total = sum(group["total"] for group in groups)
    passed = sum(group["passed"] for group in groups)
    report = {
        "suite": "mini-drop-ai-showcase-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0,
            "external_model_calls": 0,
        },
        "groups": groups,
    }
    markdown = _markdown(report)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    print(markdown)
    return 0 if total == passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
