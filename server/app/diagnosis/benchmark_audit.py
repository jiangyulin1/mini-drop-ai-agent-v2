"""Static quality and environment-readiness audit for diagnosis benchmark cases.

This module deliberately does not execute a diagnosis.  It answers the earlier
question: is a case sufficiently specified and supported by the selected lab to
produce a meaningful AI evaluation result?
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DIMENSION_WEIGHTS = {
    "performance_requirements": 20.0,
    "oracle_quality": 20.0,
    "leakage_protection": 15.0,
    "reproducibility": 15.0,
    "environment_fit": 15.0,
    "case_resolution_loop": 15.0,
}
READINESS_ORDER = {"UNSUPPORTED": 0, "PARTIAL": 1, "RUNNABLE": 2}
LIFECYCLE_STEPS = ("setup", "baseline", "inject", "incident", "recover", "verify", "cleanup")
CAUSAL_ORACLE_FIELDS = (
    "incident_trigger",
    "root_mechanism",
    "root_entity",
    "affected_entities",
    "propagation_path",
    "symptom",
    "recovery_criteria",
)


class BenchmarkAuditError(ValueError):
    """Raised when the benchmark package cannot be audited safely."""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    recommendation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "recommendation": self.recommendation,
        }


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkAuditError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkAuditError(f"Expected a JSON object in {path}")
    return value


def load_cases(dataset_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset_root = dataset_root.resolve()
    manifest_path = dataset_root / "manifest.json"
    cases_root = dataset_root / "cases"
    if not manifest_path.is_file() or not cases_root.is_dir():
        raise BenchmarkAuditError(
            f"{dataset_root} must contain manifest.json and a cases directory"
        )
    manifest = load_json(manifest_path)
    case_paths = sorted(cases_root.glob("*.json"))
    if not case_paths:
        raise BenchmarkAuditError(f"No case JSON files found under {cases_root}")
    cases = [load_json(path) for path in case_paths]
    _validate_case_identity(manifest, cases)
    return manifest, cases


def _validate_case_identity(manifest: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    ids = [str(case.get("case_id", "")) for case in cases]
    if any(not case_id for case_id in ids):
        raise BenchmarkAuditError("Every case must have a non-empty case_id")
    if len(ids) != len(set(ids)):
        raise BenchmarkAuditError("case_id values must be unique")
    manifest_ids = {
        str(item.get("case_id"))
        for item in manifest.get("core_cases", [])
        if isinstance(item, dict) and item.get("case_id")
    }
    if manifest_ids and manifest_ids != set(ids):
        missing = sorted(manifest_ids - set(ids))
        extra = sorted(set(ids) - manifest_ids)
        raise BenchmarkAuditError(
            f"Manifest/case mismatch; missing files={missing}, unlisted files={extra}"
        )


def audit_dataset(dataset_root: Path, environment_path: Path) -> dict[str, Any]:
    manifest, cases = load_cases(dataset_root)
    environment = load_json(environment_path.resolve())
    case_results = [audit_case(case, environment) for case in cases]
    dimension_scores = {
        key: round(sum(item["scores"][key] for item in case_results) / len(case_results), 2)
        for key in DIMENSION_WEIGHTS
    }
    overall = round(sum(dimension_scores.values()), 2)
    readiness_counts = {
        state: sum(item["readiness"] == state for item in case_results)
        for state in ("RUNNABLE", "PARTIAL", "UNSUPPORTED")
    }
    blockers = sum(
        finding["severity"] == "BLOCKER"
        for item in case_results
        for finding in item["findings"]
    )
    return {
        "audit_schema_version": "1.0",
        "dataset": manifest.get("dataset", dataset_root.name),
        "dataset_version": manifest.get("version", "unknown"),
        "environment_id": environment.get("environment_id", environment_path.stem),
        "case_count": len(case_results),
        "score": overall,
        "maximum_score": 100.0,
        "score_kind": "case_specification_and_environment_readiness",
        "dimension_scores": dimension_scores,
        "readiness_counts": readiness_counts,
        "blocking_finding_count": blockers,
        "formal_ai_accuracy_measured": False,
        "cases": case_results,
        "next_gate": (
            "Split public input from private trigger/oracle, add executable fixtures and "
            "quantified baseline/incident/recovery criteria before active AI trials."
        ),
    }


def audit_case(case: dict[str, Any], environment: dict[str, Any]) -> dict[str, Any]:
    findings: list[Finding] = []
    scores = {
        "performance_requirements": _score_performance_requirements(case, findings),
        "oracle_quality": _score_oracle(case, findings),
        "leakage_protection": _score_leakage(case, findings),
        "reproducibility": _score_reproducibility(case, findings),
        "environment_fit": 0.0,
        "case_resolution_loop": _score_resolution_loop(case, findings),
    }
    readiness, env_score, missing = _environment_readiness(case, environment, findings)
    scores["environment_fit"] = env_score
    return {
        "case_id": case.get("case_id", "UNKNOWN"),
        "title": case.get("title", ""),
        "fault_type": case.get("fault_type", ""),
        "readiness": readiness,
        "missing_capabilities": missing,
        "score": round(sum(scores.values()), 2),
        "scores": {key: round(value, 2) for key, value in scores.items()},
        "findings": [item.as_dict() for item in _deduplicate_findings(findings)],
    }


def _score_performance_requirements(case: dict[str, Any], findings: list[Finding]) -> float:
    score = 0.0
    query = str(case.get("query", "")).strip()
    if query:
        score += 2.0
    requirements = case.get("performance_requirements")
    if not isinstance(requirements, dict):
        findings.append(Finding(
            "PERF_REQUIREMENTS_MISSING", "BLOCKER",
            "No machine-readable performance_requirements object is defined.",
            "Define workload, baseline, incident, measurement and recovery thresholds.",
        ))
        return score
    checks = (
        ("workload", 4.0),
        ("baseline", 3.0),
        ("incident", 3.0),
        ("measurement_window", 3.0),
        ("recovery", 3.0),
        ("slo", 2.0),
    )
    for key, points in checks:
        if _non_empty(requirements.get(key)):
            score += points
        else:
            findings.append(Finding(
                f"PERF_{key.upper()}_MISSING", "ERROR",
                f"Performance requirement {key!r} is not defined.",
                f"Add a deterministic and measurable {key} contract.",
            ))
    return min(score, DIMENSION_WEIGHTS["performance_requirements"])


def _score_oracle(case: dict[str, Any], findings: list[Finding]) -> float:
    oracle = case.get("oracle")
    if not isinstance(oracle, dict):
        findings.append(Finding(
            "ORACLE_MISSING", "BLOCKER", "The case has no structured oracle.",
            "Add a private causal oracle and accepted answer aliases.",
        ))
        return 0.0
    score = 0.0
    legacy = (
        "expected_scope", "expected_root_cause", "expected_terminal_class",
        "expected_location_type", "expected_domain_type", "expected_classification",
    )
    score += 8.0 * sum(_non_empty(oracle.get(key)) for key in legacy) / len(legacy)
    score += 8.0 * sum(_non_empty(oracle.get(key)) for key in CAUSAL_ORACLE_FIELDS) / len(
        CAUSAL_ORACLE_FIELDS
    )
    if _non_empty(oracle.get("accepted_answers")):
        score += 2.0
    if _non_empty(oracle.get("forbidden_claims")):
        score += 1.0
    if _non_empty(oracle.get("expected_actions")):
        score += 1.0
    missing = [key for key in CAUSAL_ORACLE_FIELDS if not _non_empty(oracle.get(key))]
    if missing:
        findings.append(Finding(
            "ORACLE_CAUSAL_LAYERS_MISSING", "ERROR",
            "Oracle collapses trigger, mechanism, entity, propagation and recovery into a label.",
            "Add private causal fields: " + ", ".join(missing) + ".",
        ))
    return min(score, DIMENSION_WEIGHTS["oracle_quality"])


def _score_leakage(case: dict[str, Any], findings: list[Finding]) -> float:
    score = 15.0
    leaked = [key for key in ("trigger", "evidence_plan", "oracle") if key in case]
    if leaked:
        score -= 12.0
        findings.append(Finding(
            "PUBLIC_PRIVATE_NOT_SEPARATED", "BLOCKER",
            "AI-visible case JSON contains private trigger, evidence plan or oracle fields.",
            "Store only the user-visible input under cases/public and move trigger/oracle to cases/private.",
        ))
    query = str(case.get("query", "")).lower()
    oracle_text = json.dumps(case.get("oracle", {}), ensure_ascii=False).lower()
    trigger_text = json.dumps(case.get("trigger", {}), ensure_ascii=False).lower()
    fault_type = str(case.get("fault_type", "")).lower().replace("_", " ")
    if fault_type and fault_type in query:
        score -= 1.0
    if any(token and token in query for token in _diagnostic_tokens(oracle_text, trigger_text)):
        score -= 1.0
        findings.append(Finding(
            "QUERY_HINTS_AT_ANSWER", "WARNING",
            "The user query names a likely target, domain or mechanism.",
            "Rewrite the public query as an operator symptom without revealing the answer.",
        ))
    return max(score, 0.0)


def _score_reproducibility(case: dict[str, Any], findings: list[Finding]) -> float:
    score = 0.0
    execution = case.get("execution", {})
    if isinstance(execution, dict):
        if int(execution.get("warmup_runs", 0) or 0) >= 1:
            score += 1.0
        if int(execution.get("repetitions", 0) or 0) >= 3:
            score += 2.0
        if int(execution.get("timeout_seconds", 0) or 0) > 0:
            score += 1.0
    trigger = case.get("trigger", {})
    if isinstance(trigger, dict) and trigger.get("action"):
        score += 2.0
    if isinstance(trigger, dict) and trigger.get("adapter") not in (None, "implementation_defined"):
        score += 2.0
    else:
        findings.append(Finding(
            "TRIGGER_IMPLEMENTATION_DEFINED", "ERROR",
            "Fault injection is implementation-defined and cannot guarantee an equivalent experiment.",
            "Provide a versioned adapter or fixture script with fixed parameters.",
        ))
    lifecycle = case.get("lifecycle")
    present = 0
    if isinstance(lifecycle, dict):
        present = sum(_non_empty(lifecycle.get(step)) for step in LIFECYCLE_STEPS)
        score += 7.0 * present / len(LIFECYCLE_STEPS)
    if present != len(LIFECYCLE_STEPS):
        findings.append(Finding(
            "EXECUTABLE_LIFECYCLE_INCOMPLETE", "BLOCKER",
            "The full setup/baseline/inject/incident/recover/verify/cleanup lifecycle is not executable.",
            "Provide allowlisted commands or scripts for every lifecycle phase.",
        ))
    return min(score, DIMENSION_WEIGHTS["reproducibility"])


def _score_resolution_loop(case: dict[str, Any], findings: list[Finding]) -> float:
    score = 0.0
    roles = set(case.get("evidence_plan", {}).get("snapshot_roles", []))
    score += 1.0 if "baseline" in roles else 0.0
    score += 1.0 if "verification" in roles else 0.0
    protocol = case.get("session_protocol")
    if isinstance(protocol, dict):
        score += 3.0 if _non_empty(protocol.get("investigation_steps")) else 0.0
        score += 2.0 if _non_empty(protocol.get("candidate_updates")) else 0.0
        score += 2.0 if _non_empty(protocol.get("policy_decisions")) else 0.0
        score += 2.0 if _non_empty(protocol.get("human_interventions")) else 0.0
        score += 2.0 if _non_empty(protocol.get("fix_result")) else 0.0
        score += 2.0 if _non_empty(protocol.get("verification_result")) else 0.0
    if not isinstance(protocol, dict):
        findings.append(Finding(
            "SESSION_OUTPUT_NOT_DEFINED", "ERROR",
            "The case records a final diagnosis only, not the investigation and recovery process.",
            "Define session steps, candidate changes, policy decisions, interventions, fix and verification output.",
        ))
    return min(score, DIMENSION_WEIGHTS["case_resolution_loop"])


def _environment_readiness(
    case: dict[str, Any], environment: dict[str, Any], findings: list[Finding]
) -> tuple[str, float, list[str]]:
    required = list(case.get("evidence_plan", {}).get("required_evidence", []))
    capability_map = environment.get("evidence_capabilities", {})
    supported = [item for item in required if capability_map.get(item) == "SUPPORTED"]
    missing = [item for item in required if capability_map.get(item) != "SUPPORTED"]
    required_hosts = int(case.get("topology", {}).get("minimum_hosts", 1) or 1)
    worker_hosts = int(environment.get("topology", {}).get("worker_hosts", 0) or 0)
    if required_hosts > worker_hosts:
        missing.append(f"worker_hosts>={required_hosts}")
    if not missing:
        readiness, score = "RUNNABLE", 15.0
    elif supported and required_hosts <= worker_hosts:
        readiness, score = "PARTIAL", 7.5
    else:
        readiness, score = "UNSUPPORTED", 0.0
    if missing:
        findings.append(Finding(
            "ENVIRONMENT_CAPABILITY_GAP",
            "ERROR" if readiness == "UNSUPPORTED" else "WARNING",
            "Selected environment cannot collect all required evidence: " + ", ".join(missing) + ".",
            "Add the data source or mark this case PARTIAL/UNSUPPORTED; do not count it as an AI failure.",
        ))
    return readiness, score, sorted(set(missing))


def _diagnostic_tokens(*values: str) -> Iterable[str]:
    vocabulary = (
        "ad service", "email service", "payment service", "hot function", "cpu",
        "memory", "kafka", "downstream", "shared io", "same-host", "peer",
    )
    combined = " ".join(values)
    return (token for token in vocabulary if token in combined)


def _non_empty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    unique: dict[str, Finding] = {}
    for finding in findings:
        unique.setdefault(finding.code, finding)
    return list(unique.values())


def render_markdown(report: dict[str, Any]) -> str:
    scores = report["dimension_scores"]
    lines = [
        "# Mini-Drop 统一诊断测试集质量审计",
        "",
        f"- 测试集：`{report['dataset']}` v{report['dataset_version']}",
        f"- 环境：`{report['environment_id']}`",
        f"- Case 数：{report['case_count']}",
        f"- 成熟度与环境就绪度：**{report['score']:.2f} / 100**",
        "- 本报告不代表 AI 正确率；它只衡量题目成熟度与环境就绪度",
        "",
        "## 六维评分",
        "",
        "| 维度 | 得分 | 满分 |",
        "|---|---:|---:|",
    ]
    labels = {
        "performance_requirements": "性能需求定义",
        "oracle_quality": "Oracle 质量",
        "leakage_protection": "答案泄漏防护",
        "reproducibility": "可复现性",
        "environment_fit": "三节点环境适配",
        "case_resolution_loop": "持续诊断与问题解决",
    }
    for key, maximum in DIMENSION_WEIGHTS.items():
        lines.append(f"| {labels[key]} | {scores[key]:.2f} | {maximum:.0f} |")
    lines.extend([
        "",
        "## 环境就绪度",
        "",
        "| Case | 状态 | 总分 | 缺失能力 |",
        "|---|---|---:|---|",
    ])
    for item in report["cases"]:
        missing = ", ".join(item["missing_capabilities"]) or "—"
        lines.append(f"| {item['case_id']} | {item['readiness']} | {item['score']:.2f} | {missing} |")
    lines.extend([
        "",
        "## 阻断项",
        "",
    ])
    blocker_rows = [
        (item["case_id"], finding)
        for item in report["cases"]
        for finding in item["findings"]
        if finding["severity"] == "BLOCKER"
    ]
    if blocker_rows:
        for case_id, finding in blocker_rows:
            lines.append(f"- `{case_id}` / `{finding['code']}`：{finding['recommendation']}")
    else:
        lines.append("- 无")
    lines.extend([
        "",
        "## 下一道门禁",
        "",
        report["next_gate"],
        "",
    ])
    return "\n".join(lines)
