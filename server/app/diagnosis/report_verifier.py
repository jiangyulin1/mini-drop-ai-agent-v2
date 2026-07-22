"""诊断报告的确定性引用、动作和安全策略校验。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import shlex
from typing import Any

from pydantic import ValidationError

from server.app.diagnosis.knowledge import knowledge_ids
from server.app.diagnosis.actions import render_action_argv
from server.app.diagnosis.domain_analyzers import ANALYZER_CONTRACTS
from server.app.diagnosis.probe_registry import list_probes
from server.app.diagnosis.schemas import (
    DiagnosisAction, DiagnosisReport, DomainCause, DomainFinding, ReportVerification, RootLocation,
)


LEGACY_ACTION_FIELDS = {"command_id", "command", "confidence"}


def verify_report(
    conclusion: dict[str, Any],
    evidence: list[dict[str, Any]],
    target_scope: dict[str, Any],
    diagnosis_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    valid_evidence = set(evidence_by_id)
    valid_knowledge = knowledge_ids()
    evidence_refs = _all_evidence_refs(conclusion)
    unknown_evidence = sorted(evidence_refs - valid_evidence)
    if unknown_evidence:
        issues.append(f"未知 evidence_refs: {unknown_evidence}")

    report_knowledge = set(conclusion.get("knowledge_refs", []))
    for item in conclusion.get("knowledge_context", []):
        if item.get("knowledge_id"):
            report_knowledge.add(item["knowledge_id"])
    unknown_knowledge = sorted(report_knowledge - valid_knowledge)
    if unknown_knowledge:
        issues.append(f"未知 knowledge_refs: {unknown_knowledge}")

    for model, key in ((RootLocation, "root_location"), (DomainCause, "domain_cause")):
        if key in conclusion:
            try:
                model.model_validate(conclusion[key])
            except ValidationError as exc:
                issues.append(f"{key} Schema 校验失败: {exc.errors()[0]['msg']}")

    registered_analyzers = set(ANALYZER_CONTRACTS) | {"cluster_assessor.v2"}
    for raw_finding in conclusion.get("findings", []):
        try:
            finding = DomainFinding.model_validate(raw_finding)
        except ValidationError as exc:
            issues.append(f"Finding Schema 校验失败: {exc.errors()[0]['msg']}")
            continue
        if finding.analyzer_id not in registered_analyzers:
            issues.append(f"未注册 Analyzer: {finding.analyzer_id}")
        if finding.severity != "info" and not finding.evidence_refs:
            issues.append(f"Finding 缺少 Evidence: {finding.finding_id}")
        referenced_items = [evidence_by_id[ref] for ref in finding.evidence_refs if ref in evidence_by_id]
        if finding.severity != "info" and referenced_items:
            minimum = ANALYZER_CONTRACTS.get(finding.analyzer_id, {}).get("minimum_quality", "medium")
            if not any(_quality_at_least(item, minimum) for item in referenced_items):
                issues.append(f"Finding 证据质量低于 {minimum}: {finding.finding_id}")
            required_domain = _required_evidence_domain(finding)
            if required_domain and not any(
                required_domain in item.get("data_quality", {}).get("domains", [])
                for item in referenced_items
            ):
                issues.append(f"Finding 缺少 {required_domain} 域 Evidence: {finding.finding_id}")

    allowed_targets = {
        (item.get("agent_id"), item.get("pid"))
        for item in target_scope.get("instances", [])
    }
    for ref in sorted(evidence_refs & valid_evidence):
        item = evidence_by_id[ref]
        target = item.get("target", {})
        if (target.get("agent_id"), target.get("pid")) not in allowed_targets:
            issues.append(f"Evidence 目标不在诊断范围: {ref}")
        if item.get("integrity_hash") and item["integrity_hash"] != evidence_integrity_hash(item):
            issues.append(f"Evidence Hash 校验失败: {ref}")

    for claim_name, refs in _substantive_claims(conclusion):
        if not refs:
            issues.append(f"实质性结论缺少 Evidence: {claim_name}")

    _verify_evidence_time(
        issues,
        conclusion,
        [evidence_by_id[ref] for ref in evidence_refs if ref in evidence_by_id],
        diagnosis_context or {},
    )
    _verify_cross_target_time(issues, conclusion, evidence_by_id, diagnosis_context or {})
    registered_collectors = {item.runner_task_kind for item in list_probes()}
    actions = conclusion.get("actions") or conclusion.get("diagnostic_commands") or []
    validated_actions: list[dict[str, Any]] = []
    for raw in actions:
        payload = {key: value for key, value in raw.items() if key not in LEGACY_ACTION_FIELDS}
        try:
            action = DiagnosisAction.model_validate(payload)
            validated_actions.append(action.model_dump(mode="json"))
        except ValidationError as exc:
            issues.append(f"Action Schema 校验失败 {raw.get('action_id')}: {exc.errors()[0]['msg']}")
            continue
        if "\n" in action.rendered_command or "\r" in action.rendered_command:
            issues.append(f"Action 包含非法换行: {action.action_id}")
        try:
            expected_argv = render_action_argv(action)
            if shlex.split(action.rendered_command) != expected_argv:
                issues.append(f"Action preview 与结构化字段不一致: {action.action_id}")
        except (ValueError, KeyError) as exc:
            issues.append(f"Action 无法重渲染: {action.action_id}: {exc}")
        if action.action_type == "collect":
            if action.collector_type not in registered_collectors:
                issues.append(f"Action 使用未注册采集器: {action.collector_type}")
            if (action.target.agent_id, action.target.pid) not in allowed_targets:
                issues.append(f"Action 目标不在诊断范围: {action.action_id}")
            if not action.parameters.get("api_key_env"):
                issues.append(f"Action 未配置 CLI 认证来源: {action.action_id}")
        if action.auto_execute is not False:
            issues.append(f"Action 禁止自动执行: {action.action_id}")

    report_core = {
        "summary": conclusion.get("summary"),
        "root_location": conclusion.get("root_location"),
        "domain_cause": conclusion.get("domain_cause"),
        "findings": conclusion.get("findings"),
        "actions": validated_actions,
        "knowledge_refs": conclusion.get("knowledge_refs"),
        "limitations": conclusion.get("limitations"),
        "coverage": conclusion.get("coverage"),
    }
    try:
        DiagnosisReport.model_validate(report_core)
    except ValidationError as exc:
        first = exc.errors()[0]
        issues.append(f"DiagnosisReport Schema 校验失败: {'.'.join(map(str, first['loc']))}: {first['msg']}")

    result = ReportVerification(
        status="failed" if issues else "passed",
        checked_evidence_refs=len(evidence_refs),
        checked_knowledge_refs=len(report_knowledge),
        checked_actions=len(actions),
        issues=issues,
    )
    return result.model_dump(mode="json")


def _substantive_claims(conclusion: dict[str, Any]) -> list[tuple[str, list[str]]]:
    claims: list[tuple[str, list[str]]] = []
    assessment = conclusion.get("cluster_assessment", {})
    classification = assessment.get("classification")
    if classification not in {None, "insufficient_evidence", "scope_unresolved"}:
        claims.append((f"cluster_assessment:{classification}", assessment.get("evidence_refs", [])))
    for name in ("root_location", "domain_cause"):
        value = conclusion.get(name, {})
        if value.get("type") not in {None, "unknown"}:
            claims.append((name, value.get("evidence_refs", [])))
    for finding in conclusion.get("findings", []):
        if finding.get("finding_type") != "insufficient_evidence" and finding.get("severity") != "info":
            claims.append((f"finding:{finding.get('finding_id', 'unknown')}", finding.get("evidence_refs", [])))
    for candidate in conclusion.get("root_cause_candidates", []):
        claims.append((f"candidate:{candidate.get('candidate_id', 'unknown')}", candidate.get("evidence_refs", [])))
    return claims


def _verify_evidence_time(
    issues: list[str],
    conclusion: dict[str, Any],
    referenced: list[dict[str, Any]],
    context: dict[str, Any],
) -> None:
    intent = context.get("normalized_intent", {})
    mode = intent.get("diagnosis_mode", "LIVE")
    policy = intent.get("evidence_time_policy", {})
    requested = context.get("requested_time_range", {})
    start = _parse_time(requested.get("start"))
    end = _parse_time(requested.get("end"))
    skew = timedelta(seconds=int(policy.get("max_clock_skew_seconds", 5) or 0))
    require_overlap = bool(policy.get("require_overlap", True))
    for item in referenced:
        role = item.get("evidence_role", "incident")
        ref = item.get("evidence_id", "unknown")
        event_range = item.get("event_time_range", {})
        event_start = _parse_time(event_range.get("start"))
        event_end = _parse_time(event_range.get("end"))
        if role == "reproduction":
            if mode != "REPRODUCTION" or conclusion.get("evidence_scope") != "reproduction":
                issues.append(f"复现 Evidence 不能证明历史根因: {ref}")
            continue
        if role != "incident" or not require_overlap or not all((start, end, event_start, event_end)):
            continue
        if event_end < start - skew or event_start > end + skew:
            issues.append(f"Evidence 时间窗与请求不重叠: {ref}")


def _verify_cross_target_time(
    issues: list[str],
    conclusion: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> None:
    assessment = conclusion.get("cluster_assessment", {})
    if assessment.get("classification") in {None, "insufficient_evidence", "scope_unresolved"}:
        return
    refs = assessment.get("evidence_refs", [])
    ranges = []
    targets = set()
    for ref in refs:
        item = evidence_by_id.get(ref, {})
        event_range = item.get("event_time_range", {})
        start = _parse_time(event_range.get("start"))
        end = _parse_time(event_range.get("end"))
        target = item.get("target", {})
        if start and end:
            ranges.append((start, end))
            targets.add((target.get("agent_id"), target.get("pid")))
    if len(targets) < 2 or len(ranges) < 2:
        return
    policy = context.get("normalized_intent", {}).get("evidence_time_policy", {})
    skew = timedelta(seconds=int(policy.get("max_clock_skew_seconds", 5) or 0))
    if max(start for start, _ in ranges) > min(end for _, end in ranges) + skew:
        issues.append("跨目标 Evidence 时间窗不一致，不能用于横向归因")


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif not value:
        return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _quality_at_least(item: dict[str, Any], minimum: str) -> bool:
    ranks = {"low": 0, "medium": 1, "high": 2}
    actual = str(item.get("data_quality", {}).get("completeness", "low")).lower()
    return ranks.get(actual, 0) >= ranks.get(str(minimum).lower(), 1)


def _required_evidence_domain(finding: DomainFinding) -> str | None:
    declared = finding.facts.get("scope")
    if declared in {"host", "process", "container", "dependency"}:
        return declared
    if finding.category in {"memory", "runtime"}:
        return "process"
    if finding.category == "database":
        return "dependency"
    if finding.category in {"network", "io"}:
        return "host"
    return None


EVIDENCE_HASH_FIELDS = (
    "source_type", "source_system", "evidence_role", "target", "event_time_range",
    "ingestion_time", "query_or_probe", "raw_artifact_ref", "derived_artifact_ref",
    "derivation_version", "observed_value", "baseline_value", "anomaly_score",
    "data_quality", "claim_links",
)


def evidence_integrity_hash(value: dict[str, Any]) -> str:
    canonical = {name: _canonicalize(value.get(name)) for name in EVIDENCE_HASH_FIELDS}
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def _all_evidence_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_refs" and isinstance(item, list):
                refs.update(str(ref) for ref in item)
            else:
                refs.update(_all_evidence_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_all_evidence_refs(item))
    return refs
