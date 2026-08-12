"""Golden scenarios 评测：纯结构化输入、可离线复现。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.diagnosis.actions import collect_action, inspect_session_action
from server.app.diagnosis.domain_analyzers import analyze_observations, cluster_finding
from server.app.diagnosis.knowledge import retrieve_knowledge
from server.app.diagnosis.reasoner import DEFAULT_REASONER, Reasoner, assess_with_reasoner
from server.app.diagnosis.report_verifier import verify_report


DEFAULT_SCENARIO_ROOT = Path(__file__).resolve().parents[3] / "golden_scenarios"


def load_scenarios(root: Path | None = None) -> list[dict[str, Any]]:
    """Load standalone scenarios and compact scenario-pack JSON arrays."""
    root = root or DEFAULT_SCENARIO_ROOT
    scenarios: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            scenarios.extend(payload)
        else:
            scenarios.append(payload)
    return scenarios


def evaluate_scenario(
    scenario: dict[str, Any],
    *,
    reasoner: Reasoner = DEFAULT_REASONER,
) -> dict[str, Any]:
    observations = scenario["observations"]
    findings = analyze_observations(observations)
    decision = assess_with_reasoner(
        scenario["scope"],
        observations,
        reasoner=reasoner,
        intent={"query": scenario.get("query", "")},
        versions={"feature_builder": "golden-observation.v1"},
    )
    assessment = decision.assessment or {}
    findings.append(cluster_finding(assessment))
    knowledge = retrieve_knowledge(scenario.get("query", ""), findings)
    knowledge_refs = [item["knowledge_id"] for item in knowledge]
    actions = _evaluation_actions(scenario["scenario_id"], observations, assessment)
    evidence_by_ref = {
        ref: {
            "evidence_id": ref,
            "target": dict(obs.get("target", {})),
            "evidence_role": "incident",
            "data_quality": {
                "completeness": "high",
                "domains": _observation_domains(obs),
            },
        }
        for obs in observations
        for ref in obs.get("evidence_refs", [])
    }
    evidence_refs = sorted(evidence_by_ref)
    evidence = [evidence_by_ref[ref] for ref in evidence_refs]
    conclusion = {
        "summary": assessment["summary"],
        "cluster_assessment": assessment,
        "root_location": assessment["root_location"],
        "domain_cause": assessment["domain_cause"],
        "findings": findings,
        "knowledge_refs": knowledge_refs,
        "knowledge_context": knowledge,
        "actions": actions,
        "limitations": [],
        "coverage": {"observation_count": len(observations), "evidence_count": len(evidence)},
    }
    verification = verify_report(conclusion, evidence, scenario["scope"])

    expected = scenario["expected"]
    actual_finding_types = {item["finding_type"] for item in findings}
    actual_collectors = {item.get("collector_type") for item in actions if item.get("collector_type")}
    checks = {
        "classification": _matches(expected["classification"], assessment["classification"]),
        "root_location": _matches(
            expected.get("root_location", assessment["root_location"]["type"]),
            assessment["root_location"]["type"],
        ),
        "domain_cause": _matches(
            expected.get("domain_cause", assessment["domain_cause"]["type"]),
            assessment["domain_cause"]["type"],
        ),
        "compound": expected.get("is_compound", assessment["is_compound"])
        == assessment["is_compound"],
        "contributing_domains": set(expected.get("contributing_domains", [])).issubset({
            item["domain"] for item in assessment.get("contributing_causes", [])
        }),
        "finding_types": set(expected.get("finding_types", [])).issubset(actual_finding_types),
        "forbidden_finding_types": not set(expected.get("forbidden_finding_types", []))
        .intersection(actual_finding_types),
        "knowledge_refs": set(expected.get("knowledge_refs", [])).issubset(set(knowledge_refs)),
        "action_collectors": set(expected.get("action_collectors", [])).issubset(actual_collectors),
        "report_verification": verification["status"] == "passed",
        "no_auto_execute": all(item.get("auto_execute") is False for item in actions),
    }
    return {
        "scenario_id": scenario["scenario_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "expected": expected,
        "actual": {
            "reasoner": {
                "strategy_id": decision.strategy_id,
                "strategy_version": decision.strategy_version,
                "decision_type": decision.decision_type,
            },
            "classification": assessment["classification"],
            "confidence_level": assessment["confidence_level"],
            "finding_types": sorted(actual_finding_types),
            "knowledge_refs": knowledge_refs,
            "action_collectors": sorted(actual_collectors),
            "verification": verification,
        },
    }


def run_evaluation(
    root: Path | None = None,
    *,
    scenario_ids: set[str] | None = None,
    suite: str = "mini-drop-diagnosis-golden-v1",
    reasoner: Reasoner = DEFAULT_REASONER,
) -> dict[str, Any]:
    scenarios = load_scenarios(root)
    if scenario_ids is not None:
        scenarios = [item for item in scenarios if item["scenario_id"] in scenario_ids]
    results = [evaluate_scenario(item, reasoner=reasoner) for item in scenarios]
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    classification_hits = sum(1 for item in results if item["checks"]["classification"])
    return {
        "suite": suite,
        "reasoner": {
            "strategy_id": reasoner.strategy_id,
            "strategy_version": reasoner.strategy_version,
        },
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "metrics": {
            "scenario_pass_rate": round(passed / total, 4) if total else 0,
            "classification_accuracy": round(classification_hits / total, 4) if total else 0,
            "evidence_reference_integrity": round(
                sum(1 for item in results if item["checks"]["report_verification"]) / total, 4,
            ) if total else 0,
            "unsafe_auto_execute_count": sum(
                1 for item in results if not item["checks"]["no_auto_execute"]
            ),
            "root_location_accuracy": _optional_check_rate(results, "root_location", "root_location"),
            "domain_cause_accuracy": _optional_check_rate(results, "domain_cause", "domain_cause"),
        },
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Mini-Drop Diagnosis Golden Evaluation",
        "",
        f"- Scenarios: {report['total']}",
        f"- Passed: {report['passed']}",
        f"- Classification accuracy: {report['metrics']['classification_accuracy']:.2%}",
        f"- Evidence reference integrity: {report['metrics']['evidence_reference_integrity']:.2%}",
        f"- Unsafe auto execute: {report['metrics']['unsafe_auto_execute_count']}",
        "",
        "| Scenario | Result | Classification | Verification |",
        "|---|---|---|---|",
    ]
    for item in report["results"]:
        lines.append(
            f"| {item['scenario_id']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"{item['actual']['classification']} | {item['actual']['verification']['status']} |"
        )
    return "\n".join(lines) + "\n"


def _evaluation_actions(
    diagnosis_id: str,
    observations: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    actions = [inspect_session_action(diagnosis_id, assessment.get("evidence_refs", []))]
    if not observations:
        return actions
    target = observations[0]["target"]
    refs = observations[0].get("evidence_refs", [])
    actions.append(collect_action(
        action_id="act_low_risk_metrics", title="补充低风险系统指标",
        collector_type="sys_metrics", target=target, duration_sec=15, sample_rate=11,
        comment="复核当前判断。", risk_level="R1", evidence_refs=refs, confidence_level="高",
    ))
    if assessment["classification"] in {"self_code_or_process_pressure", "insufficient_evidence"}:
        actions.append(collect_action(
            action_id="act_cpu_profile", title="申请一次 CPU Profile",
            collector_type="perf_cpu", target=target, duration_sec=15, sample_rate=49,
            comment="需要单次人工审批。", risk_level="R2", evidence_refs=refs, confidence_level="中",
        ))
    if assessment["classification"] in {"same_host_noisy_neighbor", "host_resource_contention", "insufficient_evidence"}:
        actions.append(collect_action(
            action_id="act_io_latency", title="申请一次 I/O 延迟探针",
            collector_type="ebpf_io", target=target, duration_sec=15, sample_rate=11,
            comment="需要单次人工审批。", risk_level="R2",
            evidence_refs=assessment.get("evidence_refs", []), confidence_level="中",
        ))
    return actions


def _observation_domains(observation: dict[str, Any]) -> list[str]:
    collector = observation.get("collector_type")
    if collector in {"connection_probe", "database_metrics"}:
        return ["dependency"]
    if collector in {"perf_cpu", "jvm_metrics"}:
        return ["host", "process"]
    if collector == "network_metrics":
        return ["host"]
    return ["host", "process", "container"]


def _matches(expected: Any, actual: Any) -> bool:
    values = expected if isinstance(expected, list) else [expected]
    return actual in values


def _optional_check_rate(
    results: list[dict[str, Any]], expected_key: str, check_key: str,
) -> float | None:
    specified = [item for item in results if expected_key in item["expected"]]
    if not specified:
        return None
    return round(sum(1 for item in specified if item["checks"][check_key]) / len(specified), 4)
