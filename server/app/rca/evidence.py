"""证据采集层。

从数据库/内存中收集归因所需的结构化证据，不依赖 LLM。
"""

from __future__ import annotations

import json

from server.app.common_utils import status_value
from server.app.rca.models import EvidenceInput


def collect_evidence(
    task_id: str,
    task_record,
    top_functions: list[dict] | None = None,
    ebpf_metrics: dict | None = None,
    sys_metrics: dict | None = None,
    suggestions: list[str] | None = None,
    failure_events: list[str] | None = None,
    baseline_diff: dict | None = None,
    agent_stats: dict | None = None,
    tool_results: list[dict] | None = None,
) -> EvidenceInput:
    """从各数据源汇总结构化证据。

    Args:
        task_id: 任务 ID。
        task_record: DB/内存中的任务记录（TaskModel 或 TaskRecord）。
        top_functions: Analyzer 产出的 TopN 列表。
        ebpf_metrics: eBPF 采集器产出的 IO 延迟 histogram。
        sys_metrics: SysMetrics 采集器产出的系统多维指标。
        suggestions: 规则引擎产出的建议文本。
        failure_events: 失败原因列表（来自 task_status_events）。
        baseline_diff: 与历史基线的差异。
        agent_stats: Agent 自身资源开销。

    Returns:
        EvidenceInput 实例。
    """
    task_status = task_record.status if task_record else "UNKNOWN"

    return EvidenceInput(
        task_metadata={
            "task_id": task_id,
            "collector_type": getattr(task_record, "collector_type", "unknown") if task_record else "unknown",
            "agent_id": getattr(task_record, "agent_id", None) if task_record else None,
            "target_pid": getattr(task_record, "target_pid", None) if task_record else None,
            "duration_sec": getattr(task_record, "duration_sec", 0) if task_record else 0,
            "sample_rate": getattr(task_record, "sample_rate", 0) if task_record else 0,
            "status": status_value(task_status),
            "status_reason": getattr(task_record, "status_reason", "") if task_record else "",
        },
        top_functions=top_functions or [],
        ebpf_metrics=ebpf_metrics,
        sys_metrics=sys_metrics,
        baseline_diff=baseline_diff,
        agent_stats=agent_stats or {},
        tool_results=tool_results or [],
        suggestions=suggestions or [],
        failure_events=failure_events or [],
    )


def evidence_to_json(evidence: EvidenceInput) -> str:
    """将 EvidenceInput 序列化为 LLM 输入的 JSON 字符串。

    输出字段顺序按重要性排列——越重要越靠后（近因效应）。
    """
    parts: dict = {}

    parts["task_metadata"] = evidence.task_metadata

    if evidence.top_functions:
        parts["top_functions"] = evidence.top_functions[:10]

    if evidence.ebpf_metrics:
        parts["ebpf_metrics"] = evidence.ebpf_metrics

    if evidence.sys_metrics:
        sm = dict(evidence.sys_metrics)
        # 截断 samples 数组避免超过 LLM token 限制
        if "samples" in sm and isinstance(sm["samples"], list) and len(sm["samples"]) > 20:
            sm["samples"] = sm["samples"][:20]
        parts["sys_metrics"] = sm

    if evidence.baseline_diff:
        parts["baseline_diff"] = evidence.baseline_diff

    if evidence.agent_stats:
        parts["agent_stats"] = evidence.agent_stats

    if evidence.tool_results:
        parts["tool_results"] = evidence.tool_results

    if evidence.suggestions:
        parts["suggestions"] = evidence.suggestions[:5]

    if evidence.failure_events:
        parts["failure_events"] = evidence.failure_events[-3:]  # 最近 3 条

    return json.dumps(parts, indent=2, ensure_ascii=False, default=str)
