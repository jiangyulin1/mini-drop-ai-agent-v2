"""当前理解（AI 的 mental model）：程序增量维护，取代假设图作为会话判断核心。

设计见 docs/ai_diagnosis_agent_design.md §4。本模块从诊断会话的
假设图 + 证据集**确定性派生** confirmed / contradictions / missing（按证据域），
并给出 `next`（下一步该补哪类证据 → 建议采集器）。AI 只负责解释和扩展，
决策字段始终来自结构化数据，不依赖模型自由文本。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from server.app.diagnosis.probe_registry import fallback_candidates_for_gap
from server.app.diagnosis.schemas import StrictModel

# 证据域 → 建议采集器（确定性骨，AI 只在候选内选）
DOMAIN_TO_COLLECTOR = {
    "host": "sys_metrics",
    "process": "perf_cpu",
    "container": "sys_metrics",
    "dependency": "network_metrics",
    "database": "mysql_lock",
    "runtime": "go_pprof",
    "network": "network_metrics",
}

COLLECTOR_HINT = {
    "sys_metrics": "宿主/进程级系统指标",
    "perf_cpu": "CPU 热点与调用栈",
    "mysql_lock": "数据库锁等待",
    "go_pprof": "运行时/堆 Profile",
    "network_metrics": "网络延迟与重传",
}


class CurrentUnderstanding(StrictModel):
    target: str = ""
    symptom: str = ""
    understanding: str = "不可判断"
    confirmed: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    missing_domains: list[str] = Field(default_factory=list)
    candidate_gap_proposals: list[dict[str, Any]] = Field(default_factory=list)
    next: str = ""
    source: str = "programmatic"
    updated_at: str = ""


def derive_current_understanding(
    *,
    target: str = "",
    symptom: str = "",
    hypotheses: Optional[list[dict[str, Any]]] = None,
    evidence: Optional[list[dict[str, Any]]] = None,
    utcnow_str: Optional[str] = None,
) -> CurrentUnderstanding:
    """从假设图 + 证据集派生当前理解。

    - understanding：当前最可信活跃假设的 statement（无则 OTHER_UNKNOWN）；
    - confirmed：活跃/已确认假设的支持证据摘要（引用 evidence_refs）；
    - contradictions：活跃假设的反证；
    - missing：缺口（按证据域聚合去重）；
    - next：按缺失证据域建议下一步采集器。
    """
    hypotheses = hypotheses or []
    evidence = evidence or []
    evidence_by_id = {item.get("evidence_id"): item for item in evidence}

    active = [
        item for item in hypotheses
        if item.get("status", "ACTIVE") not in {"RULED_OUT", "WEAKENED"}
    ]
    top = None
    for item in sorted(
        active, key=lambda h: _score(h), reverse=True,
    ):
        top = item
        break

    confirmed: list[str] = []
    contradictions: list[str] = []
    missing: list[str] = []
    missing_domains: list[str] = []

    if top:
        for ref in (top.get("supporting_evidence_refs") or [])[:8]:
            ev = evidence_by_id.get(ref)
            if ev is None:
                missing_item = f"支持证据引用不可用: {ref}"
                if missing_item not in missing:
                    missing.append(missing_item)
                continue
            domains = ", ".join(ev.get("data_quality", {}).get("domains") or [])
            source = ev.get("source_type") or "evidence"
            confirmed.append(_evidence_summary(ev, ref, domains, source))
        for ref in (top.get("contradicting_evidence_refs") or [])[:8]:
            ev = evidence_by_id.get(ref)
            contradictions.append(_evidence_summary(
                ev, ref,
                ", ".join(ev.get("data_quality", {}).get("domains") or []) if ev else "",
                ev.get("source_type") if ev else "evidence",
            ))
        for miss in (top.get("missing_evidence") or [])[:10]:
            if isinstance(miss, dict):
                description = str(miss.get("description", ""))
                domains = miss.get("domains") or _domain_from_description(description)
            else:
                description = str(miss)
                domains = _domain_from_description(description)
            if description and description not in missing:
                missing.append(description)
            for domain in domains or []:
                if domain not in missing_domains:
                    missing_domains.append(domain)

    understanding = (
        top.get("statement") if top else "OTHER_UNKNOWN：尚无活跃候选解释"
    )
    next_action = _derive_next(missing_domains, understanding)

    return CurrentUnderstanding(
        target=target,
        symptom=symptom,
        understanding=understanding,
        confirmed=confirmed,
        contradictions=contradictions,
        missing=missing,
        missing_domains=missing_domains,
        candidate_gap_proposals=fallback_candidates_for_gap(missing_domains),
        next=next_action,
        source="programmatic",
        updated_at=utcnow_str or "",
    )


def _score(hypothesis: dict[str, Any]) -> float:
    """活跃候选排序：CONFIRMED > ACTIVE，支持证据多者优先。"""
    status = hypothesis.get("status", "ACTIVE")
    base = {"CONFIRMED": 2, "ACTIVE": 1, "PROPOSED": 0.5}.get(status, 1)
    return base + 0.05 * len(hypothesis.get("supporting_evidence_refs") or [])


def _evidence_summary(ev, ref, domains: str, source: str) -> str:
    if ev is None:
        return f"{ref}（证据缺失）"
    observed = (ev.get("observed_value") or {})
    probe = ev.get("query_or_probe") or source
    if isinstance(observed, dict) and observed:
        preview = ", ".join(f"{k}={v}" for k, v in list(observed.items())[:3])
        detail = f" [{preview}]"
    else:
        detail = ""
    suffix = f"（域: {domains}）" if domains else ""
    return f"{probe}: {ref}{detail}{suffix}"


def _domain_from_description(description: str) -> list[str]:
    lowered = description.lower()
    mapping = {
        "host": ("host", "宿主", "系统", "load", "cpu"),
        "process": ("process", "进程", "cpu", "profile", "热点"),
        "database": ("database", "db", "锁", "查询", "mysql"),
        "network": ("network", "网络", "延迟", "重传", "连接"),
        "runtime": ("runtime", "gc", "堆", "内存", "goroutine"),
        "container": ("container", "容器", "cgroup"),
    }
    result = []
    for domain, keywords in mapping.items():
        if any(keyword in lowered for keyword in keywords):
            if domain not in result:
                result.append(domain)
    return result


def _derive_next(missing_domains: list[str], understanding: str) -> str:
    if not missing_domains:
        if understanding and "不可判断" not in understanding:
            return "证据覆盖充分，可收敛根因结论。"
        return "证据不足，建议补充采集或请求用户上下文。"
    domain = missing_domains[0]
    collector = DOMAIN_TO_COLLECTOR.get(domain)
    if not collector:
        return f"缺 {domain} 域证据，建议向用户确认下一步方向（候选缺失，走审批）。"
    hint = COLLECTOR_HINT.get(collector, collector)
    return f"缺 {domain} 域证据 → 建议 {collector}（{hint}）；候选缺失时从白名单内提案并走审批。"
